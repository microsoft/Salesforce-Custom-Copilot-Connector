from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from urllib.parse import quote
import logging

from connector.acl import AclResolver
from connector.graph import GraphApiError, GraphClient
from connector.salesforce import get_all_items_from_api
from connector.settings import AppConfig
from connector.transform import SalesforceItemTransformer


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


def delete_content(config: AppConfig, client: GraphClient, item_id: str) -> None:
    url = f"/external/connections/{config.connector.id}/items/{quote(item_id, safe='')}"
    logger.info("DELETE %s", url)

    try:
        client.delete(url)
    except GraphApiError as error:
        logger.error("Failed to delete %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)


def ingest_content(config: AppConfig, client: GraphClient, since: datetime | None = None) -> None:
    logger.info("Starting ingestion process...")
    if since:
        logger.info("Incremental sync from: %s", since.isoformat())
    else:
        logger.info("Full sync (all items)")

    raw_items = list(get_all_items_from_api(config, since))
    if not raw_items:
        logger.info("No items returned from Salesforce")
        return

    transformer = SalesforceItemTransformer(
        config.connector.salesforce.instance_url,
        config.connector.schema,
    )

    records_by_object_type: dict[str, list[dict]] = defaultdict(list)
    for item in raw_items:
        object_type = item.get("objectType")
        if object_type:
            records_by_object_type[object_type].append(item)

    acl_map_by_object: dict[str, dict[str, list[dict[str, str]]]] = {}
    try:
        acl_map_by_object = AclResolver(
            config,
            transformer.handlers,
            graph_client=client,
        ).resolve(dict(records_by_object_type))
    except Exception as error:  # pragma: no cover - runtime error fan-in
        logger.exception("Failed to resolve ACLs, falling back to public ACLs: %s", error)

    ingested_count = 0
    for item in raw_items:
        object_type = item.get("objectType", "")
        acl = acl_map_by_object.get(object_type, {}).get(item["Id"])
        transformed_items = transformer.transform_record(item, acl)

        for transformed_item in transformed_items:
            ingested_count += 1

            if ingested_count % 10 == 0:
                logger.info("Ingested %s items so far...", ingested_count)

            if transformed_item.get("type") == "deleted":
                delete_content(config, client, transformed_item["id"])
                continue

            load_content(config, client, transformed_item)

    logger.info("Ingestion complete. Total items ingested: %s", ingested_count)
