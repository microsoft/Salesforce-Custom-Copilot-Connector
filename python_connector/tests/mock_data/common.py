from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json


INSTANCE_URL = "https://mock-org.example.salesforce.com"
API_VERSION = "v48.0"
DEFAULT_RECORD_LIMIT = 10
TENANT_ID = "11111111-2222-3333-4444-555555555555"
OWNER_USER_ID = "005000000000001AAA"
SHARED_USER_ID = "005000000000002AAA"
OWNER_GUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SHARED_GUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OWNER_USERNAME = "owner.user@example.com"
SHARED_USERNAME = "shared.user@example.com"
OWNER_NAME = "Owner User"
SHARED_NAME = "Shared User"
PUBLIC_GROUP_ID = "00G000000000001AAA"
ROLE_ID = "00E000000000001AAA"


_GRAPH_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "connector" / "references" / "graph-schema.json"


def load_graph_schema() -> list[dict[str, Any]]:
    return json.loads(_GRAPH_SCHEMA_PATH.read_text(encoding="utf-8"))


def clamp_limit(limit: int = DEFAULT_RECORD_LIMIT) -> int:
    return max(1, min(limit, DEFAULT_RECORD_LIMIT))


def build_record_id(prefix: str, index: int) -> str:
    return f"{prefix}{index:012d}AAA"


def build_base_metadata(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    day = 19 + ((index - 1) % DEFAULT_RECORD_LIMIT)
    return {
        "IsDeleted": False,
        "OwnerId": owner_id,
        "Owner": {"Name": OWNER_NAME, "Id": owner_id},
        "CreatedById": owner_id,
        "CreatedBy": {"Name": OWNER_NAME, "Id": owner_id},
        "CreatedDate": f"2026-03-{day:02d}T08:00:00.000+0000",
        "LastModifiedById": owner_id,
        "LastModifiedBy": {"Name": OWNER_NAME, "Id": owner_id},
        "LastModifiedDate": f"2026-03-{day:02d}T09:00:00.000+0000",
    }


def build_account_reference(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    account_id = build_record_id("001", index)
    return {
        "Id": account_id,
        "Name": f"Acme Corporation {index:02d}",
        "OwnerId": owner_id,
        "Owner": {"Name": OWNER_NAME, "Id": owner_id},
    }


def build_acl_map(records: list[dict[str, Any]], acl_entries: list[dict[str, str]]) -> dict[str, dict[str, list[dict[str, str]]]]:
    acl_map: dict[str, dict[str, list[dict[str, str]]]] = {}
    for record in records:
        object_type = str(record["objectType"])
        acl_map.setdefault(object_type, {})[str(record["Id"])] = deepcopy(acl_entries)
    return acl_map