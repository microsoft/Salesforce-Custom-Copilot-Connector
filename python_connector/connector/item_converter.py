from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import json
import logging

from connector.item_models import Content, DeletedItem, SearchableItem


logger = logging.getLogger("salesforce_connector")

CONTENT_FIELD_NAME = "Description"
AUTHORS_SOURCE_PROPERTY = "Authors"
CREATED_BY_SOURCE_PROPERTY = "CreatedBy"
LAST_MODIFIED_BY_SOURCE_PROPERTY = "LastModifiedBy"
SYSTEM_CREATED_BY_USER_ID = "__System.User.CreatedBy.Id"
SYSTEM_MODIFIED_BY_USER_ID = "__System.User.ModifiedBy.Id"

METADATA_COLUMNS = [
    "Id",
    "LastModifiedDate",
    "IsDeleted",
    "Owner.UserRole.ParentRoleId",
    "OwnerId",
    "Owner.Name",
    "LastModifiedById",
    "LastModifiedBy.Name",
    "CreatedById",
    "CreatedBy.Name",
    "CreatedDate",
]

METADATA_COLUMN_SCHEMA_MAPPING: dict[str, str] = {
    "CreatedDate": "CreatedDate",
    "LastModifiedDate": "LastModifiedDate",
    "LastModifiedBy.Name": LAST_MODIFIED_BY_SOURCE_PROPERTY,
    "LastModifiedById": "LastModifiedByUrl",
    "CreatedById": "CreatedByUrl",
    "CreatedBy.Name": CREATED_BY_SOURCE_PROPERTY,
    "Owner.Name": "Owner",
    "OwnerId": "OwnerUrl",
    "Id": "Id",
}

METADATA_OBJECT_COLUMN_SCHEMA_MAPPING: dict[str, list[str]] = {
    "LastModifiedBy": ["Name"],
    "CreatedBy": ["Name"],
    "Owner": ["Name"],
}

TYPE_CONVERTERS: dict[str, str] = {
    "System.Boolean": "bool",
    "System.Double": "float",
    "System.DateTime": "datetime",
    "System.Int32": "int",
    "System.Int64": "int",
    "System.String": "str",
}

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "references" / "schema.json"


def load_converter_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or _DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_type(assembly_qualified_name: str) -> Optional[str]:
    if not assembly_qualified_name:
        return None
    dotnet_type = assembly_qualified_name.split(",")[0].strip()
    return TYPE_CONVERTERS.get(dotnet_type)


def _convert_value(value: Any, type_tag: str) -> Any:
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
    def __init__(
        self,
        sf_object_config: dict[str, Any],
        icon_url: str = "",
        child_handlers: Optional[list["SalesforceObjectHandler"]] = None,
    ):
        self.object_name: str = sf_object_config["objectName"]
        self.selected_fields: dict[str, str] = dict(sf_object_config["selectedFields"])
        self.parent_object_name: Optional[str] = sf_object_config.get("parentObjectName")
        self.object_name_as_child: Optional[str] = sf_object_config.get("objectNameAsChild")
        self.icon_url: str = icon_url or sf_object_config.get("iconUrl", "")
        self.fls_fields: set[str] = set(sf_object_config.get("flsFields", []))

        raw_types: dict[str, str] = sf_object_config.get("SfColumnTypes", {})
        self.field_data_types: dict[str, str] = {}
        for sf_column, assembly_name in raw_types.items():
            resolved = _resolve_type(assembly_name)
            if resolved is not None:
                self.field_data_types[sf_column] = resolved

        self.field_data_types["LastModifiedDate"] = "datetime"
        self.field_data_types["CreatedDate"] = "datetime"

        self.object_fields: dict[str, list[str]] = {}
        for key in self.selected_fields:
            if "." not in key:
                continue
            parent_key, child_key = key.split(".", 1)
            self.object_fields.setdefault(parent_key, []).append(child_key)

        self.child_handlers: list[SalesforceObjectHandler] = child_handlers or []
        self._child_handler_map: dict[str, SalesforceObjectHandler] = {}
        for child_handler in self.child_handlers:
            if child_handler.object_name_as_child:
                self._child_handler_map[child_handler.object_name_as_child] = child_handler

    def construct_ingestion_items(
        self,
        sf_query_result: dict[str, Any],
        instance_url: str,
        schema_properties: set[str],
    ) -> list[dict[str, Any]]:
        records = sf_query_result.get("records", [])
        all_items: list[dict[str, Any]] = []
        for record in records:
            items = self._construct_items_for_record_and_children(
                record,
                instance_url,
                schema_properties,
            )
            if items:
                all_items.extend(items)
        return all_items

    def _construct_items_for_record_and_children(
        self,
        record: dict[str, Any],
        instance_url: str,
        schema_properties: set[str],
    ) -> list[dict[str, Any]] | None:
        record_id = record.get("Id")
        if not record_id:
            return None

        child_items: list[dict[str, Any]] = []
        for key, value in record.items():
            if key in self._child_handler_map and isinstance(value, dict):
                child_handler = self._child_handler_map[key]
                child_items.extend(
                    child_handler.construct_ingestion_items(value, instance_url, schema_properties)
                )

        if record.get("IsDeleted") is True:
            return child_items + [DeletedItem(str(record_id)).to_dict()]

        item = SearchableItem(str(record_id))
        item.content = self._build_item_properties_and_content(
            record,
            instance_url,
            item.properties,
            schema_properties,
        )
        return child_items + [item.to_dict()]

    def _build_item_properties_and_content(
        self,
        record: dict[str, Any],
        instance_url: str,
        props: dict[str, Any],
        schema_properties: set[str],
    ) -> Content:
        props["ObjectName"] = self.object_name
        props["Url"] = f"{instance_url}/{record['Id']}"
        if "IconUrl" in schema_properties:
            props["IconUrl"] = self.icon_url

        content = Content()

        for field_key, field_value in record.items():
            if field_key == "attributes":
                continue

            if field_key in self.selected_fields:
                property_name = self.selected_fields[field_key]
                if property_name in schema_properties:
                    self._add_schema_property_for_field(
                        props,
                        record,
                        field_key,
                        property_name,
                        instance_url,
                    )
                    if property_name == CONTENT_FIELD_NAME:
                        raw_value = record.get(field_key)
                        content = Content(raw_value if isinstance(raw_value, str) and raw_value else "")
            elif field_key in METADATA_COLUMN_SCHEMA_MAPPING:
                property_name = METADATA_COLUMN_SCHEMA_MAPPING[field_key]
                if property_name in schema_properties:
                    self._add_schema_property_for_field(
                        props,
                        record,
                        field_key,
                        property_name,
                        instance_url,
                    )
            elif field_value is not None and isinstance(field_value, dict):
                if field_key in self.object_fields:
                    self._add_schema_property_for_object_field(
                        props,
                        record,
                        field_key,
                        self.object_fields[field_key],
                        self.selected_fields,
                    )
                elif field_key in METADATA_OBJECT_COLUMN_SCHEMA_MAPPING:
                    self._add_schema_property_for_object_field(
                        props,
                        record,
                        field_key,
                        METADATA_OBJECT_COLUMN_SCHEMA_MAPPING[field_key],
                        METADATA_COLUMN_SCHEMA_MAPPING,
                    )

        for fls_field in self.fls_fields:
            props[fls_field] = None

        if "AccountId" in props:
            props["AccountUrl"] = f"{instance_url}/{props['AccountId']}"

        authors = self._get_authors_source_property(props, schema_properties)
        if authors:
            props[AUTHORS_SOURCE_PROPERTY] = authors

        if SYSTEM_CREATED_BY_USER_ID in schema_properties and "CreatedById" in record:
            props[SYSTEM_CREATED_BY_USER_ID] = str(record["CreatedById"])

        if SYSTEM_MODIFIED_BY_USER_ID in schema_properties and "LastModifiedById" in record:
            props[SYSTEM_MODIFIED_BY_USER_ID] = str(record["LastModifiedById"])

        return content

    def _add_schema_property_for_field(
        self,
        props: dict[str, Any],
        record: dict[str, Any],
        field_key: str,
        property_name: str,
        instance_url: str,
    ) -> None:
        value = record.get(field_key)

        if isinstance(value, dict) and "street" in value and len(value) > 1:
            props[property_name] = self._serialize_address_object(value)
            return

        if field_key in self.field_data_types:
            type_tag = self.field_data_types[field_key]
            try:
                if value is not None:
                    props[property_name] = _convert_value(value, type_tag)
                return
            except (TypeError, ValueError) as error:
                logger.error("Could not parse %s.%s: %s", self.object_name, field_key, error)
                defaults = {"bool": False, "float": 0.0, "int": 0, "datetime": "", "str": ""}
                props[property_name] = defaults.get(type_tag, "")
                return

        try:
            field_data = str(value) if value is not None else None
            if field_data is not None and "id" in field_key.lower() and "url" in property_name.lower():
                props[property_name] = f"{instance_url}/{field_data}"
            else:
                props[property_name] = field_data
        except Exception as error:  # pragma: no cover - defensive fallback
            logger.error("Could not parse %s.%s: %s", self.object_name, field_key, error)
            props[property_name] = ""

    def _add_schema_property_for_object_field(
        self,
        props: dict[str, Any],
        record: dict[str, Any],
        field_key: str,
        keys: list[str],
        schema_mapping: dict[str, str],
    ) -> None:
        parent_object = record.get(field_key)
        if not isinstance(parent_object, dict):
            return

        for key in keys:
            lookup_key = f"{field_key}.{key}"
            property_name = schema_mapping.get(lookup_key)
            if property_name is None:
                continue

            try:
                if "." in key:
                    nested_value: Any = parent_object
                    for part in key.split("."):
                        nested_value = nested_value.get(part) if isinstance(nested_value, dict) else None
                        if nested_value is None:
                            break
                    if nested_value is not None:
                        props[property_name] = str(nested_value)
                else:
                    nested_value = parent_object.get(key)
                    if nested_value is not None:
                        props[property_name] = str(nested_value)
            except Exception as error:  # pragma: no cover - defensive fallback
                logger.error("Could not parse %s.%s: %s", self.object_name, field_key, error)
                props[property_name] = ""

    @staticmethod
    def _serialize_address_object(token: dict[str, Any]) -> str:
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
        except Exception as error:  # pragma: no cover - defensive fallback
            logger.error("Could not parse address: %s", error)
            return ""
        return "".join(parts)

    @staticmethod
    def _get_authors_source_property(
        props: dict[str, Any],
        schema_properties: set[str],
    ) -> list[str] | None:
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


def build_handlers_from_config(
    config: dict[str, Any],
    icon_url: str = "",
) -> dict[str, SalesforceObjectHandler]:
    handlers: dict[str, SalesforceObjectHandler] = {}
    children: list[dict[str, Any]] = []

    for object_config in config["objectList"]:
        if object_config.get("parentObjectName"):
            children.append(object_config)
            continue
        handlers[object_config["objectName"]] = SalesforceObjectHandler(object_config, icon_url=icon_url)

    for child_config in children:
        child_handler = SalesforceObjectHandler(child_config, icon_url=icon_url)
        parent_name = child_config["parentObjectName"]
        if parent_name in handlers:
            handlers[parent_name].child_handlers.append(child_handler)
            if child_handler.object_name_as_child:
                handlers[parent_name]._child_handler_map[child_handler.object_name_as_child] = child_handler
        handlers[child_config["objectName"]] = child_handler

    return handlers


def _build_schema_properties(handlers: dict[str, SalesforceObjectHandler]) -> set[str]:
    props: set[str] = {"ObjectName", "Url", "IconUrl", "AccountUrl"}
    props.update(METADATA_COLUMN_SCHEMA_MAPPING.values())
    props.update({AUTHORS_SOURCE_PROPERTY, SYSTEM_CREATED_BY_USER_ID, SYSTEM_MODIFIED_BY_USER_ID})
    for handler in handlers.values():
        props.update(handler.selected_fields.values())
    return props


class SalesforceConverter:
    def __init__(
        self,
        instance_url: str,
        config: dict[str, Any] | None = None,
        schema_properties: set[str] | None = None,
        icon_url: str = "",
    ):
        self._instance_url = instance_url
        effective_config = config if config is not None else load_converter_config()
        self._handlers = build_handlers_from_config(effective_config, icon_url=icon_url)
        self._schema_properties = schema_properties if schema_properties is not None else _build_schema_properties(self._handlers)

    @property
    def object_names(self) -> list[str]:
        return list(self._handlers.keys())

    @property
    def parent_object_names(self) -> list[str]:
        return [
            object_name
            for object_name, handler in self._handlers.items()
            if handler.parent_object_name is None
        ]

    @property
    def schema_properties(self) -> set[str]:
        return set(self._schema_properties)

    def get_handler(self, object_name: str) -> SalesforceObjectHandler | None:
        return self._handlers.get(object_name)

    def convert(
        self,
        sf_query_result: dict[str, Any],
        object_name: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_object_name = object_name or self._infer_object_name(sf_query_result)
        handler = self._handlers.get(effective_object_name)
        if handler is None:
            raise ValueError(f"Unknown object '{effective_object_name}'. Available: {self.object_names}")
        return handler.construct_ingestion_items(
            sf_query_result,
            self._instance_url,
            self._schema_properties,
        )

    @staticmethod
    def _infer_object_name(sf_query_result: dict[str, Any]) -> str:
        records = sf_query_result.get("records", [])
        if not records:
            raise ValueError("Cannot infer object_name from an empty records list")
        attributes = records[0].get("attributes")
        if not isinstance(attributes, dict) or "type" not in attributes:
            raise ValueError("Cannot infer object_name: first record has no attributes.type")
        return str(attributes["type"])