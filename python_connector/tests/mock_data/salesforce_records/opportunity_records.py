from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_account_reference, build_base_metadata, build_record_id, clamp_limit


def build_opportunity_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("006", index)
    account_reference = build_account_reference(index, owner_id)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Opportunity"},
            "Id": record_id,
            "Name": f"Acme Renewal FY26 {index:02d}",
            "Description": f"Renewal opportunity sample {index:02d} for enterprise subscription.",
            "Account": account_reference,
            "AccountId": account_reference["Id"],
            "Amount": 100000.0 + (index * 5000.0),
            "StageName": "Negotiation/Review",
            "CloseDate": f"2026-06-{10 + index:02d}T00:00:00.000+0000",
            "Type": "Existing Customer - Upgrade",
            "Probability": min(50 + index * 5, 95),
            "LeadSource": "Partner Referral",
            "OwnerId": owner_id,
            "objectType": "Opportunity",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_opportunity_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_opportunity_record(index) for index in range(1, clamp_limit(limit) + 1)]