"""
Data classes — simplified stand-ins for framework types:
  - SearchableItem
  - DeletedItem
  - Content
"""

from __future__ import annotations

from typing import Any, Optional


class Content:
    """Mirrors Microsoft.Graph.Connectors.Framework.Model.Content."""

    def __init__(self, parsed_data: str = ""):
        self.parsed_data = parsed_data

    def to_dict(self) -> dict:
        return {"parsedData": self.parsed_data}


class SearchableItem:
    """Mirrors Microsoft.Graph.Connectors.Framework.Model.SearchableItem."""

    def __init__(self, item_id: str):
        self.id = item_id
        self.should_hash_id = False
        self.properties: dict[str, Any] = {}
        self.content: Optional[Content] = None
        self.item_type = "searchable"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "shouldHashId": self.should_hash_id,
            "properties": dict(self.properties),
            "content": self.content.to_dict() if self.content else None,
            "type": self.item_type,
        }


class DeletedItem:
    """Mirrors Microsoft.Graph.Connectors.Framework.Model.DeletedItem."""

    def __init__(self, item_id: str):
        self.id = item_id
        self.item_type = "deleted"

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.item_type}
