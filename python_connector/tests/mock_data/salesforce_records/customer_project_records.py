from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_base_metadata, build_record_id, clamp_limit


def build_customer_project_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("a01", index)
    account_id = build_record_id("001", index)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Customer_Project__c"},
            "Id": record_id,
            "Name": f"Acme Lighthouse Deployment {index:02d}",
            "Account__c": account_id,
            "Project_description__c": f"Customer project sample {index:02d} used for connector regression coverage.",
            "CreatedById": owner_id,
            "LastModifiedById": owner_id,
            "OwnerId": owner_id,
            "objectType": "Customer_Project__c",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_customer_project_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_customer_project_record(index) for index in range(1, clamp_limit(limit) + 1)]