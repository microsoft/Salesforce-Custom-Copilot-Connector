"""Salesforce Record → SearchableItem Converter Package."""

from salesforce_converter.converter import SalesforceConverter
from salesforce_converter.handler import SalesforceObjectHandler
from salesforce_converter.config import load_config, build_handlers_from_config
from salesforce_converter.models import SearchableItem, DeletedItem, Content
from salesforce_converter.id_helper import (
    construct_item_id_without_hashing,
    construct_item_id_with_hashing,
    generate_alphanumeric_128char_hash,
)

__all__ = [
    "SalesforceConverter",
    "SalesforceObjectHandler",
    "load_config",
    "build_handlers_from_config",
    "SearchableItem",
    "DeletedItem",
    "Content",
    "construct_item_id_without_hashing",
    "construct_item_id_with_hashing",
    "generate_alphanumeric_128char_hash",
]
