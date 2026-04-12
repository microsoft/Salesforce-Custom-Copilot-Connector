"""
External connection lifecycle management.

Functions in this module create, verify, configure, and tear down Microsoft
Graph external connections used by the Salesforce CRM connector.

Key functions
-------------
ensure_connection(config, client, initial_timestamp)
    Idempotently creates the external connection.  If the connection already
    exists it is a no-op; if not, it is created.  Retries on auth errors
    (prompting the operator to grant admin consent) until the configured
    timeout elapses.

is_connection_ready(config, client)
    Returns ``True`` when the connection state is ``ready`` **and** a schema
    has been registered.

set_search_settings(config, client)
    PATCHes the connection's ``searchSettings`` with the adaptive card
    result template defined in ``config/template.json``.  Skipped if search
    settings are already present.

delete_connection / clear_connection_items
    Destructive helpers used during development and reset workflows.
"""

from __future__ import annotations

from urllib.parse import quote
import logging
import time

from azure.core.exceptions import ClientAuthenticationError

from graph.client import GraphApiError, GraphClient, EXTERNAL_CONNECTIONS_PATH
from graph.schema import schema_exists
from salesforce.settings import AppConfig
from salesforce.utils import delay


logger = logging.getLogger("salesforce_connector")
_consent_requested = False


def create_connection(config: AppConfig, client: GraphClient) -> None:
    """Create a new external connection in Microsoft Graph."""
    logger.info("Creating connection %s.", config.connector.id)
    client.post(
        EXTERNAL_CONNECTIONS_PATH,
        json_body={
            "id": config.connector.id,
            "name": config.connector.name,
            "description": config.connector.description,
        },
        headers={"content-type": "application/json"},
    )
    logger.info("Connection %s was created", config.connector.id)


def get_connection(config: AppConfig, client: GraphClient) -> dict:
    """Retrieve the external connection metadata from Microsoft Graph."""
    payload = client.get(f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}")
    return payload if isinstance(payload, dict) else {}


def set_search_settings(config: AppConfig, client: GraphClient) -> None:
    """Patch the connection's search settings with the adaptive card result template."""
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
    logger.info("PATCH %s/%s", EXTERNAL_CONNECTIONS_PATH, config.connector.id)
    client.patch(
        f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}",
        json_body=payload,
        headers={"content-type": "application/json"},
    )


def connection_exists(config: AppConfig, client: GraphClient) -> bool:
    """Return True if the external connection exists, False otherwise."""
    try:
        get_connection(config, client)
        return True
    except GraphApiError as error:
        logger.warning("Can't find the connection %s: %s", config.connector.id, error)
        return False


def ensure_connection(config: AppConfig, client: GraphClient, initial_timestamp: float) -> str | None:
    """Ensure external connection exists. Returns 'created', 'existing', or None on failure."""
    progress = logging.getLogger("progress")
    while time.monotonic() - initial_timestamp <= config.tuning.connection_timeout_seconds:
        try:
            get_connection(config, client)
            logger.info("Connection %s already exists", config.connector.id)
            progress.info("  Connection '%s' verified (existing)", config.connector.id)
            return "existing"
        except Exception as error:  # pragma: no cover - runtime error fan-in
            if isinstance(error, GraphApiError) and error.status_code == 404:
                create_connection(config, client)
                progress.info("  Connection '%s' created", config.connector.id)
                return "created"

            if _is_auth_error(error):
                _request_admin_consent_once(config)
                delay(config.tuning.connection_retry_interval_seconds)
                continue

            logger.exception("Failed to ensure connection %s", config.connector.id)
            return None

    logger.error("Could not create connection %s in under 10 minutes", config.connector.id)
    return None


def is_connection_ready(config: AppConfig, client: GraphClient) -> bool:
    """Return True if the connection state is 'ready' and a schema is registered."""
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
    """Delete all items from the external connection. Returns the number of items deleted."""
    deleted_count = 0
    for item in client.paginate(f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items"):
        item_id = item.get("id")
        if not item_id:
            continue
        logger.info("Deleting item %s from connection %s", item_id, config.connector.id)
        client.delete(f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items/{quote(item_id, safe='')}")
        deleted_count += 1
    return deleted_count


def delete_connection(config: AppConfig, client: GraphClient, initial_timestamp: float) -> bool:
    """Delete the external connection, retrying on auth errors until timeout. Returns True on success."""
    while time.monotonic() - initial_timestamp <= config.tuning.connection_timeout_seconds:
        try:
            connection = get_connection(config, client)
            if connection:
                logger.info("Deleting connection %s", config.connector.id)
                client.delete(f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}")
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
    """Log a one-time warning with the Entra admin consent URL."""
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
    """Return True if the error indicates an authentication or authorization failure."""
    if isinstance(error, ClientAuthenticationError):
        return True

    if isinstance(error, GraphApiError):
        if error.status_code in (401, 403):
            return True
        if error.status_code == -1 and error.code == "AuthenticationRequiredError":
            return True

    return False
