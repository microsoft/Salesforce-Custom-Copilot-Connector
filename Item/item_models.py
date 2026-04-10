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