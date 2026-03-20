from __future__ import annotations

from typing import Any

from ..common import INSTANCE_URL, OWNER_USER_ID, build_account_reference, build_base_metadata, build_record_id, clamp_limit


def build_contact_record(index: int, owner_id: str = OWNER_USER_ID) -> dict[str, Any]:
    record_id = build_record_id("003", index)
    account_reference = build_account_reference(index, owner_id)
    payload = build_base_metadata(index, owner_id)
    payload.update(
        {
            "attributes": {"type": "Contact"},
            "Id": record_id,
            "Name": f"Jordan Contact {index:02d}",
            "FirstName": f"Jordan{index}",
            "LastName": f"Contact{index}",
            "Description": f"Primary account contact sample {index:02d}.",
            "Fax": f"(206) 555-03{index:02d}",
            "Phone": f"(206) 555-13{index:02d}",
            "Account": account_reference,
            "AccountId": account_reference["Id"],
            "Email": f"contact{index:02d}@example.com",
            "Title": "Account Executive",
            "MobilePhone": f"(206) 555-23{index:02d}",
            "AssistantName": f"Assistant {index:02d}",
            "AssistantPhone": f"(206) 555-33{index:02d}",
            "Department": "Sales",
            "HomePhone": f"(206) 555-43{index:02d}",
            "MailingAddress": {
                "street": f"{30 + index} Lake Avenue",
                "city": "Seattle",
                "state": "WA",
                "postalCode": f"981{20 + index:02d}",
                "country": "United States",
            },
            "OtherAddress": {
                "street": f"{90 + index} Other Road",
                "city": "Bellevue",
                "state": "WA",
                "postalCode": f"980{10 + index:02d}",
                "country": "United States",
            },
            "OtherPhone": f"(206) 555-53{index:02d}",
            "MailingCity": "Seattle",
            "MailingState": "WA",
            "MailingCountry": "United States",
            "OwnerId": owner_id,
            "objectType": "Contact",
            "url": f"{INSTANCE_URL}/{record_id}",
        }
    )
    return payload


def get_contact_records(limit: int = 10) -> list[dict[str, Any]]:
    return [build_contact_record(index) for index in range(1, clamp_limit(limit) + 1)]