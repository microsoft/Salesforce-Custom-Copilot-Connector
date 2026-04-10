from __future__ import annotations

from typing import Any
import os

from Item.item_converter import SalesforceConverter


COLLECTION_SCHEMA_TO_ODATA_TYPE = {
    "StringCollection": "String",
    "Int64Collection": "Int64",
    "DoubleCollection": "Double",
    "BooleanCollection": "Boolean",
    "DateTimeCollection": "DateTime",
}

def _fallback_acl() -> list[dict[str, str]]:
    return [
        {
            "accessType": "grant",
            "type": "everyone",
            "value": os.getenv("AZURE_TENANT_ID") or "everyone",
        }
    ]


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
            "Url": converted_properties.get("Url") or raw_item["url"],
            "ObjectName": raw_item["objectType"],
        }

        for key, value in converted_properties.items():
            if key in {"ObjectName", "Url"} or value is None:
                continue
            if key not in self._schema_properties:
                continue

            normalized_value = self._normalize_schema_value(key, value)
            self._apply_collection_annotation(properties, key, normalized_value)
            properties[key] = normalized_value

        converted_content = converted_item.get("content") or {}
        content_value = converted_content.get("parsedData") if isinstance(converted_content, dict) else None

        return {
            "id": converted_item.get("id") or raw_item["Id"],
            "properties": properties,
            "content": {
                "value": content_value or "",
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
