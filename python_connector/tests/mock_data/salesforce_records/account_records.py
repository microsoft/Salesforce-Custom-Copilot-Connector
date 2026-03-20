from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_base_metadata, build_record_id, clamp_limit


def build_account_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("001", index)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Account"},
            "Id": record_id,
            "Name": f"Acme Corporation {index:02d}",
            "Description": f"Enterprise customer account sample {index:02d} for connector tests.",
            "AccountNumber": f"AC-{index:03d}",
            "BillingAddress": {
                "street": f"{index} Main Street",
                "city": "Seattle",
                "state": "WA",
                "postalCode": f"981{index:02d}",
                "country": "United States",
            },
            "ShippingAddress": {
                "street": f"{index} Warehouse Way",
                "city": "Seattle",
                "state": "WA",
                "postalCode": f"981{index:02d}",
                "country": "United States",
            },
            "Industry": "Technology",
            "TickerSymbol": f"ACM{index}",
            "Website": f"https://acme{index:02d}.example.com",
            "Fax": f"(206) 555-01{index:02d}",
            "Phone": f"(206) 555-11{index:02d}",
            "Site": "Headquarters",
            "Type": "Customer - Direct",
            "BillingCity": "Seattle",
            "BillingState": "WA",
            "BillingCountry": "United States",
            "OwnerId": owner_id,
            "Owner": {"Name": "Owner User", "Id": owner_id},
            "objectType": "Account",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_account_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_account_record(index) for index in range(1, clamp_limit(limit) + 1)]