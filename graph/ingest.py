"""
Item ingestion pipeline — Salesforce → Graph external items.

Orchestrates the full ingestion flow:

1. **Fetch** — queries all Salesforce objects defined in ``config/schema.json``
   via the Salesforce REST API (supports full and incremental sync).
2. **ACL resolution** — for each record, resolves the Salesforce sharing model
   (OWD, roles, groups, territories, sharing rules, parent chains) into
   Microsoft Graph ACL entries.  Two engines are available:
   * *Legacy* (``Graph.acl.AclResolver``) — default.
   * *New* (``acl_engine``) — enabled by setting ``USE_NEW_ACL_ENGINE=true``.
3. **Transform** — converts each Salesforce record + ACLs into a Graph
   ``externalItem`` payload using ``SalesforceItemTransformer``.
4. **Upsert / delete** — PUTs each item into the external connection (or
   DELETEs it if the transformer marks it as ``deleted``).

Debug modes
-----------
``DEBUG_ITEM_ID``
    Set via the ``single-item`` command.  Restricts ingestion to a single
    Salesforce record ID.
``DEBUG_OBJECT_TYPE``
    Set via the ``single-object`` command.  Restricts ingestion to one
    Salesforce object type (e.g. ``Case``, ``Account``).

Key functions
-------------
ingest_content(config, client, since)
    Main entry point.  Called by every command that performs ingestion.
load_content(config, client, item)
    PUTs a single transformed item into the Graph external connection.
delete_content(config, client, item_id)
    DELETEs a single item from the Graph external connection.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote
import json
import logging
import os

from graph.legacy_acl_resolver import AclResolver as LegacyAclResolver
from graph.client import GraphApiError, GraphClient, EXTERNAL_CONNECTIONS_PATH
from salesforce.api_client import get_all_items_from_api
from salesforce.settings import AppConfig
from salesforce.item_transformer import SalesforceItemTransformer


@dataclass
class IngestionStats:
    """Tracks ingestion outcomes for summary reporting."""
    total_fetched: int = 0
    success_count: int = 0
    failed_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    failed_ids: list[str] = field(default_factory=list)
    object_type_counts: dict[str, int] = field(default_factory=dict)
    acl_engine: str = "LEGACY"
    acl_fallback_used: bool = False

logger = logging.getLogger("salesforce_connector")

# Track sample items for detailed logging
_sample_items_logged_by_type = set()


def load_content(config: AppConfig, client: GraphClient, item: dict) -> None:
    item_id = item["id"]
    payload = {key: value for key, value in item.items() if key != "id"}
    url = f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items/{quote(item_id, safe='')}"

    # Log sample item request/response for first item of each object type
    object_type = item.get("properties", {}).get("ObjectName")
    if object_type and object_type not in _sample_items_logged_by_type:
        _sample_items_logged_by_type.add(object_type)
        logger.info("\n" + "=" * 80)
        logger.info("SAMPLE ITEM REQUEST: %s (ID: %s)", object_type, item_id)
        logger.info("=" * 80)
        logger.info("PUT %s", url)
        logger.info("\nRequest Payload:")
        logger.info(json.dumps(payload, indent=2))
    else:
        logger.info("\n" + "=" * 80)
        logger.info("ITEM REQUEST: %s", item_id)
        logger.info("=" * 80)
        logger.info(json.dumps(payload, indent=2))
        logger.info("=" * 80 + "\n")
    
    logger.info("PUT %s", url)

    try:
        response = client.put(url, json_body=payload, headers={"content-type": "application/json"})
        
        # Log response for sample items
        if object_type and object_type in _sample_items_logged_by_type and len(_sample_items_logged_by_type) <= 6:
            logger.info("\nResponse:")
            logger.info(json.dumps(response if response else {"status": "success"}, indent=2))
            logger.info("=" * 80 + "\n")
            
    except GraphApiError as error:
        logger.error("Failed to load %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)


def delete_content(config: AppConfig, client: GraphClient, item_id: str) -> None:
    url = f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items/{quote(item_id, safe='')}"
    logger.info("DELETE %s", url)

    try:
        client.delete(url)
    except GraphApiError as error:
        logger.error("Failed to delete %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)


# ── New ACL engine helper ─────────────────────────────────────────────────────

async def _resolve_acl_new_engine(
    config: AppConfig,
    graph_client: GraphClient,
    records_by_object_type: dict[str, list[dict]],
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """
    Resolve ACLs for all records using the new acl_engine pipeline.

    Flow
    ----
    1. For each record, call AclResolver.resolve_async(object_type, record_id)
       concurrently (asyncio.gather per object type).
    2. Collect the union of all Salesforce User IDs across every record.
    3. PrincipalMapper bulk-fetches user identity fields (FederationIdentifier /
       UserName / Email) in a single SOQL per 100 IDs.
    4. For each record's AclResult, call PrincipalMapper.to_acl_entries() to
       produce the final Graph-API-ready ACL list.

    Returns
    -------
    {object_type: {record_id: [acl_entry, ...]}}  – same shape as the legacy
    AclResolver so the rest of ingest_content is unchanged.
    """
    from acl_engine import AclResolver as NewAclResolver, PrincipalMapper
    from acl_engine.sf_client import SalesforceClient
    from salesforce.api_client import get_salesforce_access_token

    sf_client = SalesforceClient(
        instance_url=config.connector.salesforce.instance_url,
        api_version=config.connector.salesforce.api_version,
        access_token=get_salesforce_access_token(config),
    )
    resolver = NewAclResolver(
        sf_client,
        owd_field_map=config.owd_field_map,
        parent_map=config.parent_map,
    )
    mapper = PrincipalMapper(
        sf_client=sf_client,
        graph_client=graph_client,
    )

    acl_map_by_object: dict[str, dict[str, list[dict[str, str]]]] = {}

    for object_type, records in records_by_object_type.items():
        logger.info("[NewACL] Resolving %d %s record(s)", len(records), object_type)

        # Resolve all records for this object type concurrently
        tasks = [
            resolver.resolve_async(object_type, str(record["Id"]))
            for record in records
            if record.get("Id")
        ]
        acl_results = await asyncio.gather(*tasks, return_exceptions=True)

        object_acl: dict[str, list[dict[str, str]]] = {}
        for record, acl_result in zip(records, acl_results):
            record_id = str(record["Id"])
            if isinstance(acl_result, Exception):
                logger.error(
                    "[NewACL] resolve_async failed for %s/%s: %s – using public ACL",
                    object_type, record_id, acl_result,
                )
                object_acl[record_id] = _public_acl_entry()
                continue

            acl_entries = await mapper.to_acl_entries(acl_result)
            object_acl[record_id] = acl_entries

        acl_map_by_object[object_type] = object_acl
        logger.info("[NewACL] %s: resolved %d ACL(s)", object_type, len(object_acl))

    return acl_map_by_object


def _public_acl_entry() -> list[dict[str, str]]:
    return [{"accessType": "grant", "type": "everyone", "value": os.getenv("AZURE_TENANT_ID") or "everyone"}]


def ingest_content(config: AppConfig, client: GraphClient, since: datetime | None = None) -> IngestionStats:
    """
    Ingest content from Salesforce.

    Args:
        config: Application configuration
        client: Graph API client
        since: Timestamp for incremental sync (None for full sync)

    Returns:
        IngestionStats with success/fail/delete counts and failed item IDs.
    """
    stats = IngestionStats()
    progress = logging.getLogger("progress")
    logger.info("Starting ingestion process...")
    
    if since:
        logger.info("Incremental sync from: %s", since.isoformat())
    else:
        logger.info("Full sync (all items)")

    raw_items = list(get_all_items_from_api(config, since))
    if not raw_items:
        logger.info("No items returned from Salesforce")
        return stats

    # FILTER FOR SPECIFIC OBJECT TYPE (DEBUG MODE)
    DEBUG_OBJECT_TYPE = os.getenv("DEBUG_OBJECT_TYPE")
    if DEBUG_OBJECT_TYPE:
        logger.info("DEBUG MODE: Looking for object type: %s", DEBUG_OBJECT_TYPE)
        filtered_items = [item for item in raw_items if item.get("objectType") == DEBUG_OBJECT_TYPE]
        if filtered_items:
            logger.info("\n" + "!" * 70)
            logger.info("DEBUG MODE: Found and filtering to %d records of type: %s", len(filtered_items), DEBUG_OBJECT_TYPE)
            logger.info("!" * 70 + "\n")
            raw_items = filtered_items
        else:
            logger.warning("\n" + "!" * 70)
            logger.warning("DEBUG object type %s not found in results.", DEBUG_OBJECT_TYPE)
            # Count objects by type
            object_counts = Counter(item.get("objectType") for item in raw_items)
            logger.warning("Available object types in results:")
            for obj_type, count in object_counts.items():
                logger.warning("  - %s: %d records", obj_type, count)
            logger.warning("!" * 70 + "\n")
            # Don't process any items if the specific object type wasn't found
            logger.error("Stopping ingestion - specified object type not found")
            return stats

    # FILTER FOR SPECIFIC ITEM (DEBUG MODE)
    DEBUG_ITEM_ID = os.getenv("DEBUG_ITEM_ID")
    if DEBUG_ITEM_ID:
        logger.info("DEBUG MODE: Looking for item ID: %s", DEBUG_ITEM_ID)
        filtered_items = [item for item in raw_items if item.get("Id") == DEBUG_ITEM_ID]
        if filtered_items:
            logger.info("\n" + "!" * 70)
            logger.info("DEBUG MODE: Found and filtering to only process item: %s", DEBUG_ITEM_ID)
            logger.info("!" * 70 + "\n")
            raw_items = filtered_items
        else:
            logger.warning("\n" + "!" * 70)
            logger.warning("DEBUG item %s not found in results.", DEBUG_ITEM_ID)
            logger.warning("Available IDs in results:")
            for item in raw_items[:10]:  # Show first 10 IDs
                logger.warning("  - %s (%s)", item.get("Id"), item.get("objectType"))
            if len(raw_items) > 10:
                logger.warning("  ... and %d more", len(raw_items) - 10)
            logger.warning("!" * 70 + "\n")
            # Don't process any items if the specific item wasn't found
            logger.error("Stopping ingestion - specified item not found")
            return stats

    # Log Salesforce API response (real data flow only)
    logger.info("\n" + "=" * 70)
    logger.info("SALESFORCE API RESPONSE")
    logger.info("=" * 70)
    logger.info("Total records retrieved: %d", len(raw_items))
    logger.info("=" * 70 + "\n")

    stats.total_fetched = len(raw_items)
    stats.object_type_counts = dict(Counter(item.get("objectType") for item in raw_items))
    progress.info("  Fetched %d records from Salesforce", len(raw_items))

    # Log each raw item from Salesforce API
    for idx, raw_item in enumerate(raw_items, 1):
        logger.info("\n" + "-" * 70)
        logger.info("SALESFORCE ITEM %d/%d", idx, len(raw_items))
        logger.info("-" * 70)
        logger.info("Object Type: %s", raw_item.get("objectType", "Unknown"))
        logger.info("Record ID: %s", raw_item.get("Id", "Unknown"))
        logger.info("\nRaw Salesforce Record:")
        logger.info(json.dumps(raw_item, indent=2))
        logger.info("-" * 70 + "\n")

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
        # Read the flag here (lazily) so dotenv values from load_local_environment() are visible
        _USE_NEW_ACL_ENGINE: bool = (
            os.getenv("USE_NEW_ACL_ENGINE", "false").lower() in ("true", "1", "yes")
        )
        stats.acl_engine = "NEW (acl_engine)" if _USE_NEW_ACL_ENGINE else "LEGACY"
        progress.info("  Resolving ACLs for %d object type(s)...", len(records_by_object_type))
        logger.info(
            "Starting ACL resolution for %d object type(s) using %s engine",
            len(records_by_object_type),
            stats.acl_engine,
        )
        if _USE_NEW_ACL_ENGINE:
            acl_map_by_object = asyncio.run(
                _resolve_acl_new_engine(config, client, dict(records_by_object_type))
            )
        else:
            acl_map_by_object = LegacyAclResolver(
                config,
                transformer.handlers,
                graph_client=client,
            ).resolve(dict(records_by_object_type))
        logger.info(
            "ACL resolution completed. Resolved ACLs for %d object type(s)",
            len(acl_map_by_object),
        )
        progress.info("  ACL resolution complete")
    except Exception as error:  # pragma: no cover - runtime error fan-in
        logger.exception("❌ CRITICAL: Failed to resolve ACLs, falling back to public ACLs: %s", error)
        logger.error("⚠️ ALL ITEMS WILL HAVE PUBLIC ACCESS (everyone) DUE TO ACL FAILURE")
        stats.acl_fallback_used = True

    ingested_count = 0
    for item in raw_items:
        object_type = item.get("objectType", "")
        item_id = item["Id"]
        acl = acl_map_by_object.get(object_type, {}).get(item_id)
        
        # Log ACL retrieval
        if acl is None:
            logger.warning("No ACL found for item %s (%s) - will use fallback ACL", item_id, object_type)
        else:
            logger.info("Retrieved ACL for item %s (%s): %d entries", item_id, object_type, len(acl))
        
        transformed_items = transformer.transform_record(item, acl)

        for transformed_item in transformed_items:
            ingested_count += 1

            if ingested_count % 25 == 0:
                progress.info("  Ingested %d / %d items...", ingested_count, stats.total_fetched)
            elif ingested_count % 10 == 0:
                logger.info("Ingested %s items so far...", ingested_count)

            if transformed_item.get("type") == "deleted":
                try:
                    delete_content(config, client, transformed_item["id"])
                    stats.deleted_count += 1
                except Exception as e:
                    stats.failed_count += 1
                    stats.failed_ids.append(transformed_item["id"])
                    logger.error("Failed to delete item %s: %s", transformed_item["id"], e)
                continue

            try:
                load_content(config, client, transformed_item)
                stats.success_count += 1
            except Exception as e:
                stats.failed_count += 1
                stats.failed_ids.append(transformed_item["id"])
                logger.error("Failed to ingest item %s: %s", transformed_item["id"], e)

    progress.info("  Ingestion complete: %d succeeded, %d failed, %d deleted", stats.success_count, stats.failed_count, stats.deleted_count)
    logger.info("Ingestion complete. Total items ingested: %s", ingested_count)
    return stats

