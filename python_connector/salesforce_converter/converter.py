"""
SalesforceConverter — single entry-point facade for clients.

Usage:
    from salesforce_converter import SalesforceConverter

    converter = SalesforceConverter(instance_url)
    items = converter.convert(sf_query_result)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from salesforce_converter.config import build_handlers_from_config
from salesforce_converter.constants import (
    AUTHORS_SOURCE_PROPERTY,
    METADATA_COLUMN_SCHEMA_MAPPING,
    SYSTEM_CREATED_BY_USER_ID,
    SYSTEM_MODIFIED_BY_USER_ID,
)
from salesforce_converter.handler import SalesforceObjectHandler

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "SalesforceConfiguration.json"


def _load_default_config() -> dict:
    with _DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_schema_properties(handlers: dict[str, SalesforceObjectHandler]) -> set[str]:
    """Derive the full set of possible property names from config + constants."""
    props: set[str] = set()

    # Always-emitted hardcoded properties
    props.update({"ObjectName", "Url", "IconUrl", "AccountUrl"})

    # Metadata mapping values
    props.update(METADATA_COLUMN_SCHEMA_MAPPING.values())

    # System properties + Authors
    props.update({AUTHORS_SOURCE_PROPERTY, SYSTEM_CREATED_BY_USER_ID, SYSTEM_MODIFIED_BY_USER_ID})

    # All selectedFields values from every handler
    for handler in handlers.values():
        props.update(handler.selected_fields.values())

    return props


class SalesforceConverter:
    """
    Facade that hides handler wiring and schema plumbing.

    Init once with config + instance_url, then call convert() per SOQL response.
    """

    def __init__(
        self,
        instance_url: str,
        config: Optional[dict[str, Any]] = None,
        schema_properties: Optional[set[str]] = None,
        icon_url: str = "",
    ):
        self._instance_url = instance_url
        effective_config = config if config is not None else _load_default_config()
        self._handlers: dict[str, SalesforceObjectHandler] = build_handlers_from_config(
            effective_config, icon_url=icon_url
        )
        self._schema_properties = (
            schema_properties if schema_properties is not None
            else _build_schema_properties(self._handlers)
        )

    @property
    def object_names(self) -> list[str]:
        """Registered object names (e.g. ['Account', 'Contact', ...])."""
        return list(self._handlers.keys())

    @property
    def parent_object_names(self) -> list[str]:
        """Only top-level (non-child) object names."""
        return [
            name for name, h in self._handlers.items()
            if h.parent_object_name is None
        ]

    @property
    def schema_properties(self) -> set[str]:
        """The schema property set (inferred or explicitly provided)."""
        return set(self._schema_properties)

    def convert(
        self,
        sf_query_result: dict,
        object_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Convert a Salesforce SOQL response into ingestion-ready items.

        Args:
            sf_query_result: Raw Salesforce JSON with a "records" list.
            object_name:     Optional. If omitted, inferred from the first
                             record's attributes.type field.

        Returns:
            List of item dicts (searchable or deleted), ready for
            Graph connector PUT /items/{itemId}.
        """
        if object_name is None:
            object_name = self._infer_object_name(sf_query_result)

        handler = self._handlers.get(object_name)
        if handler is None:
            raise ValueError(
                f"Unknown object '{object_name}'. "
                f"Available: {self.object_names}"
            )
        return handler.construct_ingestion_items(
            sf_query_result, self._instance_url, self._schema_properties
        )

    @staticmethod
    def _infer_object_name(sf_query_result: dict) -> str:
        records = sf_query_result.get("records", [])
        if not records:
            raise ValueError("Cannot infer object_name from an empty records list")
        attrs = records[0].get("attributes")
        if not isinstance(attrs, dict) or "type" not in attrs:
            raise ValueError(
                "Cannot infer object_name: first record has no attributes.type"
            )
        return attrs["type"]
