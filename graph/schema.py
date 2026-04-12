"""
Graph connector schema registration.

Manages the external connection schema that defines which properties
(fields) the connector exposes to Microsoft Search.  The schema is read
from ``config/graph-schema.json`` at startup and registered via a PATCH
to ``/external/connections/{id}/schema``.

Key functions
-------------
ensure_schema(config, client)
    Idempotently registers the schema.  If a schema already exists the call
    is a no-op; if not, the function waits briefly and then creates it.
    Retries on transient errors using the configured retry interval.

schema_exists(config, client)
    Returns ``True`` if the connection already has a registered schema.

create_schema(config, client)
    Issues the PATCH request to create / update the schema.  This is a
    long-running Graph operation; ``GraphClient`` follows the ``Location``
    header automatically.
"""

from __future__ import annotations

import logging

from graph.client import GraphApiError, GraphClient, EXTERNAL_CONNECTIONS_PATH
from salesforce.settings import AppConfig
from salesforce.utils import delay


logger = logging.getLogger("salesforce_connector")


def create_schema(config: AppConfig, client: GraphClient) -> None:
    """Register the external connection schema via a PATCH request (long-running operation)."""
    logger.info(
        "Creating schema for connection %s. This should take under 10 minutes...",
        config.connector.id,
    )
    client.patch(
        f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/schema",
        json_body={
            "baseType": "microsoft.graph.externalItem",
            "properties": config.connector.schema,
        },
        headers={"content-type": "application/json"},
    )
    logger.info("Schema for connection %s was created", config.connector.id)


def get_schema(config: AppConfig, client: GraphClient) -> dict:
    """Retrieve the current schema for the external connection."""
    payload = client.get(f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/schema")
    return payload if isinstance(payload, dict) else {}


def schema_exists(config: AppConfig, client: GraphClient) -> bool:
    """Return True if a schema is registered for the external connection."""
    try:
        get_schema(config, client)
        return True
    except GraphApiError as error:
        logger.warning("Can't find the schema for connection %s: %s", config.connector.id, error)
        return False


def ensure_schema(config: AppConfig, client: GraphClient) -> None:
    """Idempotently register the schema, creating it if not found."""
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
