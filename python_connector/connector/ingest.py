from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from urllib.parse import quote
import logging

from connector.acl import AclResolver
from connector.graph import GraphApiError, GraphClient
from connector.item_upload_log import (
    initialize_item_request_debug_log,
    initialize_item_upload_log,
    record_item_put_request,
    record_uploaded_item,
)
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

    # Log sample item request/response for first item of each object type
    object_type = item.get("properties", {}).get("objectType")
    if object_type and object_type not in _sample_items_logged_by_type:
        _sample_items_logged_by_type.add(object_type)
        import json
        logger.info("\n" + "=" * 80)
        logger.info("SAMPLE ITEM REQUEST: %s (ID: %s)", object_type, item_id)
        logger.info("=" * 80)
        logger.info("PUT %s", url)
        logger.info("\nRequest Payload:")
        logger.info(json.dumps(payload, indent=2))
    
    # For real data flow (not mock), log every item request payload
    elif not config.use_mock_data:
        import json
        logger.info("\n" + "=" * 80)
        logger.info("ITEM REQUEST: %s", item_id)
        logger.info("=" * 80)
        logger.info(json.dumps(payload, indent=2))
        logger.info("=" * 80 + "\n")
    
    logger.info("PUT %s", url)

    record_item_put_request(
        config,
        item_id=item_id,
        object_type=object_type,
        url=payload.get("properties", {}).get("url"),
        graph_path=url,
        request_payload=payload,
    )

    try:
        response = client.put(url, json_body=payload, headers={"content-type": "application/json"})
        record_uploaded_item(
            config,
            item_id=item_id,
            object_type=object_type,
            graph_path=url,
            url=payload.get("properties", {}).get("url"),
        )
        
        # Log response for sample items
        if object_type and object_type in _sample_items_logged_by_type and len(_sample_items_logged_by_type) <= 6:
            import json
            logger.info("\nResponse:")
            logger.info(json.dumps(response if response else {"status": "success"}, indent=2))
            logger.info("=" * 80 + "\n")
            
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
    initialize_item_request_debug_log(config)
    initialize_item_upload_log(config)
    
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

    # Log Salesforce API response (real data flow only)
    logger.info("\n" + "=" * 70)
    logger.info("SALESFORCE API RESPONSE")
    logger.info("=" * 70)
    logger.info("Total records retrieved: %d", len(raw_items))
    logger.info("\nFirst record (sample):")
    import json
    logger.info(json.dumps(raw_items[0] if raw_items else {}, indent=2))
    logger.info("=" * 70 + "\n")

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
    object_types = ["Account", "Contact", "Lead", "Opportunity", "Case", "Customer_Project__c"]

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
