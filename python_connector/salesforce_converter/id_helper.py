"""
ItemIdConstructionHelper + IdGenerator
Mirrors:
  - SalesforceCommon/Helper/ItemIdConstructionHelper.cs
  - Common/Utils.Standard/IdGenerator.cs
"""

import hashlib

from salesforce_converter.constants import RECORD_ID_LENGTH


def generate_alphanumeric_128char_hash(id_value: str) -> str:
    """
    Mirrors IdGenerator.GenerateAlphaNumeric128charHash.
    SHA-512 of UTF-16LE bytes ? uppercase hex ? no dashes ? 128 chars.
    """
    encoded = id_value.encode("utf-16-le")
    sha512_bytes = hashlib.sha512(encoded).digest()
    hex_str = "".join(f"{b:02X}" for b in sha512_bytes)
    return hex_str[:128]


def construct_item_id_without_hashing(item_id: str) -> str:
    """
    Mirrors ItemIdConstructionHelper.ConstructItemIdWithoutHashing.
    Truncates to first 15 characters (RecordIdLength).
    """
    if not item_id or len(item_id) < RECORD_ID_LENGTH:
        raise ValueError(f"Invalid itemId: {item_id!r}")
    return item_id[:RECORD_ID_LENGTH]


def construct_item_id_with_hashing(item_id: str) -> str:
    """
    Mirrors ItemIdConstructionHelper.ConstructItemIdWithHashing.
    SHA-512 hash of the truncated ID.
    """
    truncated = construct_item_id_without_hashing(item_id)
    return generate_alphanumeric_128char_hash(truncated)
