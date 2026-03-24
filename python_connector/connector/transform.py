from __future__ import annotations

from typing import Any
import json
import os

from connector.item_converter import SalesforceConverter


FIELD_NAME_MAP = {
    "Account__c": "AccountC",
    "Project_description__c": "ProjectDescriptionC",
    "Title": "JobTitle",
}

COLLECTION_SCHEMA_TO_ODATA_TYPE = {
    "StringCollection": "String",
    "Int64Collection": "Int64",
    "DoubleCollection": "Double",
    "BooleanCollection": "Boolean",
    "DateTimeCollection": "DateTime",
}

CONVERTER_TO_LIVE_PROPERTY_MAP = {
    "Title": "JobTitle",
    "LeadStatus": "Status",
    "CaseClosedDate": "ClosedDate",
    "CaseCreatedDate": "CreatedDate",
}

EXCLUDED_KEYS = {"Id", "objectType", "url", "attributes"}


def _get_collection_type(values: list) -> str | None:
    """
    Determine the OData collection type from list values.
    Returns: "String", "Int64", "Double", "Boolean", or "DateTime"
    """
    if not values:
        return "String"  # Default to String for empty lists
    
    # Check first non-None value to determine type
    for value in values:
        if value is None:
            continue
        
        if isinstance(value, bool):
            return "Boolean"
        elif isinstance(value, int):
            return "Int64"
        elif isinstance(value, float):
            return "Double"
        elif isinstance(value, str):
            # Check if it's a datetime string (ISO 8601 format)
            if "T" in value and ("Z" in value or "+" in value or value.endswith("00")):
                return "DateTime"
            return "String"
        break
    
    return "String"  # Default fallback


def _fallback_acl() -> list[dict[str, str]]:
    return [
        {
            "accessType": "grant",
            "type": "everyone",
            "value": os.getenv("AZURE_TENANT_ID") or "everyone",
        }
    ]


def get_item_title(item: dict[str, Any]) -> str:
    object_type = item.get("objectType")
    if object_type == "Account":
        return item.get("Name") or item["Id"]
    if object_type in {"Lead", "Contact"}:
        full_name = f"{item.get('FirstName', '')} {item.get('LastName', '')}".strip()
        return full_name or item.get("Name") or item["Id"]
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


class SalesforceItemTransformer:
    def __init__(self, instance_url: str, schema: list[dict[str, Any]]):
        self._schema_property_types = {
            prop["name"]: prop.get("type")
            for prop in schema
            if prop.get("name")
        }
        self._schema_properties = set(self._schema_property_types)
        self._converter = SalesforceConverter(instance_url=instance_url)
        self._supported_objects = set(self._converter.object_names)

    @property
    def handlers(self) -> dict[str, Any]:
        return {
            object_name: self._converter.get_handler(object_name)
            for object_name in self._supported_objects
            if self._converter.get_handler(object_name) is not None
        }

    def transform_record(
        self,
        item: dict[str, Any],
        acl: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        object_type = item.get("objectType")
        if object_type not in self._supported_objects:
            return [self._build_legacy_item(item, acl)]

        converted_items = self._converter.convert({"records": [item]}, object_name=object_type)
        transformed_items: list[dict[str, Any]] = []
        for converted_item in converted_items:
            if converted_item.get("type") == "deleted":
                transformed_items.append(converted_item)
                continue
            transformed_items.append(self._build_live_item(item, converted_item, acl))
        return transformed_items

    def _build_live_item(
        self,
        raw_item: dict[str, Any],
        converted_item: dict[str, Any],
        acl: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        converted_properties = converted_item.get("properties") or {}
        properties: dict[str, Any] = {
            "title": get_item_title(raw_item),
            "url": converted_properties.get("Url") or raw_item["url"],
            "objectType": raw_item["objectType"],
        }

        for key, value in converted_properties.items():
            if key in {"ObjectName", "Url"} or value is None:
                continue
            live_key = CONVERTER_TO_LIVE_PROPERTY_MAP.get(key, key)
            if live_key not in self._schema_properties:
                continue
            if live_key in {"title", "url", "objectType"}:
                continue

            normalized_value = self._normalize_schema_value(live_key, value)
            self._apply_collection_annotation(properties, live_key, normalized_value)
            properties[live_key] = normalized_value

        for key, value in raw_item.items():
            if key in EXCLUDED_KEYS or value is None:
                continue
            live_key = FIELD_NAME_MAP.get(key, key)
            if live_key not in self._schema_properties:
                continue
            if live_key in {"title", "url", "objectType"}:
                continue

            normalized_value = self._normalize_schema_value(live_key, value)
            self._apply_collection_annotation(properties, live_key, normalized_value)
            properties.setdefault(live_key, normalized_value)

        converted_content = converted_item.get("content") or {}
        content_value = converted_content.get("parsedData") if isinstance(converted_content, dict) else None

        return {
            "id": converted_item.get("id") or raw_item["Id"],
            "properties": properties,
            "content": {
                "value": content_value or get_item_content(raw_item),
                "type": "text",
            },
            "acl": acl or _fallback_acl(),
        }

    @staticmethod
    def _build_legacy_item(
        item: dict[str, Any],
        acl: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "title": get_item_title(item),
            "url": item["url"],
            "objectType": item["objectType"],
        }

        for key, value in item.items():
            if key in EXCLUDED_KEYS or value is None:
                continue
            field_key = FIELD_NAME_MAP.get(key, key)

            if isinstance(value, list):
                collection_type = _get_collection_type(value)
                if collection_type:
                    properties[f"{field_key}@odata.type"] = f"Collection({collection_type})"

            properties[field_key] = value

        return {
            "id": item["Id"],
            "properties": properties,
            "content": {
                "value": get_item_content(item),
                "type": "text",
            },
            "acl": acl or _fallback_acl(),
        }

    def _normalize_schema_value(self, live_key: str, value: Any) -> Any:
        collection_type = COLLECTION_SCHEMA_TO_ODATA_TYPE.get(self._schema_property_types.get(live_key, ""))
        if not collection_type:
            return value

        if isinstance(value, list):
            return value

        if isinstance(value, tuple):
            return list(value)

        return [value]

    def _apply_collection_annotation(
        self,
        properties: dict[str, Any],
        live_key: str,
        value: Any,
    ) -> None:
        schema_collection_type = COLLECTION_SCHEMA_TO_ODATA_TYPE.get(
            self._schema_property_types.get(live_key, "")
        )
        if schema_collection_type:
            properties[f"{live_key}@odata.type"] = f"Collection({schema_collection_type})"
            return

        if isinstance(value, list):
            collection_type = _get_collection_type(value)
            if collection_type:
                properties.setdefault(f"{live_key}@odata.type", f"Collection({collection_type})")
