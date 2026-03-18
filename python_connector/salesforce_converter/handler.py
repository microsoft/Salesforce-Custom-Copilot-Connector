"""
SalesforceObjectHandler — core conversion logic.
Mirrors SalesforceObjectHandler.cs:
  - BuildItemPropertiesAndContent
  - AddSchemaPropertyForField
  - AddSchemaPropertyForObjectField
  - SerializeAddressObject
  - GetAuthorsSourceProperty
  - ConstructIngestionItems
  - ConstructIngestionItemsForRecordAndItsChildren
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from salesforce_converter.constants import (
    AUTHORS_SOURCE_PROPERTY,
    CONTENT_FIELD_NAME,
    CREATED_BY_SOURCE_PROPERTY,
    LAST_MODIFIED_BY_SOURCE_PROPERTY,
    METADATA_COLUMN_SCHEMA_MAPPING,
    METADATA_OBJECT_COLUMN_SCHEMA_MAPPING,
    SYSTEM_CREATED_BY_USER_ID,
    SYSTEM_MODIFIED_BY_USER_ID,
    TYPE_CONVERTERS,
)
from salesforce_converter.id_helper import construct_item_id_without_hashing
from salesforce_converter.models import Content, DeletedItem, SearchableItem

logger = logging.getLogger(__name__)


def _resolve_type(assembly_qualified_name: str) -> Optional[str]:
    """Extract the .NET type name and return a Python type tag."""
    if not assembly_qualified_name:
        return None
    dotnet_type = assembly_qualified_name.split(",")[0].strip()
    return TYPE_CONVERTERS.get(dotnet_type)


def _convert_value(value: Any, type_tag: str) -> Any:
    """Convert a JSON value to the target type, matching C# Convert.ChangeType."""
    if value is None:
        return None
    if type_tag == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)
    if type_tag == "float":
        return float(value)
    if type_tag == "int":
        return int(value)
    return str(value)


class SalesforceObjectHandler:
    """
    Replicates SalesforceObjectHandler.cs property-mapping and
    item-construction logic.
    """

    def __init__(
        self,
        sf_object_config: dict,
        icon_url: str = "",
        child_handlers: Optional[list[SalesforceObjectHandler]] = None,
    ):
        """
        Args:
            sf_object_config: One entry from SalesforceConfiguration.json objectList.
                Required keys: objectName, selectedFields
                Optional keys: SfColumnTypes, parentObjectName, objectNameAsChild,
                               filterCondition, iconUrl, flsFields
            icon_url: Resolved CDN icon URL (overrides config).
            child_handlers: Pre-built handlers for child objects.
        """
        self.object_name: str = sf_object_config["objectName"]
        self.selected_fields: dict[str, str] = dict(sf_object_config["selectedFields"])
        self.parent_object_name: Optional[str] = sf_object_config.get("parentObjectName")
        self.object_name_as_child: Optional[str] = sf_object_config.get("objectNameAsChild")
        self.icon_url: str = icon_url or sf_object_config.get("iconUrl", "")
        self.fls_fields: set[str] = set(sf_object_config.get("flsFields", []))

        # Build field data type map
        raw_types: dict[str, str] = sf_object_config.get("SfColumnTypes", {})
        self.field_data_types: dict[str, str] = {}
        for sf_col, aqn in raw_types.items():
            resolved = _resolve_type(aqn)
            if resolved is not None:
                self.field_data_types[sf_col] = resolved

        # Always treat these as datetime
        self.field_data_types["LastModifiedDate"] = "datetime"
        self.field_data_types["CreatedDate"] = "datetime"

        # Build object_fields map for dot-notation selectedFields
        # e.g. {"Account": ["Id", "Name", "OwnerId", "Owner.Name"]}
        self.object_fields: dict[str, list[str]] = {}
        for key in self.selected_fields:
            if "." in key:
                parts = key.split(".")
                parent_key = parts[0]
                sub_key = ".".join(parts[1:])
                self.object_fields.setdefault(parent_key, []).append(sub_key)

        # Child object handlers
        self.child_handlers: list[SalesforceObjectHandler] = child_handlers or []
        self._child_handler_map: dict[str, SalesforceObjectHandler] = {}
        for ch in self.child_handlers:
            if ch.object_name_as_child:
                self._child_handler_map[ch.object_name_as_child] = ch

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def construct_ingestion_items(
        self,
        sf_query_result: dict,
        instance_url: str,
        schema_properties: set[str],
    ) -> list[dict]:
        """
        Top-level entry point. Mirrors ConstructIngestionItems.

        Args:
            sf_query_result: Salesforce SOQL JSON response with "records" list.
            instance_url: e.g. "https://ap15.salesforce.com"
            schema_properties: Set of registered schema property names.

        Returns:
            List of item dicts (SearchableItem or DeletedItem).
        """
        records = sf_query_result.get("records", [])
        all_items: list[dict] = []
        for record in records:
            items = self._construct_items_for_record_and_children(
                record, instance_url, schema_properties
            )
            if items:
                all_items.extend(items)
        return all_items

    # -------------------------------------------------------------------
    # Private — per-record processing
    # -------------------------------------------------------------------

    def _construct_items_for_record_and_children(
        self,
        record: dict,
        instance_url: str,
        schema_properties: set[str],
    ) -> Optional[list[dict]]:
        """Mirrors ConstructIngestionItemsForRecordAndItsChildren."""
        record_id = record.get("Id")
        if not record_id:
            return None

        # 1. Process child objects first
        child_items: list[dict] = []
        for key, value in record.items():
            if key in self._child_handler_map and isinstance(value, dict):
                child_handler = self._child_handler_map[key]
                child_items.extend(
                    child_handler.construct_ingestion_items(
                        value, instance_url, schema_properties
                    )
                )

        # 2. Build parent item
        is_deleted = record.get("IsDeleted")
        if is_deleted is True:
            parent_item = DeletedItem(construct_item_id_without_hashing(record_id))
            return child_items + [parent_item.to_dict()]

        item = SearchableItem(construct_item_id_without_hashing(record_id))
        item.should_hash_id = True
        item.content = self._build_item_properties_and_content(
            record, instance_url, item.properties, schema_properties
        )
        return child_items + [item.to_dict()]

    # -------------------------------------------------------------------
    # BuildItemPropertiesAndContent
    # -------------------------------------------------------------------

    def _build_item_properties_and_content(
        self,
        record: dict,
        instance_url: str,
        props: dict[str, Any],
        schema_properties: set[str],
    ) -> Content:
        """Mirrors BuildItemPropertiesAndContent exactly."""

        # Hardcoded properties
        props["ObjectName"] = self.object_name
        props["Url"] = f"{instance_url}/{record['Id']}"
        if "IconUrl" in schema_properties:
            props["IconUrl"] = self.icon_url

        content = Content()

        # Iterate all fields in the record
        for field_key, field_value in record.items():
            if field_key == "attributes":
                continue

            # Priority 1: selectedFields match
            if field_key in self.selected_fields:
                property_name = self.selected_fields[field_key]
                if property_name in schema_properties:
                    self._add_schema_property_for_field(
                        props, record, field_key, property_name, instance_url
                    )
                    if property_name == CONTENT_FIELD_NAME:
                        raw = record.get(field_key)
                        content = Content(
                            parsed_data=raw if isinstance(raw, str) and raw else ""
                        )

            # Priority 2: MetadataColumnSchemaMapping match
            elif field_key in METADATA_COLUMN_SCHEMA_MAPPING:
                property_name = METADATA_COLUMN_SCHEMA_MAPPING[field_key]
                if property_name in schema_properties:
                    self._add_schema_property_for_field(
                        props, record, field_key, property_name, instance_url
                    )

            # Priority 3: Object-type fields with nested values
            elif field_value is not None and isinstance(field_value, dict):
                # 3a: Config object fields (e.g. "Account" ? ["Id", "Name", ...])
                if field_key in self.object_fields:
                    self._add_schema_property_for_object_field(
                        props, record, field_key,
                        self.object_fields[field_key], self.selected_fields,
                    )
                # 3b: Metadata object columns (e.g. "CreatedBy" ? ["Name"])
                elif field_key in METADATA_OBJECT_COLUMN_SCHEMA_MAPPING:
                    self._add_schema_property_for_object_field(
                        props, record, field_key,
                        METADATA_OBJECT_COLUMN_SCHEMA_MAPPING[field_key],
                        METADATA_COLUMN_SCHEMA_MAPPING,
                    )

        # FLS fields ? null
        for fls_field in self.fls_fields:
            props[fls_field] = None

        # Derived AccountUrl
        if "AccountId" in props:
            props["AccountUrl"] = f"{instance_url}/{props['AccountId']}"

        # Authors
        authors = self._get_authors_source_property(props, schema_properties)
        if authors:
            props[AUTHORS_SOURCE_PROPERTY] = authors

        # System properties
        if SYSTEM_CREATED_BY_USER_ID in schema_properties and "CreatedById" in record:
            props[SYSTEM_CREATED_BY_USER_ID] = str(record["CreatedById"])
        if SYSTEM_MODIFIED_BY_USER_ID in schema_properties and "LastModifiedById" in record:
            props[SYSTEM_MODIFIED_BY_USER_ID] = str(record["LastModifiedById"])

        return content

    # -------------------------------------------------------------------
    # AddSchemaPropertyForField
    # -------------------------------------------------------------------

    def _add_schema_property_for_field(
        self,
        props: dict[str, Any],
        record: dict,
        field_key: str,
        property_name: str,
        instance_url: str,
    ) -> None:
        """Mirrors AddSchemaPropertyForField."""
        value = record.get(field_key)

        # Address object detection: dict with "street" key and multiple children
        if isinstance(value, dict) and "street" in value and len(value) > 1:
            props[property_name] = self._serialize_address_object(value)
            return

        # Typed field
        if field_key in self.field_data_types:
            type_tag = self.field_data_types[field_key]
            try:
                if value is not None:
                    props[property_name] = _convert_value(value, type_tag)
                return
            except (ValueError, TypeError) as e:
                logger.error(
                    "Could not parse %s's %s: %s", self.object_name, field_key, e
                )
                defaults = {"bool": False, "float": 0.0, "int": 0, "datetime": "", "str": ""}
                props[property_name] = defaults.get(type_tag, "")
                return

        # Untyped field — string cast with Id?Url transformation
        try:
            field_data = str(value) if value is not None else None
            if (
                field_data is not None
                and "id" in field_key.lower()
                and "url" in property_name.lower()
            ):
                props[property_name] = f"{instance_url}/{field_data}"
            else:
                props[property_name] = field_data
        except Exception as e:
            logger.error(
                "Could not parse %s's %s: %s", self.object_name, field_key, e
            )
            props[property_name] = ""

    # -------------------------------------------------------------------
    # AddSchemaPropertyForObjectField
    # -------------------------------------------------------------------

    def _add_schema_property_for_object_field(
        self,
        props: dict[str, Any],
        record: dict,
        field_key: str,
        keys: list[str],
        schema_mapping: dict[str, str],
    ) -> None:
        """Mirrors AddSchemaPropertyForObjectField."""
        parent_obj = record.get(field_key)
        if not isinstance(parent_obj, dict):
            return

        for key in keys:
            lookup_key = f"{field_key}.{key}"
            object_property_name = schema_mapping.get(lookup_key)
            if object_property_name is None:
                continue

            try:
                if "." in key:
                    # e.g. key = "Owner.Name" ? parent_obj["Owner"]["Name"]
                    parts = key.split(".")
                    nested = parent_obj
                    for part in parts:
                        nested = nested.get(part) if isinstance(nested, dict) else None
                        if nested is None:
                            break
                    if nested is not None:
                        props[object_property_name] = str(nested)
                else:
                    val = parent_obj.get(key)
                    if val is not None:
                        props[object_property_name] = str(val)
            except Exception as e:
                logger.error(
                    "Could not parse %s's %s: %s", self.object_name, field_key, e
                )
                props[object_property_name] = ""

    # -------------------------------------------------------------------
    # SerializeAddressObject
    # -------------------------------------------------------------------

    @staticmethod
    def _serialize_address_object(token: dict) -> str:
        """Mirrors SerializeAddressObject exactly."""
        parts: list[str] = []
        try:
            if token.get("street"):
                parts.append(token["street"])
            if token.get("city"):
                parts.append(f", {token['city']}")
            if token.get("state"):
                parts.append(f", {token['state']}")
            if token.get("postalCode"):
                parts.append(f" - {token['postalCode']}")
            if token.get("country"):
                parts.append(f", {token['country']}")
        except Exception as e:
            logger.error("Could not parse address: %s", e)
            return ""
        return "".join(parts)

    # -------------------------------------------------------------------
    # GetAuthorsSourceProperty
    # -------------------------------------------------------------------

    @staticmethod
    def _get_authors_source_property(
        props: dict[str, Any],
        schema_properties: set[str],
    ) -> Optional[list[str]]:
        """Mirrors GetAuthorsSourceProperty."""
        if AUTHORS_SOURCE_PROPERTY not in schema_properties:
            return None
        authors: set[str] = set()
        created_by = props.get(CREATED_BY_SOURCE_PROPERTY)
        if created_by:
            authors.add(str(created_by))
        last_modified_by = props.get(LAST_MODIFIED_BY_SOURCE_PROPERTY)
        if last_modified_by:
            authors.add(str(last_modified_by))
        return list(authors) if authors else None
