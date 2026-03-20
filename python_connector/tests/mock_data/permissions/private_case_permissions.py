from __future__ import annotations

from typing import Any

from ..common import OWNER_GUID, OWNER_NAME, OWNER_USER_ID, OWNER_USERNAME, SHARED_GUID, SHARED_NAME, SHARED_USER_ID, SHARED_USERNAME, build_record_id
from ..salesforce_records.case_records import get_case_records
from .principals import build_share, build_user


def build_records_with_shares_response(record_id: str, object_type: str, shares: list[Any]) -> dict[str, Any]:
    share_records = []
    for share in shares:
        share_records.append(
            {
                "Id": share.Id,
                "attributes": {"type": f"{object_type}Share"},
                "UserOrGroupId": share.UserOrGroupId,
                "RowCause": share.RowCause,
                "UserOrGroup": {
                    "attributes": {"type": "Name"},
                    "Type": share.UserOrGroup.Type if share.UserOrGroup else None,
                },
            }
        )
    return {
        "totalSize": 1,
        "done": True,
        "records": [
            {
                "Id": record_id,
                "attributes": {"type": object_type},
                "IsDeleted": False,
                "Shares": {"totalSize": len(share_records), "done": True, "records": share_records},
            }
        ],
    }


def build_private_case_permissions_bundle(record_index: int = 1) -> dict[str, Any]:
    case_record = get_case_records(1)[0] if record_index == 1 else get_case_records(record_index)[record_index - 1]
    shares = [
        build_share(
            share_id=build_record_id("00r", record_index),
            user_or_group_id=SHARED_USER_ID,
        )
    ]
    owner = build_user(
        OWNER_USER_ID,
        name=OWNER_NAME,
        email=OWNER_USERNAME,
        username=OWNER_USERNAME,
    )
    shared = build_user(
        SHARED_USER_ID,
        name=SHARED_NAME,
        email=SHARED_USERNAME,
        username=SHARED_USERNAME,
    )
    return {
        "record": case_record,
        "shares_by_record": {case_record["Id"]: shares},
        "authorized_users_by_object": {"Case": {OWNER_USER_ID: owner, SHARED_USER_ID: shared}},
        "graph_ids_by_identifier": {OWNER_USERNAME: OWNER_GUID, SHARED_USERNAME: SHARED_GUID},
    }