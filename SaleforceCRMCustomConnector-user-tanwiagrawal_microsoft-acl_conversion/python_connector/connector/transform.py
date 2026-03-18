from __future__ import annotations

from typing import Any
import json


FIELD_NAME_MAP = {
    "Account__c": "AccountC",
    "Project_description__c": "ProjectDescriptionC",
    "Title": "JobTitle",
}

EXCLUDED_KEYS = {"Id", "objectType", "url", "attributes"}


def get_acl_from_item(item: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "accessType": "grant",
            "type": "everyone",
            "value": "everyone",
        }
    ]


def get_item_title(item: dict[str, Any]) -> str:
    object_type = item.get("objectType")
    if object_type == "Account":
        return item.get("Name") or item["Id"]
    if object_type in {"Lead", "Contact"}:
        full_name = f"{item.get('FirstName', '')} {item.get('LastName', '')}".strip()
        return full_name or item["Id"]
    if object_type == "Opportunity":
        return item.get("Name") or item["Id"]
    if object_type == "Case":
        return item.get("Subject") or f"Case {item.get('CaseNumber') or item['Id']}"
    if object_type == "Customer_Project__c":
        return item.get("Name") or f"Customer Project {item['Id']}"
    return item.get("Name") or item["Id"]


def get_item_content(item: dict[str, Any]) -> str:
    object_type = item.get("objectType")
    if object_type == "Account":
        return f"{item.get('Name', '')} - {item.get('Type', '')} - {item.get('Industry', '')} - {item.get('BillingCity', '')}".strip()
    if object_type == "Lead":
        return f"{item.get('FirstName', '')} {item.get('LastName', '')} - {item.get('Company', '')} - {item.get('Title', '')} - {item.get('Email', '')}".strip()
    if object_type == "Contact":
        return f"{item.get('FirstName', '')} {item.get('LastName', '')} - {item.get('Title', '')} - {item.get('Email', '')} - {item.get('Department', '')}".strip()
    if object_type == "Opportunity":
        return f"{item.get('Name', '')} - {item.get('StageName', '')} - {item.get('Amount', '')} - {item.get('CloseDate', '')}".strip()
    if object_type == "Case":
        return f"{item.get('Subject', '')} - {item.get('Status', '')} - {item.get('Priority', '')} - {item.get('Description', '')}".strip()
    if object_type == "Customer_Project__c":
        return f"Customer Project: {item.get('Name', '')} - Created: {item.get('CreatedDate', '')}".strip()
    return json.dumps(item)


def get_external_item_from_item(item: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "title@odata.type": "String",
        "title": get_item_title(item),
        "url@odata.type": "String",
        "url": item["url"],
        "objectType": item["objectType"],
    }

    for key, value in item.items():
        if key in EXCLUDED_KEYS or value is None:
            continue
        properties[FIELD_NAME_MAP.get(key, key)] = value

    return {
        "id": item["Id"],
        "properties": properties,
        "content": {
            "value": get_item_content(item),
            "type": "text",
        },
        "acl": get_acl_from_item(item),
    }
