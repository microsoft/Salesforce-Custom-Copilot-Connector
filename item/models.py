"""
Data models for Microsoft Graph external items.

These lightweight classes mirror the Graph ``externalItem`` JSON structure and
provide a ``to_dict()`` method for serialisation.

Classes
-------
Content
    Wraps the ``parsedData`` text body that Microsoft Search indexes for
    full-text queries.

AccessControlEntry
    A single ACL grant or deny entry (``accessType``, ``type``, ``value``).

SearchableItem
    A full external item: ID, properties dict, optional content, optional ACL
    list, and a ``type`` of ``"searchable"``.  Converted to JSON via
    ``to_dict()`` before being PUT to Graph.

DeletedItem
    Represents a Salesforce record whose ``IsDeleted`` flag is ``True``.
    Converted to ``{"id": ..., "type": "deleted"}`` so the ingestion pipeline
    issues a DELETE instead of a PUT.
"""

from __future__ import annotations

from typing import Any, Optional


class Content:
    def __init__(self, parsed_data: str = ""):
        self.parsed_data = parsed_data

    def to_dict(self) -> dict[str, Any]:
        return {"parsedData": self.parsed_data}


class AccessControlEntry:
    def __init__(self, access_type: str, principal_type: str, value: str):
        self.access_type = access_type
        self.principal_type = principal_type
        self.value = value

    def to_dict(self) -> dict[str, str]:
        return {
            "accessType": self.access_type,
            "type": self.principal_type,
            "value": self.value,
        }


class SearchableItem:
    def __init__(self, item_id: str):
        self.id = item_id
        self.should_hash_id = False
        self.properties: dict[str, Any] = {}
        self.content: Optional[Content] = None
        self.acl: list[AccessControlEntry] | None = None
        self.item_type = "searchable"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "properties": dict(self.properties),
            "content": self.content.to_dict() if self.content else None,
            "type": self.item_type,
        }
        if self.should_hash_id:
            result["shouldHashId"] = True
        if self.acl is not None:
            result["acl"] = [entry.to_dict() for entry in self.acl]
        return result


class DeletedItem:
    def __init__(self, item_id: str):
        self.id = item_id
        self.item_type = "deleted"

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "type": self.item_type}