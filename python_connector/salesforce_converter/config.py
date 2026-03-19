"""
Configuration loader and handler factory.
"""

from __future__ import annotations

import json

from salesforce_converter.handler import SalesforceObjectHandler


def load_config(path: str) -> dict:
    """Load SalesforceConfiguration.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_handlers_from_config(
    config: dict, icon_url: str = ""
) -> dict[str, SalesforceObjectHandler]:
    """
    Build SalesforceObjectHandler instances from config, wiring parent-child.
    Returns a map of objectName ? handler.
    """
    handlers: dict[str, SalesforceObjectHandler] = {}
    children: list[dict] = []

    # First pass: create handlers for top-level objects (no parentObjectName)
    for obj in config["objectList"]:
        if obj.get("parentObjectName"):
            children.append(obj)
        else:
            handlers[obj["objectName"]] = SalesforceObjectHandler(obj, icon_url=icon_url)

    # Second pass: create child handlers and wire them to parents
    for child_obj in children:
        child_handler = SalesforceObjectHandler(child_obj, icon_url=icon_url)
        parent_name = child_obj["parentObjectName"]
        if parent_name in handlers:
            handlers[parent_name].child_handlers.append(child_handler)
            handlers[parent_name]._child_handler_map[
                child_handler.object_name_as_child
            ] = child_handler
        handlers[child_obj["objectName"]] = child_handler

    return handlers
