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

# Track sample items for detailed logging
_sample_items_logged_by_type = set()


def load_content(config: AppConfig, client: GraphClient, item: dict) -> None:
    item_id = item["id"]
    payload = {key: value for key, value in item.items() if key != "id"}
    url = f"/external/connections/{config.connector.id}/items/{quote(item_id, safe='')}"

    # Log sample item details for first item of each object type
    object_type = item.get("properties", {}).get("objectType")
    if object_type and object_type not in _sample_items_logged_by_type:
        _sample_items_logged_by_type.add(object_type)
        logger.info("\n" + "=" * 70)
        logger.info("SAMPLE ITEM: %s", object_type)
        logger.info("=" * 70)
        logger.info("Request URL: PUT %s", url)
        logger.info("Item ID: %s", item_id)
        logger.info("\nACL Details:")
        import json
        logger.info("%s", json.dumps(payload.get("acl", []), indent=2))
        logger.info("\nContent Type: %s", payload.get("content", {}).get("type"))
        content_value = payload.get("content", {}).get("value", "")
        logger.info("Content Preview (first 300 chars):\n%s...", content_value[:300])
        logger.info("\nProperties (%d total):", len(payload.get("properties", {})))
        for key, value in list(payload.get("properties", {}).items())[:15]:
            value_str = str(value)[:100]
            logger.info("  %s: %s", key, value_str)
        if len(payload.get("properties", {})) > 15:
            logger.info("  ... and %d more properties", len(payload.get("properties", {})) - 15)
        logger.info("=" * 70 + "\n")
    
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
    """
    Ingest content from Salesforce (or mock data if enabled).
    
    Args:
        config: Application configuration
        client: Graph API client
        since: Timestamp for incremental sync (None for full sync)
    """
    logger.info("Starting ingestion process...")
    logger.info("Mock data mode: %s", "ENABLED" if config.use_mock_data else "DISABLED")
    
    if since:
        logger.info("Incremental sync from: %s", since.isoformat())
    else:
        logger.info("Full sync (all items)")

    # Use mock data if enabled
    if config.use_mock_data:
        _ingest_mock_content(config, client)
        return
    
    # Original real Salesforce API flow
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


def _ingest_mock_content(config: AppConfig, client: GraphClient) -> None:
    """
    Ingest content using mock data (for testing without live Salesforce).
    """
    try:
        import sys
        from pathlib import Path

        tests_dir = Path(__file__).parent.parent / "tests"
        if str(tests_dir) not in sys.path:
            sys.path.insert(0, str(tests_dir))

        import mock_salesforce_client

        MockSalesforceClient = mock_salesforce_client.MockSalesforceClient

    except ImportError as e:
        logger.error("Failed to import mock data modules: %s", e)
        logger.exception("Full error:")
        return

    logger.info("Using MOCK DATA for ingestion")

    sf_client = MockSalesforceClient()
    object_types = ["Account", "Contact", "Lead", "Opportunity"]

    raw_items: list[dict] = []
    for object_type in object_types:
        logger.info("Processing mock data for: %s", object_type)
        query_result = sf_client.get_records(object_type, limit=5)
        records = query_result.get("records", [])
        if not records:
            logger.info("No mock records for %s", object_type)
            continue
        for record in records:
            record.setdefault("objectType", object_type)
        raw_items.extend(records)
        logger.info("Retrieved %s mock %s records", len(records), object_type)

    if not raw_items:
        logger.info("No mock items returned")
        return

    transformer = SalesforceItemTransformer(
        config.connector.salesforce.instance_url,
        config.connector.schema,
    )

    ingested_count = 0
    for item in raw_items:
        transformed_items = transformer.transform_record(item)

        for transformed_item in transformed_items:
            ingested_count += 1

            if ingested_count % 10 == 0:
                logger.info("Ingested %s items so far...", ingested_count)

            if transformed_item.get("type") == "deleted":
                delete_content(config, client, transformed_item["id"])
                continue

            load_content(config, client, transformed_item)

    logger.info("Mock data ingestion complete. Total items ingested: %s", ingested_count)
