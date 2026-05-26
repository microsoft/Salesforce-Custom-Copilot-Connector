from __future__ import annotations

from typing import Any

from item.converter import SalesforceConverter


PRINCIPAL_ODATA_TYPE = "#microsoft.graph.externalConnectors.principal"

COLLECTION_SCHEMA_TO_ODATA_TYPE = {
    "StringCollection": "String",
    "Int64Collection": "Int64",
    "DoubleCollection": "Double",
    "BooleanCollection": "Boolean",
    "DateTimeCollection": "DateTime",
    "PrincipalCollection": "microsoft.graph.externalConnectors.principal",
}


def _inject_principal_odata_type(obj: Any) -> Any:
    """Inject ``@odata.type`` into a principal dict if not already present."""
    if isinstance(obj, dict) and "@odata.type" not in obj:
        return {"@odata.type": PRINCIPAL_ODATA_TYPE, **obj}
    return obj

def _fallback_acl() -> list[dict[str, str]]:
    """Return a deny-everyone ACL when no ACL could be resolved.

    Items ingested with this ACL will not appear in any user's search results.
    This is the safe default — it prevents accidental data exposure when ACL
    resolution fails.
    """
    return [
        {
            "accessType": "deny",
            "type": "everyone",
            "value": "everyone",
        }
    ]


class SalesforceItemTransformer:
    def __init__(self, instance_url: str, schema: list[dict[str, Any]], tenant_id: str = "everyone"):
        """Initialize the transformer with a Salesforce *instance_url* and Graph connector *schema*."""
        self._tenant_id = tenant_id
        self._schema_property_types = {
            prop["name"]: prop.get("type")
            for prop in schema
            if prop.get("name")
        }
        self._schema_properties = set(self._schema_property_types)
        self._converter = SalesforceConverter(instance_url=instance_url)
        self._supported_objects = set(self._converter.object_names)
        # Inject the real Graph schema properties and types into each handler so the
        # debug mapping table and principal promotion can use live schema info.
        for handler in self._converter._handlers.values():
            handler.graph_schema_properties = set(self._schema_properties)
            handler.graph_schema_property_types = self._schema_property_types

    @property
    def handlers(self) -> dict[str, Any]:
        """Map of supported object names to their converter handlers."""
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
        """Convert a raw Salesforce record into one or more Graph connector external items."""
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
        """Assemble a Graph connector external item dict from raw and converted data."""
        converted_properties = converted_item.get("properties") or {}
        properties: dict[str, Any] = {
            "url": converted_properties.get("url") or raw_item["url"],
            "ObjectName": raw_item["objectType"]
        }

        for key, value in converted_properties.items():
            if key in {"ObjectName", "url"} or value is None:
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
        """Wrap scalar values in a list when the schema declares a collection type."""
        schema_type = self._schema_property_types.get(live_key, "")

        if schema_type == "Principal":
            return _inject_principal_odata_type(value)

        collection_type = COLLECTION_SCHEMA_TO_ODATA_TYPE.get(schema_type)
        if not collection_type:
            return value

        if isinstance(value, list):
            items = value
        elif isinstance(value, tuple):
            items = list(value)
        else:
            items = [value]

        if schema_type == "PrincipalCollection":
            return [_inject_principal_odata_type(item) for item in items]

        return items

    def _apply_collection_annotation(
        self,
        properties: dict[str, Any],
        live_key: str,
        value: Any,
    ) -> None:
        """Add an ``@odata.type`` annotation to *properties* when *live_key* is a collection type."""
        schema_collection_type = COLLECTION_SCHEMA_TO_ODATA_TYPE.get(
            self._schema_property_types.get(live_key, "")
        )
        if schema_collection_type:
            properties[f"{live_key}@odata.type"] = f"Collection({schema_collection_type})"
