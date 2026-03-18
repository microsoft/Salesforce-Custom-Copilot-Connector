from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
import logging

from connector.graph import GraphApiError, GraphClient
from connector.salesforce import get_all_items_from_api
from connector.settings import AppConfig
from connector.transform import get_external_item_from_item


logger = logging.getLogger("salesforce_connector")


def load_content(config: AppConfig, client: GraphClient, item: dict) -> None:
    item_id = item["id"]
    payload = {key: value for key, value in item.items() if key != "id"}
    url = f"/external/connections/{config.connector.id}/items/{quote(item_id, safe='')}"

    logger.info("PUT %s", url)

    try:
        client.put(url, json_body=payload, headers={"content-type": "application/json"})
    except GraphApiError as error:
        logger.error("Failed to load %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)


def ingest_content(config: AppConfig, client: GraphClient, since: datetime | None = None) -> None:
    logger.info("Starting ingestion process...")
    if since:
        logger.info("Incremental sync from: %s", since.isoformat())
    else:
        logger.info("Full sync (all items)")

    ingested_count = 0

    for item in get_all_items_from_api(config, since):
        transformed_item = get_external_item_from_item(item)
        ingested_count += 1

        if ingested_count % 10 == 0:
            logger.info("Ingested %s items so far...", ingested_count)

        load_content(config, client, transformed_item)

    logger.info("Ingestion complete. Total items ingested: %s", ingested_count)
