"""
Salesforce → Graph external item conversion engine.

Transforms raw Salesforce SOQL query results into Microsoft Graph
``externalItem`` payloads.  The conversion is driven entirely by the
schema defined in ``config/schema.json``.

Key concepts
------------
* **SalesforceObjectHandler** — one per Salesforce object type (e.g. Account,
  Case).  Reads ``selectedFields`` from the schema config to know which
  Salesforce fields map to which Graph schema properties.  Handles nested
  relationship objects (``Owner.Name``), address serialisation, type
  coercion (bool / int / float / datetime), and parent-child hierarchies
  (e.g. Account → Opportunity).

* **SalesforceConverter** — high-level facade.  Instantiated once per
  ingestion run.  Call ``convert(sf_query_result)`` to get a list of
  ``externalItem`` dicts ready for the Graph PUT API.

* **build_handlers_from_config(config)** — factory that creates the full
  handler tree (parents + children) from ``config/schema.json``.

Constants
---------
METADATA_COLUMNS
    Standard Salesforce metadata fields (Id, OwnerId, CreatedDate, etc.)
    that are always requested in SOQL queries regardless of the schema.

METADATA_COLUMN_SCHEMA_MAPPING
    Maps Salesforce metadata field names to their Graph schema property names.

TYPE_CONVERTERS
    Maps .NET type names (from Salesforce describe metadata) to Python type
    tags used by ``_convert_value``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import json
import logging

from item.models import Content, DeletedItem, SearchableItem


logger = logging.getLogger("salesforce_connector")

CONTENT_FIELD_NAME = "Description"
AUTHORS_SOURCE_PROPERTY = "Authors"
CREATED_BY_SOURCE_PROPERTY = "CreatedBy"
LAST_MODIFIED_BY_SOURCE_PROPERTY = "LastModifiedBy"
SYSTEM_CREATED_BY_USER_ID = "__System.User.CreatedBy.Id"
SYSTEM_MODIFIED_BY_USER_ID = "__System.User.ModifiedBy.Id"

# Zero GUID used as the entraId placeholder when no Entra identity mapping exists.
_ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def _build_principal_dict(external_name: str, external_id: str) -> dict[str, Any]:
    """Return a full principal dict with all Graph principal fields.

    Entra-specific fields (entraDisplayName, entraId, email, upn, tenantId) are
    populated later by the ACL / identity engine once a Salesforce user is mapped
    to an Entra identity.  Until then:
    - string fields are set to ``None``
    - ``entraId`` is set to the zero GUID so the Graph API always receives a
      valid GUID rather than ``null``.
    """
    return {
        "externalName": external_name,
        "externalId": external_id,
        "entraDisplayName": None,
        "entraId": _ZERO_GUID,
        "email": None,
        "upn": None,
        "tenantId": None,
    }

METADATA_COLUMNS = [
    #"Id",
    "LastModifiedDate",
    "IsDeleted",
    "Owner.UserRole.Id",
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


def load_converter_config(path: Path | None = None) -> dict[str, Any]:
    """Load the schema config from *path* or fall back to the default settings."""
    if path is not None:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    from salesforce.settings import load_schema_config
    return load_schema_config()


def _resolve_type(assembly_qualified_name: str) -> Optional[str]:
    """Map a .NET assembly-qualified type name to a Python type tag."""
    if not assembly_qualified_name:
        return None
    dotnet_type = assembly_qualified_name.split(",")[0].strip()
    return TYPE_CONVERTERS.get(dotnet_type)


def _convert_value(value: Any, type_tag: str) -> Any:
    """Coerce *value* to the Python type indicated by *type_tag*."""
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
    if type_tag == "datetime":
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        else:
            normalized = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


class SalesforceObjectHandler:
    def __init__(
        self,
        sf_object_config: dict[str, Any],
        icon_url: str = "",
        child_handlers: Optional[list["SalesforceObjectHandler"]] = None,
    ):
        """Initialise a handler from a single object entry in the schema config."""
        self.object_name: str = sf_object_config["objectName"]
        self.selected_fields: dict[str, str] = dict(sf_object_config["selectedFields"])
        self.parent_object_name: Optional[str] = sf_object_config.get("parentObjectName")
        self.object_name_as_child: Optional[str] = sf_object_config.get("objectNameAsChild")
        self.icon_url: str = icon_url or sf_object_config.get("iconUrl", "")
        self.fls_fields: set[str] = set(sf_object_config.get("flsFields", []))
        # Set externally by the transformer to reflect the actual Graph schema properties
        self.graph_schema_properties: set[str] | None = None
        # Set externally by the transformer: maps property name → Graph schema type (e.g. "Principal")
        self.graph_schema_property_types: dict[str, str] | None = None

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

        self.parent_record_lookup_paths: tuple[str, ...] = self._build_parent_record_lookup_paths()

    def get_parent_record_id(self, record: dict[str, Any]) -> str | None:
        """Return the parent record ID from *record*, or ``None`` if not found."""
        for field_path in self.parent_record_lookup_paths:
            value = self._get_record_value(record, field_path)
            if value:
                return str(value)
        return None

    def _build_parent_record_lookup_paths(self) -> tuple[str, ...]:
        """Build an ordered tuple of field paths used to locate the parent record ID."""
        if not self.parent_object_name:
            return ()

        expected_property_name = f"{self.parent_object_name}Id"
        lookup_paths: list[str] = []

        for raw_field, property_name in self.selected_fields.items():
            if property_name == expected_property_name and raw_field not in lookup_paths:
                lookup_paths.append(raw_field)

        for fallback_path in (expected_property_name, f"{self.parent_object_name}.Id"):
            if fallback_path not in lookup_paths:
                lookup_paths.append(fallback_path)

        return tuple(lookup_paths)

    @staticmethod
    def _get_record_value(record: dict[str, Any], field_path: str) -> Any:
        """Retrieve a value from *record* using a dot-separated *field_path*."""
        if field_path in record:
            return record.get(field_path)

        current: Any = record
        for part in field_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current

    def construct_ingestion_items(
        self,
        sf_query_result: dict[str, Any],
        instance_url: str,
        schema_properties: set[str],
    ) -> list[dict[str, Any]]:
        """Convert a Salesforce query result into a list of Graph external-item dicts."""
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
        """Build ingestion items for a single record and its inline child records."""
        record_id = record.get("Id")
        if not record_id:
            logger.warning(
                "[%s] Skipping record with missing/null Id — record keys: %s",
                self.object_name, list(record.keys()),
            )
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
        """Populate *props* from the Salesforce *record* and return a ``Content`` object.

        Maps selected fields and metadata columns to their Graph schema
        property names, performs type coercion, and collects remaining fields
        into the full-text content body.
        """
        # Use the real Graph schema properties if available; fall back to converter's schema_properties
        _graph_props = self.graph_schema_properties or schema_properties

        props["ObjectName"] = self.object_name
        props["url"] = f"{instance_url}{record['Id']}"
        if "IconUrl" in _graph_props:
            props["IconUrl"] = self.icon_url

        content = Content()

        # Collect field mapping trace for debug logging
        # Use the real Graph schema properties if available; fall back to converter's schema_properties
        _field_mapping: list[tuple[str, str, str, bool]] = []  # (sf_field, graph_property, disposition, in_schema)
        _field_mapping.append(("(object_name)", "ObjectName", "synthetic", "ObjectName" in _graph_props))
        _field_mapping.append(("(instance_url + Id)", "url", "synthetic", "url" in _graph_props))
        if "IconUrl" in _graph_props:
            _field_mapping.append(("(icon_url)", "IconUrl", "synthetic", "IconUrl" in _graph_props))

        for field_key, field_value in record.items():
            if field_key == "attributes":
                continue

            if field_key in self.selected_fields:
                property_name = self.selected_fields[field_key]
                if property_name in _graph_props:
                    self._add_schema_property_for_field(
                        props,
                        record,
                        field_key,
                        property_name,
                        instance_url,
                    )
                    _field_mapping.append((field_key, property_name, "selectedFields", True))
                    if property_name == CONTENT_FIELD_NAME:
                        raw_value = record.get(field_key)
                        content = Content(raw_value if isinstance(raw_value, str) and raw_value else "")
                else:
                    _field_mapping.append((field_key, property_name, "selectedFields → content", False))
            elif field_key in METADATA_COLUMN_SCHEMA_MAPPING:
                property_name = METADATA_COLUMN_SCHEMA_MAPPING[field_key]
                if property_name in _graph_props:
                    self._add_schema_property_for_field(
                        props,
                        record,
                        field_key,
                        property_name,
                        instance_url,
                    )
                    _field_mapping.append((field_key, property_name, "metadata", True))
                else:
                    _field_mapping.append((field_key, property_name, "metadata → content", False))
            elif field_value is not None and isinstance(field_value, dict):
                if field_key in self.object_fields:
                    self._add_schema_property_for_object_field(
                        props,
                        record,
                        field_key,
                        self.object_fields[field_key],
                        self.selected_fields,
                        instance_url,
                    )
                elif field_key in METADATA_OBJECT_COLUMN_SCHEMA_MAPPING:
                    self._add_schema_property_for_object_field(
                        props,
                        record,
                        field_key,
                        METADATA_OBJECT_COLUMN_SCHEMA_MAPPING[field_key],
                        METADATA_COLUMN_SCHEMA_MAPPING,
                        instance_url,
                    )

        for fls_field in self.fls_fields:
            props[fls_field] = None

        if "AccountId" in props:
            props["AccountUrl"] = f"{instance_url}/{props['AccountId']}"

        # Build Authors first, while CreatedBy/LastModifiedBy are still plain name strings.
        authors = self._get_authors_source_property(props, _graph_props, record)
        if authors:
            props[AUTHORS_SOURCE_PROPERTY] = authors

        # Promote any string property to a principal dict where the schema declares Principal type.
        # Runs after Authors so it doesn't stringify already-promoted dicts.
        # Uses Salesforce naming convention: the ID field for a property named X is X + "Id" on the record.
        _prop_types = self.graph_schema_property_types or {}
        for prop_name in list(props.keys()):
            if (
                isinstance(props[prop_name], str)
                and _prop_types.get(prop_name) == "Principal"
            ):
                id_field = f"{prop_name}Id"
                external_id = str(record.get(id_field)) if record.get(id_field) else ""
                props[prop_name] = _build_principal_dict(props[prop_name], external_id)

        if SYSTEM_CREATED_BY_USER_ID in _graph_props and "CreatedById" in record:
            props[SYSTEM_CREATED_BY_USER_ID] = str(record["CreatedById"])

        if SYSTEM_MODIFIED_BY_USER_ID in _graph_props and "LastModifiedById" in record:
            props[SYSTEM_MODIFIED_BY_USER_ID] = str(record["LastModifiedById"])

        content_parts: list[str] = []
        if content.parsed_data:
            content_parts.append(content.parsed_data)

        for field_key, field_value in record.items():
            if field_key in {"attributes", "Id", "url", "objectType"}:
                continue

            field_in_schema = False
            if field_key in self.selected_fields:
                property_name = self.selected_fields[field_key]
                if property_name in _graph_props:
                    field_in_schema = True
            elif field_key in METADATA_COLUMN_SCHEMA_MAPPING:
                property_name = METADATA_COLUMN_SCHEMA_MAPPING[field_key]
                if property_name in _graph_props:
                    field_in_schema = True

            if field_in_schema or field_value is None:
                continue

            if isinstance(field_value, dict):
                for sub_key, sub_value in field_value.items():
                    if (
                        sub_key != "attributes"
                        and sub_value is not None
                        and not isinstance(sub_value, (dict, list))
                    ):
                        content_parts.append(f"{field_key}.{sub_key}: {sub_value}")
                        _field_mapping.append((f"{field_key}.{sub_key}", "content.value", "unmapped (nested)", False))
            elif not isinstance(field_value, list):
                content_parts.append(f"{field_key}: {field_value}")
                _field_mapping.append((field_key, "content.value", "unmapped", False))

        if content_parts:
            content.parsed_data = ", ".join(content_parts)

        # Emit the field-mapping table at DEBUG level (visible with --verbose or in log file)
        if _field_mapping:
            record_id = record.get("Id", "?")
            header = f"FIELD MAPPING TABLE — {self.object_name}/{record_id}"
            lines = [
                f"  {'SF Field':<45s} {'Graph Target':<35s} {'In Schema':<12s} {'Source'}",
                f"  {'─' * 45} {'─' * 35} {'─' * 12} {'─' * 25}",
            ]
            for sf_field, graph_prop, source, in_schema in _field_mapping:
                schema_flag = '✓' if in_schema else '✗'
                lines.append(f"  {sf_field:<45s} {graph_prop:<35s} {schema_flag:<12s} {source}")
            logger.debug("%s\n%s", header, "\n".join(lines))

        return content

    def _add_schema_property_for_field(
        self,
        props: dict[str, Any],
        record: dict[str, Any],
        field_key: str,
        property_name: str,
        instance_url: str,
    ) -> None:
        """Add a single scalar or address field to *props* with type coercion."""
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
        instance_url: str = "",
    ) -> None:
        """Extract sub-fields from a nested relationship object into *props*."""
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
                        field_data = str(nested_value)
                        if instance_url and "id" in key.lower() and "url" in property_name.lower():
                            props[property_name] = f"{instance_url}/{field_data}"
                        else:
                            props[property_name] = field_data
                else:
                    nested_value = parent_object.get(key)
                    if nested_value is not None:
                        field_data = str(nested_value)
                        if instance_url and "id" in key.lower() and "url" in property_name.lower():
                            props[property_name] = f"{instance_url}/{field_data}"
                        else:
                            props[property_name] = field_data
            except Exception as error:  # pragma: no cover - defensive fallback
                logger.error("Could not parse %s.%s: %s", self.object_name, field_key, error)
                props[property_name] = ""

    @staticmethod
    def _serialize_address_object(token: dict[str, Any]) -> str:
        """Serialise a Salesforce compound address dict into a single string."""
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
        record: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Return a list of principal dicts from CreatedBy/LastModifiedBy (no deduplication)."""
        if AUTHORS_SOURCE_PROPERTY not in schema_properties:
            return None

        authors: list[dict[str, Any]] = []

        entries = [
            (
                props.get(CREATED_BY_SOURCE_PROPERTY),
                record.get("CreatedById") if record else None,
            ),
            (
                props.get(LAST_MODIFIED_BY_SOURCE_PROPERTY),
                record.get("LastModifiedById") if record else None,
            ),
        ]

        for name, user_id in entries:
            if not name and not user_id:
                continue
            authors.append(_build_principal_dict(
                str(name) if name else "",
                str(user_id) if user_id else "",
            ))

        return authors if authors else None


def build_handlers_from_config(
    config: dict[str, Any],
    icon_url: str = "",
) -> dict[str, SalesforceObjectHandler]:
    """Create ``SalesforceObjectHandler`` instances from the schema config.

    Parent handlers are created first, then child handlers are attached to
    their respective parents.  Returns a dict keyed by object name.
    """
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
    """Collect the full set of Graph schema property names from all handlers."""
    props: set[str] = {"ObjectName", "url", "IconUrl", "AccountUrl"}
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
        """Initialise the converter with a Salesforce instance URL and schema config."""
        self._instance_url = instance_url
        effective_config = config if config is not None else load_converter_config()
        self._handlers = build_handlers_from_config(effective_config, icon_url=icon_url)
        self._schema_properties = schema_properties if schema_properties is not None else _build_schema_properties(self._handlers)

    @property
    def object_names(self) -> list[str]:
        """List all registered Salesforce object names."""
        return list(self._handlers.keys())

    @property
    def parent_object_names(self) -> list[str]:
        """List object names that are top-level parents (not children)."""
        return [
            object_name
            for object_name, handler in self._handlers.items()
            if handler.parent_object_name is None
        ]

    @property
    def schema_properties(self) -> set[str]:
        """Return a copy of the Graph schema property names."""
        return set(self._schema_properties)

    def get_handler(self, object_name: str) -> SalesforceObjectHandler | None:
        """Return the handler for *object_name*, or ``None`` if not registered."""
        return self._handlers.get(object_name)

    def convert(
        self,
        sf_query_result: dict[str, Any],
        object_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert a Salesforce query result into Graph external-item dicts.

        If *object_name* is ``None`` it is inferred from the first record's
        ``attributes.type``.
        """
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
        """Infer the Salesforce object name from the first record's attributes."""
        records = sf_query_result.get("records", [])
        if not records:
            raise ValueError("Cannot infer object_name from an empty records list")
        attributes = records[0].get("attributes")
        if not isinstance(attributes, dict) or "type" not in attributes:
            raise ValueError("Cannot infer object_name: first record has no attributes.type")
        return str(attributes["type"])