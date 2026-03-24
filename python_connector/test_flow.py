from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import quote
import json
import logging
import time

from connector.acl import AclResolver
from connector.connection import (
    clear_connection_items,
    delete_connection,
    ensure_connection,
    get_connection,
    is_connection_ready,
    set_search_settings,
)
from connector.graph import GraphApiError, GraphClient
from connector.ingest import ingest_content
from connector.item_upload_log import (
    initialize_item_response_debug_log,
    record_item_get_response,
)
from connector.salesforce import get_all_items_from_api, get_salesforce_access_token
from connector.schema import ensure_schema
from connector.settings import load_config
from connector.transform import SalesforceItemTransformer
from connector.utils import parse_datetime


logger = logging.getLogger("salesforce_connector")
VERIFY_OBJECT_TYPE = "Case"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Salesforce connector smoke test flow.")
    parser.add_argument("--since", help="Optional incremental crawl timestamp in ISO-8601 format.")
    parser.add_argument(
        "--show-items",
        type=int,
        default=5,
        help="Number of ingested items to print after verification.",
    )
    parser.add_argument(
        "--graph-delay-seconds",
        type=int,
        default=5,
        help="Polling delay used for Graph long-running operations.",
    )
    parser.add_argument(
        "--check-acl",
        action="store_true",
        help="Resolve ACLs for the current Salesforce sample before ingestion.",
    )
    parser.add_argument(
        "--clear-first",
        action="store_true",
        help="Delete existing external items before running ingestion.",
    )
    parser.add_argument(
        "--retract-first",
        action="store_true",
        help="Delete the external connection before the smoke test starts.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Stop after connection validation without running ingestion.",
    )
    parser.add_argument(
        "--show-item-json",
        action="store_true",
        help="Print the full JSON payload for the first verified Graph item.",
    )
    parser.add_argument(
        "--trace-connector",
        action="store_true",
        help="Enable the connector's internal INFO logs in addition to the demo step output.",
    )
    return parser.parse_args()


def print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def print_kv(name: str, value: Any) -> None:
    print(f"{name}={value}")


def configure_logging(trace_connector: bool) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    for logger_name in (
        "salesforce_connector",
        "azure",
        "azure.identity",
        "azure.core",
        "azure.core.pipeline.policies.http_logging_policy",
        "urllib3",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    if trace_connector:
        logging.getLogger("salesforce_connector").setLevel(logging.INFO)


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_datetime(value)


def wait_until_ready(
    config: Any,
    client: GraphClient,
    *,
    timeout_seconds: int = 300,
    interval_seconds: int = 5,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_connection_ready(config, client):
            return
        time.sleep(interval_seconds)
    raise TimeoutError(f"Connection {config.connector.id} did not become ready within {timeout_seconds} seconds")


def format_acl_entries(acl_entries: list[dict[str, str]] | None) -> str:
    if not acl_entries:
        return "none"

    return "; ".join(
        f"{entry.get('accessType')}/{entry.get('type')}/{entry.get('value')}"
        for entry in acl_entries
    )


def fetch_raw_items(config: Any, since: datetime | None) -> list[dict[str, Any]]:
    return list(get_all_items_from_api(config, since))


def print_object_breakdown(raw_items: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for item in raw_items:
        object_type = str(item.get("objectType") or "Unknown")
        counts[object_type] += 1

    print_kv("SALESFORCE_ITEM_COUNT", len(raw_items))
    for object_type in sorted(counts):
        print(f"OBJECT_COUNT object={object_type} count={counts[object_type]}")


def summarize_acls(config: Any, client: GraphClient, raw_items: list[dict[str, Any]]) -> None:
    transformer = SalesforceItemTransformer(
        config.connector.salesforce.instance_url,
        config.connector.schema,
    )

    records_by_object_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw_items:
        object_type = item.get("objectType")
        if object_type:
            records_by_object_type[object_type].append(item)

    resolver = AclResolver(config, transformer.handlers, graph_client=client)
    acl_map_by_object = resolver.resolve(dict(records_by_object_type))
    public_acl = [{"accessType": "grant", "type": "everyone", "value": resolver._tenant_id}]
    deny_acl = [{"accessType": "deny", "type": "everyone", "value": resolver._tenant_id}]

    for object_type in sorted(records_by_object_type):
        object_acl_map = acl_map_by_object.get(object_type, {})
        public_count = 0
        deny_count = 0
        user_count = 0
        sample_acl = "none"

        for index, record in enumerate(records_by_object_type[object_type]):
            acl = object_acl_map.get(record["Id"], [])
            if acl == public_acl:
                public_count += 1
            elif acl == deny_acl:
                deny_count += 1
            elif acl:
                user_count += 1
            if index == 0:
                sample_acl = format_acl_entries(acl)

        print(
            f"ACL_SUMMARY object={object_type} public={public_count} deny={deny_count} user_scoped={user_count} sample_acl={sample_acl}"
        )


def collect_items(
    config: Any,
    client: GraphClient,
    raw_items: list[dict[str, Any]],
    show_items: int,
) -> tuple[str, int, list[dict[str, Any]]]:
    samples: list[dict[str, Any]] = []
    case_items = [item for item in raw_items if item.get("objectType") == VERIFY_OBJECT_TYPE]
    items_to_verify = case_items or raw_items
    verify_object_type = VERIFY_OBJECT_TYPE if case_items else "All"

    initialize_item_response_debug_log(config)

    for raw_item in items_to_verify:
        item_id = str(raw_item["Id"])
        object_type = str(raw_item.get("objectType") or "Unknown")
        graph_path = f"/external/connections/{config.connector.id}/items/{quote(item_id, safe='')}"
        try:
            payload = client.get(graph_path)
        except GraphApiError as error:
            record_item_get_response(
                config,
                item_id=item_id,
                object_type=object_type,
                url=raw_item.get("url"),
                graph_path=graph_path,
                error={
                    "statusCode": error.status_code,
                    "code": error.code,
                    "message": str(error),
                    "body": error.body,
                },
            )
            raise

        record_item_get_response(
            config,
            item_id=item_id,
            object_type=object_type,
            url=raw_item.get("url"),
            graph_path=graph_path,
            response_payload=payload if isinstance(payload, dict) else {"rawResponse": payload},
        )

        if not isinstance(payload, dict) or payload.get("id") != item_id:
            raise RuntimeError(f"Failed to verify Graph item {item_id}")
        if len(samples) < show_items:
            samples.append(payload)

    return verify_object_type, len(items_to_verify), samples


def print_item_samples(samples: list[dict[str, Any]], show_item_json: bool) -> None:
    for index, item in enumerate(samples):
        properties = item.get("properties") or {}
        title = properties.get("title") or properties.get("Name") or ""
        object_type = properties.get("objectType") or properties.get("ObjectName") or ""
        acl_summary = format_acl_entries(item.get("acl") or [])
        print(
            f"ITEM_SAMPLE id={item.get('id')} objectType={object_type} title={title!r} acl={acl_summary}"
        )
        if show_item_json and index == 0:
            print("ITEM_JSON_START")
            print(json.dumps(item, indent=2))
            print("ITEM_JSON_END")


def main() -> int:
    args = parse_args()
    since = parse_since(args.since)

    configure_logging(args.trace_connector)

    print_step("Load Config")
    config = load_config()
    print_kv("CONFIG_OK", "true")
    print_kv("CONNECTOR_ID", config.connector.id)
    print_kv("CONNECTOR_NAME", config.connector.name)
    print_kv("SALESFORCE_INSTANCE", config.connector.salesforce.instance_url)
    print_kv("SALESFORCE_API_VERSION", config.connector.salesforce.api_version)
    print_kv("SYNC_MODE", since.isoformat() if since else "full")

    print_step("Salesforce Auth")
    token = get_salesforce_access_token(config)
    print_kv("SALESFORCE_AUTH_OK", "true")
    print_kv("SALESFORCE_TOKEN_LENGTH", len(token))

    print_step("Preview Salesforce Items")
    raw_items = fetch_raw_items(config, since)
    print_object_breakdown(raw_items)

    if not raw_items:
        print("TEST_FLOW_OK")
        return 0

    client = GraphClient(delay_seconds=args.graph_delay_seconds)

    if args.retract_first:
        print_step("Retract Existing Connection")
        if not delete_connection(config, client, time.monotonic()):
            raise RuntimeError(f"Failed to retract connection {config.connector.id}")
        print_kv("RETRACT_OK", "true")

    print_step("Ensure Connection")
    if not ensure_connection(config, client, time.monotonic()):
        raise RuntimeError(f"Failed to ensure connection {config.connector.id}")
    print_kv("CONNECTION_OK", "true")

    print_step("Ensure Schema")
    ensure_schema(config, client)
    print_kv("SCHEMA_OK", "true")
    print_kv("SCHEMA_PROPERTY_COUNT", len(config.connector.schema))

    print_step("Apply Search Settings")
    set_search_settings(config, client)
    connection = get_connection(config, client)
    search_settings = connection.get("searchSettings") or {}
    templates = search_settings.get("searchResultTemplates") or []
    print_kv("SEARCH_SETTINGS_OK", "true")
    print_kv("CONNECTION_STATE", connection.get("state", "unknown"))
    print_kv("SEARCH_TEMPLATE_COUNT", len(templates))

    print_step("Wait For Ready")
    wait_until_ready(config, client)
    connection = get_connection(config, client)
    print_kv("READY_OK", "true")
    print_kv("READY_STATE", connection.get("state", "ready"))

    if args.clear_first:
        print_step("Clear Existing Items")
        deleted_count = clear_connection_items(config, client)
        print_kv("CLEAR_OK", "true")
        print_kv("DELETED_ITEM_COUNT", deleted_count)

    if args.check_acl:
        print_step("Resolve ACL Summary")
        summarize_acls(config, client, raw_items)

    if not args.skip_ingest:
        print_step("Run Ingestion")
        ingest_content(config, client, since=since)
        print_kv("INGEST_OK", "true")
        print_kv("INGEST_ITEM_COUNT", len(raw_items))

    print_step("Verify Items")
    verify_object_type, item_count, samples = collect_items(config, client, raw_items, max(args.show_items, 0))
    print_kv("VERIFY_OBJECT_TYPE", verify_object_type)
    print_kv("ITEM_COUNT", item_count)
    print_item_samples(samples, args.show_item_json)
    print("TEST_FLOW_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())