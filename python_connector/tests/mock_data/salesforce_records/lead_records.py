from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_account_reference, build_base_metadata, build_record_id, clamp_limit


def build_lead_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("00Q", index)
    account_reference = build_account_reference(index, owner_id)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Lead"},
            "Id": record_id,
            "Name": f"Taylor Lead {index:02d}",
            "FirstName": f"Taylor{index}",
            "LastName": f"Lead{index}",
            "Description": f"Lead sample {index:02d} created from website inquiry.",
            "Fax": f"(206) 555-02{index:02d}",
            "Phone": f"(206) 555-12{index:02d}",
            "ConvertedAccount": account_reference,
            "ConvertedAccountId": account_reference["Id"],
            "Email": f"lead{index:02d}@example.com",
            "Title": "Director of Procurement",
            "MobilePhone": f"(206) 555-22{index:02d}",
            "Address": {
                "street": f"{10 + index} Pine Street",
                "city": "Seattle",
                "state": "WA",
                "postalCode": f"981{10 + index:02d}",
                "country": "United States",
            },
            "Company": account_reference["Name"],
            "IsConverted": False,
            "Status": "Open - Not Contacted",
            "LeadSource": "Web",
            "City": "Seattle",
            "State": "WA",
            "Country": "United States",
            "OwnerId": owner_id,
            "objectType": "Lead",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_lead_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_lead_record(index) for index in range(1, clamp_limit(limit) + 1)]