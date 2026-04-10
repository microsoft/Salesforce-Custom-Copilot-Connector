from __future__ import annotations

import logging

from connector.graph import GraphApiError, GraphClient
from connector.settings import AppConfig
from connector.utils import delay


logger = logging.getLogger("salesforce_connector")


def create_schema(config: AppConfig, client: GraphClient) -> None:
    logger.info(
        "Creating schema for connection %s. This should take under 10 minutes...",
        config.connector.id,
    )
    client.patch(
        f"/external/connections/{config.connector.id}/schema",
        json_body={
            "baseType": "microsoft.graph.externalItem",
            "properties": config.connector.schema,
        },
        headers={"content-type": "application/json"},
    )
    logger.info("Schema for connection %s was created", config.connector.id)


def get_schema(config: AppConfig, client: GraphClient) -> dict:
    payload = client.get(f"/external/connections/{config.connector.id}/schema")
    return payload if isinstance(payload, dict) else {}


def schema_exists(config: AppConfig, client: GraphClient) -> bool:
    try:
        get_schema(config, client)
        return True
    except GraphApiError as error:
        logger.warning("Can't find the schema for connection %s: %s", config.connector.id, error)
        return False


def ensure_schema(config: AppConfig, client: GraphClient) -> None:
    while True:
        try:
            get_schema(config, client)
            return
        except GraphApiError as error:
            if error.status_code == 404:
                logger.info("Schema not found. Waiting 5 seconds before creating...")
                delay(5)
                create_schema(config, client)
                return

            logger.warning("Schema check failed for %s. Retrying...", config.connector.id)
            delay(config.tuning.schema_retry_interval_seconds)
