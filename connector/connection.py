from __future__ import annotations

from urllib.parse import quote
import logging
import time

from azure.core.exceptions import ClientAuthenticationError

from connector.graph import GraphApiError, GraphClient
from connector.schema import schema_exists
from connector.settings import AppConfig
from connector.utils import delay


logger = logging.getLogger("salesforce_connector")
_consent_requested = False


def create_connection(config: AppConfig, client: GraphClient) -> None:
    logger.info("Creating connection %s.", config.connector.id)
    client.post(
        "/external/connections",
        json_body={
            "id": config.connector.id,
            "name": config.connector.name,
            "description": config.connector.description,
        },
        headers={"content-type": "application/json"},
    )
    logger.info("Connection %s was created", config.connector.id)


def get_connection(config: AppConfig, client: GraphClient) -> dict:
    payload = client.get(f"/external/connections/{config.connector.id}")
    return payload if isinstance(payload, dict) else {}


def set_search_settings(config: AppConfig, client: GraphClient) -> None:
    connection = get_connection(config, client)
    if connection.get("searchSettings"):
        return

    payload = {
        "searchSettings": {
            "searchResultTemplates": [
                {
                    "id": "display",
                    "layout": config.connector.template,
                    "priority": 1,
                }
            ]
        }
    }
    logger.info("PATCH /external/connections/%s", config.connector.id)
    client.patch(
        f"/external/connections/{config.connector.id}",
        json_body=payload,
        headers={"content-type": "application/json"},
    )


def connection_exists(config: AppConfig, client: GraphClient) -> bool:
    try:
        get_connection(config, client)
        return True
    except GraphApiError as error:
        logger.warning("Can't find the connection %s: %s", config.connector.id, error)
        return False


def ensure_connection(config: AppConfig, client: GraphClient, initial_timestamp: float) -> bool:
    while time.monotonic() - initial_timestamp <= config.tuning.connection_timeout_seconds:
        try:
            get_connection(config, client)
            logger.info("Connection %s already exists", config.connector.id)
            return True
        except Exception as error:  # pragma: no cover - runtime error fan-in
            if isinstance(error, GraphApiError) and error.status_code == 404:
                create_connection(config, client)
                return True

            if _is_auth_error(error):
                _request_admin_consent_once(config)
                delay(config.tuning.connection_retry_interval_seconds)
                continue

            logger.exception("Failed to ensure connection %s", config.connector.id)
            return False

    logger.error("Could not create connection %s in under 10 minutes", config.connector.id)
    return False


def is_connection_ready(config: AppConfig, client: GraphClient) -> bool:
    try:
        connection = get_connection(config, client)
        connection_state = connection.get("state")
        if connection_state and connection_state != "ready":
            logger.info("Connection %s is not ready", config.connector.id)
            return False

        logger.info("Connection %s is ready", config.connector.id)

        if not schema_exists(config, client):
            logger.info("Schema is not deployed")
            return False

        return True
    except Exception as error:  # pragma: no cover - runtime error fan-in
        logger.exception("Error checking connection %s: %s", config.connector.id, error)
        return False


def clear_connection_items(config: AppConfig, client: GraphClient) -> int:
    deleted_count = 0
    for item in client.paginate(f"/external/connections/{config.connector.id}/items"):
        item_id = item.get("id")
        if not item_id:
            continue
        logger.info("Deleting item %s from connection %s", item_id, config.connector.id)
        client.delete(f"/external/connections/{config.connector.id}/items/{quote(item_id, safe='')}")
        deleted_count += 1
    return deleted_count


def delete_connection(config: AppConfig, client: GraphClient, initial_timestamp: float) -> bool:
    while time.monotonic() - initial_timestamp <= config.tuning.connection_timeout_seconds:
        try:
            connection = get_connection(config, client)
            if connection:
                logger.info("Deleting connection %s", config.connector.id)
                client.delete(f"/external/connections/{config.connector.id}")
                logger.info("Connection %s was deleted", config.connector.id)
                return True
            return False
        except Exception as error:  # pragma: no cover - runtime error fan-in
            if isinstance(error, GraphApiError) and error.status_code == 404:
                logger.warning("Connection %s does not exist", config.connector.id)
                return True

            if _is_auth_error(error):
                _request_admin_consent_once(config)
                delay(config.tuning.connection_retry_interval_seconds)
                continue

            logger.exception("Failed to delete connection %s", config.connector.id)
            return False

    logger.error("Could not delete connection %s in under 10 minutes", config.connector.id)
    return False


def _request_admin_consent_once(config: AppConfig) -> None:
    global _consent_requested

    if _consent_requested:
        return

    logger.warning(
        "You need to grant tenant-wide admin consent to the application in Entra ID. "
        "Use this link to provide the consent: "
        "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/%s/isMSAApp~/false",
        config.client_id,
    )
    _consent_requested = True


def _is_auth_error(error: Exception) -> bool:
    if isinstance(error, ClientAuthenticationError):
        return True

    if isinstance(error, GraphApiError):
        if error.status_code in (401, 403):
            return True
        if error.status_code == -1 and error.code == "AuthenticationRequiredError":
            return True

    return False
