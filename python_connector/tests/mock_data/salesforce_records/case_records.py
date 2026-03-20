from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_base_metadata, build_record_id, clamp_limit


def build_case_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("500", index)
    account_id = build_record_id("001", index)
    contact_id = build_record_id("003", index)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Case"},
            "Id": record_id,
            "AccountId": account_id,
            "CaseNumber": f"{1000 + index:08d}",
            "ClosedDate": f"2026-03-{20 + ((index - 1) % 10):02d}T12:00:00.000+0000",
            "ContactEmail": f"case.contact{index:02d}@example.com",
            "ContactMobile": f"(206) 555-24{index:02d}",
            "ContactPhone": f"(206) 555-14{index:02d}",
            "Description": f"Support case sample {index:02d} for connector testing.",
            "IsClosed": False,
            "Priority": ["High", "Medium"],
            "Reason": "Installation",
            "Status": "New",
            "Subject": f"Connector test case {index:02d}",
            "Type": "Problem",
            "Origin": "Web",
            "ContactId": contact_id,
            "OwnerId": owner_id,
            "objectType": "Case",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_case_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_case_record(index) for index in range(1, clamp_limit(limit) + 1)]