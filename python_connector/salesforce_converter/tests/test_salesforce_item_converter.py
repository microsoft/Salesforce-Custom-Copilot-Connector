"""
Comprehensive tests for the salesforce_converter package.
Verifies parity with SalesforceObjectHandler.cs logic.

Run: pytest salesforce_converter/tests/ -v
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from salesforce_converter.config import build_handlers_from_config
from salesforce_converter.constants import (
    AUTHORS_SOURCE_PROPERTY,
    CONTENT_FIELD_NAME,
    CREATED_BY_SOURCE_PROPERTY,
    LAST_MODIFIED_BY_SOURCE_PROPERTY,
    RECORD_ID_LENGTH,
    SYSTEM_CREATED_BY_USER_ID,
    SYSTEM_MODIFIED_BY_USER_ID,
)
from salesforce_converter.converter import SalesforceConverter
from salesforce_converter.handler import SalesforceObjectHandler, _convert_value, _resolve_type
from salesforce_converter.id_helper import (
    construct_item_id_with_hashing,
    construct_item_id_without_hashing,
    generate_alphanumeric_128char_hash,
)
from salesforce_converter.models import Content, DeletedItem, SearchableItem

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

INSTANCE_URL = "https://ap15.salesforce.com"

ACCOUNT_CONFIG = {
    "objectName": "Account",
    "selectedFields": {
        "AccountNumber": "AccountNumber",
        "Site": "Site",
        "BillingAddress": "BillingAddress",
        "Industry": "Industry",
        "ShippingAddress": "ShippingAddress",
        "TickerSymbol": "TickerSymbol",
        "Website": "Website",
        "Name": "Name",
        "Description": "Description",
        "Fax": "Fax",
        "Phone": "Phone",
        "Type": "Type",
    },
    "SfColumnTypes": {},
    "filterCondition": "",
    "iconUrl": "",
}

CONTACT_CONFIG = {
    "objectName": "Contact",
    "selectedFields": {
        "Name": "Name",
        "Description": "Description",
        "Account.Id": "AccountId",
        "Account.Name": "AccountName",
        "Account.OwnerId": "AccountOwnerUrl",
        "Account.Owner.Name": "AccountOwner",
        "Email": "Email",
    },
    "SfColumnTypes": {},
    "parentObjectName": "Account",
    "objectNameAsChild": "Contacts",
    "iconUrl": "",
}

OPPORTUNITY_CONFIG = {
    "objectName": "Opportunity",
    "selectedFields": {
        "Name": "Name",
        "Amount": "Amount",
        "CloseDate": "CloseDate",
        "Description": "Description",
    },
    "SfColumnTypes": {
        "Amount": "System.Double, mscorlib",
        "CloseDate": "System.DateTime, mscorlib",
    },
}

LEAD_CONFIG = {
    "objectName": "Lead",
    "selectedFields": {
        "Name": "Name",
        "IsConverted": "IsConverted",
    },
    "SfColumnTypes": {
        "IsConverted": "System.Boolean, mscorlib",
    },
}

ALL_SCHEMA_PROPS = {
    "ObjectName", "Url", "AccountNumber", "Site", "BillingAddress", "Industry",
    "ShippingAddress", "TickerSymbol", "Website", "Name", "Description", "Fax",
    "Phone", "Type", "CreatedDate", "LastModifiedDate", "LastModifiedBy",
    "LastModifiedByUrl", "CreatedBy", "CreatedByUrl", "Owner", "OwnerUrl", "Id",
    AUTHORS_SOURCE_PROPERTY, "AccountId", "AccountName", "AccountOwnerUrl",
    "AccountOwner", "Email", "Amount", "CloseDate", "IsConverted", "IconUrl",
    SYSTEM_CREATED_BY_USER_ID, SYSTEM_MODIFIED_BY_USER_ID,
}

_BASE_METADATA = {
    "IsDeleted": False,
    "OwnerId": "0055w00000CkeZ1AAJ",
    "Owner": {"Name": "Rohit Sharma"},
    "CreatedById": "0052v00000B8o7AAAR",
    "CreatedBy": {"Name": "John Doe"},
    "CreatedDate": "2019-06-14T17:35:22.000+0000",
    "LastModifiedById": "0055w00000CkeZ1AAJ",
    "LastModifiedBy": {"Name": "Rohit Sharma"},
    "LastModifiedDate": "2025-03-10T09:22:15.000+0000",
}

SAMPLE_ACCOUNT_RECORD = {
    "attributes": {"type": "Account"},
    "Id": "0012v00002RkkJnAAJ",
    "Name": "GenePoint",
    "Description": "Genomics company",
    "AccountNumber": "CC978213",
    "BillingAddress": {
        "street": "345 Shoreline Park", "city": "Mountain View",
        "state": "CA", "postalCode": "94043", "country": "US",
    },
    "ShippingAddress": None,
    "Industry": "Biotechnology",
    "TickerSymbol": "GENE",
    "Website": "www.genepoint.com",
    "Fax": "(650) 867-3450",
    "Phone": "(650) 867-3450",
    "Site": "Single Location",
    "Type": "Customer - Channel",
    **_BASE_METADATA,
}


def _make_contact_record(cid: str, name: str, email: str, acct_id: str = "001X", acct_name: str = "G"):
    return {
        "Id": cid, "Name": name, "Description": f"Desc for {name}",
        "Email": email,
        "Account": {"Id": acct_id, "Name": acct_name, "OwnerId": "005O", "Owner": {"Name": "R"}},
        **_BASE_METADATA,
    }


# ===========================================================================
# Test: ItemIdConstructionHelper
# ===========================================================================

class TestItemIdConstruction:
    def test_truncates_to_15_chars(self):
        assert construct_item_id_without_hashing("0012v00002RkkJnAAJ") == "0012v00002RkkJn"

    def test_exact_15_chars(self):
        assert construct_item_id_without_hashing("0012v00002RkkJn") == "0012v00002RkkJn"

    def test_raises_for_short_id(self):
        with pytest.raises(ValueError):
            construct_item_id_without_hashing("short")

    def test_raises_for_empty(self):
        with pytest.raises(ValueError):
            construct_item_id_without_hashing("")

    def test_raises_for_none(self):
        with pytest.raises(ValueError):
            construct_item_id_without_hashing(None)

    def test_hash_is_128_chars_uppercase_hex(self):
        result = construct_item_id_with_hashing("0012v00002RkkJnAAJ")
        assert len(result) == 128
        assert result == result.upper()
        assert all(c in "0123456789ABCDEF" for c in result)


# ===========================================================================
# Test: IdGenerator SHA-512 parity with C#
# ===========================================================================

class TestIdGenerator:
    def test_hash_deterministic(self):
        assert generate_alphanumeric_128char_hash("test") == generate_alphanumeric_128char_hash("test")

    def test_hash_different_inputs(self):
        assert generate_alphanumeric_128char_hash("abc") != generate_alphanumeric_128char_hash("def")

    def test_hash_length(self):
        assert len(generate_alphanumeric_128char_hash("anything")) == 128

    def test_hash_uses_utf16le(self):
        test_str = "0012v00002RkkJn"
        encoded = test_str.encode("utf-16-le")
        expected = hashlib.sha512(encoded).digest()
        expected_hex = "".join(f"{b:02X}" for b in expected)[:128]
        assert generate_alphanumeric_128char_hash(test_str) == expected_hex


# ===========================================================================
# Test: Type conversion
# ===========================================================================

class TestTypeConversion:
    def test_bool_true_string(self):
        assert _convert_value("true", "bool") is True

    def test_bool_false_string(self):
        assert _convert_value("false", "bool") is False

    def test_bool_native(self):
        assert _convert_value(True, "bool") is True
        assert _convert_value(False, "bool") is False

    def test_float(self):
        assert _convert_value("50000.5", "float") == 50000.5

    def test_int(self):
        assert _convert_value("42", "int") == 42

    def test_none_returns_none(self):
        assert _convert_value(None, "bool") is None


# ===========================================================================
# Test: Address serialization
# ===========================================================================

class TestSerializeAddress:
    def test_full_address(self):
        addr = {"street": "123 Main", "city": "Seattle", "state": "WA", "postalCode": "98101", "country": "US"}
        assert SalesforceObjectHandler._serialize_address_object(addr) == "123 Main, Seattle, WA - 98101, US"

    def test_empty_address(self):
        addr = {"street": None, "city": None, "state": None, "postalCode": None, "country": None}
        assert SalesforceObjectHandler._serialize_address_object(addr) == ""

    def test_only_street(self):
        assert SalesforceObjectHandler._serialize_address_object(
            {"street": "1 Rd", "city": None, "state": None, "postalCode": None, "country": None}
        ) == "1 Rd"


# ===========================================================================
# Test: Authors
# ===========================================================================

class TestAuthors:
    def test_deduplicated(self):
        props = {CREATED_BY_SOURCE_PROPERTY: "Alice", LAST_MODIFIED_BY_SOURCE_PROPERTY: "Alice"}
        assert SalesforceObjectHandler._get_authors_source_property(props, {AUTHORS_SOURCE_PROPERTY}) == ["Alice"]

    def test_both_different(self):
        props = {CREATED_BY_SOURCE_PROPERTY: "Alice", LAST_MODIFIED_BY_SOURCE_PROPERTY: "Bob"}
        assert set(SalesforceObjectHandler._get_authors_source_property(props, {AUTHORS_SOURCE_PROPERTY})) == {"Alice", "Bob"}

    def test_not_in_schema(self):
        assert SalesforceObjectHandler._get_authors_source_property({CREATED_BY_SOURCE_PROPERTY: "A"}, {"Other"}) is None


# ===========================================================================
# Test: Basic property mapping (Account)
# ===========================================================================

class TestAccountPropertyMapping:
    @pytest.fixture
    def items(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        return handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)

    def test_returns_one_item(self, items):
        assert len(items) == 1

    def test_item_id_truncated(self, items):
        assert items[0]["id"] == "0012v00002RkkJn"

    def test_object_name(self, items):
        assert items[0]["properties"]["ObjectName"] == "Account"

    def test_billing_address_serialized(self, items):
        assert items[0]["properties"]["BillingAddress"] == "345 Shoreline Park, Mountain View, CA - 94043, US"

    def test_null_shipping_address(self, items):
        assert items[0]["properties"]["ShippingAddress"] is None

    def test_content(self, items):
        assert items[0]["content"]["parsedData"] == "Genomics company"

    def test_owner_from_metadata_object(self, items):
        assert items[0]["properties"]["Owner"] == "Rohit Sharma"

    def test_authors(self, items):
        assert set(items[0]["properties"]["Authors"]) == {"John Doe", "Rohit Sharma"}


# ===========================================================================
# Test: Deleted record
# ===========================================================================

class TestDeletedRecord:
    def test_deleted_item(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "IsDeleted": True}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert len(items) == 1
        assert items[0]["type"] == "deleted"


# ===========================================================================
# Test: Missing ID
# ===========================================================================

class TestMissingId:
    def test_null_id_skipped(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "Id": None}
        assert handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS) == []

    def test_empty_records(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        assert handler.construct_ingestion_items({"records": []}, INSTANCE_URL, ALL_SCHEMA_PROPS) == []


# ===========================================================================
# Test: Typed fields
# ===========================================================================

class TestTypedFields:
    def test_double_and_datetime(self):
        handler = SalesforceObjectHandler(OPPORTUNITY_CONFIG)
        record = {"Id": "006AAAAAAAAAAAAAAA", "Name": "Deal", "Amount": 50000.5,
                   "CloseDate": "2025-12-31", "Description": "Big", **_BASE_METADATA}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["Amount"] == 50000.5
        assert isinstance(items[0]["properties"]["Amount"], float)

    def test_boolean_field(self):
        handler = SalesforceObjectHandler(LEAD_CONFIG)
        record = {"Id": "00QAAAAAAAAAAAAAL", "Name": "Lead", "IsConverted": False, **_BASE_METADATA}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["IsConverted"] is False


# ===========================================================================
# Test: Object-type fields (Contact ? Account.Owner.Name)
# ===========================================================================

class TestObjectTypeFields:
    def test_nested_object_traversal(self):
        handler = SalesforceObjectHandler(CONTACT_CONFIG)
        record = _make_contact_record("003AAAAAAAAAAAAAAQ", "Edna", "edna@g.com", "001X", "GenePoint")
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        props = items[0]["properties"]
        assert props["AccountId"] == "001X"
        assert props["AccountName"] == "GenePoint"
        assert props["AccountOwnerUrl"] == "005O"
        assert props["AccountOwner"] == "R"
        assert props["AccountUrl"] == f"{INSTANCE_URL}/001X"


# ===========================================================================
# Test: Parent-child — single parent, single child
# ===========================================================================

class TestParentChildSingle:
    def test_ordering_child_before_parent(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])
        record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Contacts": {"totalSize": 1, "done": True, "records": [
                _make_contact_record("003AAAAAAAAAAAAAAQ", "Edna", "e@g.com"),
            ]},
        }
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert len(items) == 2
        assert items[0]["properties"]["ObjectName"] == "Contact"
        assert items[1]["properties"]["ObjectName"] == "Account"


# ===========================================================================
# Test: Parent-child — single parent, multiple children
# ===========================================================================

class TestParentChildMultipleChildren:
    def test_multiple_contacts_per_account(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])
        record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Contacts": {"totalSize": 3, "done": True, "records": [
                _make_contact_record("003AAAAAAAAAAAAAAQ", "Alice", "a@g.com"),
                _make_contact_record("003BBBBBBBBBBBBBBB", "Bob", "b@g.com"),
                _make_contact_record("003CCCCCCCCCCCCCCC", "Charlie", "c@g.com"),
            ]},
        }
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert len(items) == 4
        assert [i["properties"]["ObjectName"] for i in items] == ["Contact", "Contact", "Contact", "Account"]
        assert items[0]["properties"]["Name"] == "Alice"
        assert items[1]["properties"]["Name"] == "Bob"
        assert items[2]["properties"]["Name"] == "Charlie"
        assert items[3]["properties"]["Name"] == "GenePoint"


# ===========================================================================
# Test: Multiple parent records, each with children
# ===========================================================================

class TestMultipleParentsWithChildren:
    def test_two_accounts_with_contacts(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])

        acct1 = {
            **SAMPLE_ACCOUNT_RECORD,
            "Id": "001AAAAAAAAAAAAAAA", "Name": "Acme",
            "Contacts": {"totalSize": 2, "done": True, "records": [
                _make_contact_record("003AAAAAAAAAAAAAAQ", "Alice", "a@acme.com"),
                _make_contact_record("003BBBBBBBBBBBBBBB", "Bob", "b@acme.com"),
            ]},
        }
        acct2 = {
            **SAMPLE_ACCOUNT_RECORD,
            "Id": "001DDDDDDDDDDDDDDD", "Name": "Globex",
            "Contacts": {"totalSize": 1, "done": True, "records": [
                _make_contact_record("003EEEEEEEEEEEEEEE", "Charlie", "c@globex.com"),
            ]},
        }

        items = handler.construct_ingestion_items(
            {"records": [acct1, acct2]}, INSTANCE_URL, ALL_SCHEMA_PROPS
        )

        assert len(items) == 5
        types = [i["properties"]["ObjectName"] for i in items]
        assert types == ["Contact", "Contact", "Account", "Contact", "Account"]

        assert items[0]["properties"]["Name"] == "Alice"
        assert items[1]["properties"]["Name"] == "Bob"
        assert items[2]["properties"]["Name"] == "Acme"
        assert items[3]["properties"]["Name"] == "Charlie"
        assert items[4]["properties"]["Name"] == "Globex"

        assert items[0]["id"] == "003AAAAAAAAAAAA"
        assert items[2]["id"] == "001AAAAAAAAAAAA"
        assert items[4]["id"] == "001DDDDDDDDDDDD"


# ===========================================================================
# Test: Multiple parents, some with no children, some deleted
# ===========================================================================

class TestMixedRecordsBatch:
    def test_mixed_deleted_and_live_with_children(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])

        live_with_children = {
            **SAMPLE_ACCOUNT_RECORD,
            "Id": "001AAAAAAAAAAAAAAA", "Name": "Acme",
            "Contacts": {"totalSize": 1, "done": True, "records": [
                _make_contact_record("003AAAAAAAAAAAAAAQ", "Alice", "a@acme.com"),
            ]},
        }
        deleted_record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Id": "001BBBBBBBBBBBBBBB", "Name": "Defunct", "IsDeleted": True,
        }
        live_no_children = {
            **SAMPLE_ACCOUNT_RECORD,
            "Id": "001CCCCCCCCCCCCCCC", "Name": "Solo",
        }

        items = handler.construct_ingestion_items(
            {"records": [live_with_children, deleted_record, live_no_children]},
            INSTANCE_URL, ALL_SCHEMA_PROPS,
        )

        assert len(items) == 4

        assert items[0]["type"] == "searchable"
        assert items[0]["properties"]["ObjectName"] == "Contact"

        assert items[1]["type"] == "searchable"
        assert items[1]["properties"]["ObjectName"] == "Account"
        assert items[1]["properties"]["Name"] == "Acme"

        assert items[2]["type"] == "deleted"
        assert items[2]["id"] == "001BBBBBBBBBBBB"

        assert items[3]["type"] == "searchable"
        assert items[3]["properties"]["Name"] == "Solo"


# ===========================================================================
# Test: Parent with empty child list
# ===========================================================================

class TestEmptyChildList:
    def test_no_child_records(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])
        record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Contacts": {"totalSize": 0, "done": True, "records": []},
        }
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert len(items) == 1
        assert items[0]["properties"]["ObjectName"] == "Account"


# ===========================================================================
# Test: FLS fields
# ===========================================================================

class TestFLSFields:
    def test_fls_fields_set_to_none(self):
        config = {**ACCOUNT_CONFIG, "flsFields": ["Industry", "Website"]}
        handler = SalesforceObjectHandler(config)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["Industry"] is None
        assert items[0]["properties"]["Website"] is None


# ===========================================================================
# Test: System properties
# ===========================================================================

class TestSystemProperties:
    def test_system_properties_mapped(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"][SYSTEM_CREATED_BY_USER_ID] == "0052v00000B8o7AAAR"
        assert items[0]["properties"][SYSTEM_MODIFIED_BY_USER_ID] == "0055w00000CkeZ1AAJ"


# ===========================================================================
# Test: Schema filtering
# ===========================================================================

class TestSchemaFiltering:
    def test_only_schema_properties_emitted(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        limited = {"ObjectName", "Url", "Name", "Id"}
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, limited)
        props = items[0]["properties"]
        assert "Name" in props
        assert "AccountNumber" not in props
        assert "BillingAddress" not in props


# ===========================================================================
# Test: build_handlers_from_config
# ===========================================================================

class TestBuildHandlers:
    def test_config_wiring(self):
        config = {"objectList": [ACCOUNT_CONFIG, CONTACT_CONFIG, OPPORTUNITY_CONFIG]}
        handlers = build_handlers_from_config(config)
        assert "Account" in handlers
        assert "Contact" in handlers
        assert len(handlers["Account"].child_handlers) == 1
        assert handlers["Account"].child_handlers[0].object_name == "Contact"
        assert "Contacts" in handlers["Account"]._child_handler_map

    def test_icon_url_passthrough(self):
        config = {"objectList": [ACCOUNT_CONFIG]}
        handlers = build_handlers_from_config(config, icon_url="https://cdn.example.com/icon.png")
        assert handlers["Account"].icon_url == "https://cdn.example.com/icon.png"

    def test_multiple_children_wired(self):
        opp_child = {
            "objectName": "Opportunity",
            "selectedFields": {"Name": "Name"},
            "SfColumnTypes": {},
            "parentObjectName": "Account",
            "objectNameAsChild": "Opportunities",
        }
        config = {"objectList": [ACCOUNT_CONFIG, CONTACT_CONFIG, opp_child]}
        handlers = build_handlers_from_config(config)
        assert len(handlers["Account"].child_handlers) == 2
        assert "Contacts" in handlers["Account"]._child_handler_map
        assert "Opportunities" in handlers["Account"]._child_handler_map

    def test_orphan_child_still_created(self):
        config = {"objectList": [CONTACT_CONFIG]}
        handlers = build_handlers_from_config(config)
        assert "Contact" in handlers


# ===========================================================================
# Test: Model classes
# ===========================================================================

class TestModels:
    def test_content_to_dict(self):
        c = Content("hello world")
        assert c.to_dict() == {"parsedData": "hello world"}

    def test_content_empty(self):
        c = Content()
        assert c.to_dict() == {"parsedData": ""}

    def test_searchable_item_to_dict(self):
        item = SearchableItem("abc123")
        item.properties["Name"] = "Test"
        item.content = Content("body")
        d = item.to_dict()
        assert d["id"] == "abc123"
        assert d["shouldHashId"] is False
        assert d["properties"]["Name"] == "Test"
        assert d["content"]["parsedData"] == "body"
        assert d["type"] == "searchable"

    def test_searchable_item_no_content(self):
        item = SearchableItem("x")
        d = item.to_dict()
        assert d["content"] is None

    def test_deleted_item_to_dict(self):
        item = DeletedItem("del123")
        d = item.to_dict()
        assert d == {"id": "del123", "type": "deleted"}


# ===========================================================================
# Test: _resolve_type
# ===========================================================================

class TestResolveType:
    def test_system_boolean(self):
        assert _resolve_type("System.Boolean, mscorlib") == "bool"

    def test_system_double(self):
        assert _resolve_type("System.Double, mscorlib") == "float"

    def test_system_datetime(self):
        assert _resolve_type("System.DateTime, mscorlib") == "datetime"

    def test_system_int32(self):
        assert _resolve_type("System.Int32, mscorlib") == "int"

    def test_system_int64(self):
        assert _resolve_type("System.Int64, mscorlib") == "int"

    def test_system_string(self):
        assert _resolve_type("System.String, mscorlib") == "str"

    def test_unknown_type_returns_none(self):
        assert _resolve_type("System.Guid, mscorlib") is None

    def test_empty_string_returns_none(self):
        assert _resolve_type("") is None

    def test_none_returns_none(self):
        assert _resolve_type(None) is None


# ===========================================================================
# Test: _convert_value edge cases
# ===========================================================================

class TestConvertValueEdgeCases:
    def test_string_type_tag(self):
        assert _convert_value(42, "str") == "42"

    def test_bool_from_integer(self):
        assert _convert_value(1, "bool") is True
        assert _convert_value(0, "bool") is False

    def test_float_from_int(self):
        assert _convert_value(10, "float") == 10.0
        assert isinstance(_convert_value(10, "float"), float)

    def test_int_from_float_string(self):
        assert _convert_value("42", "int") == 42


# ===========================================================================
# Test: IconUrl property
# ===========================================================================

class TestIconUrl:
    def test_icon_url_in_schema(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, icon_url="https://cdn.example.com/icon.png")
        items = handler.construct_ingestion_items(
            {"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS
        )
        assert items[0]["properties"]["IconUrl"] == "https://cdn.example.com/icon.png"

    def test_icon_url_not_in_schema(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, icon_url="https://cdn.example.com/icon.png")
        schema_no_icon = ALL_SCHEMA_PROPS - {"IconUrl"}
        items = handler.construct_ingestion_items(
            {"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, schema_no_icon
        )
        assert "IconUrl" not in items[0]["properties"]


# ===========================================================================
# Test: Content edge cases
# ===========================================================================

class TestContentExtraction:
    def test_description_none(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "Description": None}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["content"]["parsedData"] == ""

    def test_description_empty_string(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "Description": ""}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["content"]["parsedData"] == ""

    def test_no_description_field(self):
        config = {
            "objectName": "Lead",
            "selectedFields": {"Name": "Name"},
            "SfColumnTypes": {},
        }
        handler = SalesforceObjectHandler(config)
        record = {"Id": "00QAAAAAAAAAAAAAL", "Name": "Test", **_BASE_METADATA}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["content"]["parsedData"] == ""


# ===========================================================================
# Test: URL construction for Id fields
# ===========================================================================

class TestUrlConstruction:
    def test_url_property(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["Url"] == f"{INSTANCE_URL}/0012v00002RkkJnAAJ"

    def test_owner_url_from_metadata(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["OwnerUrl"] == f"{INSTANCE_URL}/0055w00000CkeZ1AAJ"

    def test_created_by_url(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["CreatedByUrl"] == f"{INSTANCE_URL}/0052v00000B8o7AAAR"

    def test_last_modified_by_url(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["LastModifiedByUrl"] == f"{INSTANCE_URL}/0055w00000CkeZ1AAJ"


# ===========================================================================
# Test: shouldHashId flag
# ===========================================================================

class TestShouldHashId:
    def test_searchable_item_has_hash_flag(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["shouldHashId"] is True

    def test_deleted_item_has_no_hash_flag(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "IsDeleted": True}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert "shouldHashId" not in items[0]


# ===========================================================================
# Test: Address serialization additional cases
# ===========================================================================

class TestSerializeAddressExtended:
    def test_city_state_only(self):
        addr = {"street": None, "city": "Seattle", "state": "WA", "postalCode": None, "country": None}
        assert SalesforceObjectHandler._serialize_address_object(addr) == ", Seattle, WA"

    def test_street_and_country_only(self):
        addr = {"street": "1 Main St", "city": None, "state": None, "postalCode": None, "country": "US"}
        assert SalesforceObjectHandler._serialize_address_object(addr) == "1 Main St, US"

    def test_postal_code_only(self):
        addr = {"street": None, "city": None, "state": None, "postalCode": "12345", "country": None}
        assert SalesforceObjectHandler._serialize_address_object(addr) == " - 12345"


# ===========================================================================
# Test: Error handling in type conversion (fallback defaults)
# ===========================================================================

class TestTypeConversionFallback:
    def test_invalid_float_uses_default(self):
        config = {
            "objectName": "Opp",
            "selectedFields": {"Amount": "Amount"},
            "SfColumnTypes": {"Amount": "System.Double, mscorlib"},
        }
        handler = SalesforceObjectHandler(config)
        record = {"Id": "006AAAAAAAAAAAAAAA", "Amount": "not_a_number", **_BASE_METADATA}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, {"ObjectName", "Url", "Amount"})
        assert items[0]["properties"]["Amount"] == 0.0

    def test_invalid_int_uses_default(self):
        config = {
            "objectName": "Opp",
            "selectedFields": {"Count": "Count"},
            "SfColumnTypes": {"Count": "System.Int32, mscorlib"},
        }
        handler = SalesforceObjectHandler(config)
        record = {"Id": "006AAAAAAAAAAAAAAA", "Count": "abc", **_BASE_METADATA}
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, {"ObjectName", "Url", "Count"})
        assert items[0]["properties"]["Count"] == 0


# ===========================================================================
# Test: Authors edge cases
# ===========================================================================

class TestAuthorsExtended:
    def test_only_created_by(self):
        props = {CREATED_BY_SOURCE_PROPERTY: "Alice"}
        result = SalesforceObjectHandler._get_authors_source_property(props, {AUTHORS_SOURCE_PROPERTY})
        assert result == ["Alice"]

    def test_only_last_modified_by(self):
        props = {LAST_MODIFIED_BY_SOURCE_PROPERTY: "Bob"}
        result = SalesforceObjectHandler._get_authors_source_property(props, {AUTHORS_SOURCE_PROPERTY})
        assert result == ["Bob"]

    def test_empty_props(self):
        result = SalesforceObjectHandler._get_authors_source_property({}, {AUTHORS_SOURCE_PROPERTY})
        assert result is None


# ===========================================================================
# Test: Deleted child records
# ===========================================================================

class TestDeletedChildRecord:
    def test_deleted_child_produces_deleted_item(self):
        child_handler = SalesforceObjectHandler(CONTACT_CONFIG)
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG, child_handlers=[child_handler])
        deleted_contact = {**_make_contact_record("003DDDDDDDDDDDDDDD", "Deleted", "d@x.com"), "IsDeleted": True}
        record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Contacts": {"totalSize": 1, "done": True, "records": [deleted_contact]},
        }
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert len(items) == 2
        assert items[0]["type"] == "deleted"
        assert items[0]["id"] == "003DDDDDDDDDDDD"
        assert items[1]["type"] == "searchable"
        assert items[1]["properties"]["ObjectName"] == "Account"


# ===========================================================================
# Test: AccountUrl derived property
# ===========================================================================

class TestAccountUrlDerived:
    def test_account_url_derived_from_account_id(self):
        handler = SalesforceObjectHandler(CONTACT_CONFIG)
        record = _make_contact_record("003AAAAAAAAAAAAAAQ", "Edna", "e@g.com", "001X000000AAAAAA", "GenePoint")
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        props = items[0]["properties"]
        assert props["AccountUrl"] == f"{INSTANCE_URL}/001X000000AAAAAA"


# ===========================================================================
# Test: Object field with None parent object
# ===========================================================================

class TestObjectFieldNoneParent:
    def test_null_account_object(self):
        handler = SalesforceObjectHandler(CONTACT_CONFIG)
        record = {
            "Id": "003AAAAAAAAAAAAAAQ", "Name": "Edna", "Description": "Test",
            "Email": "e@g.com", "Account": None,
            **_BASE_METADATA,
        }
        items = handler.construct_ingestion_items({"records": [record]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        props = items[0]["properties"]
        assert "AccountId" not in props
        assert "AccountName" not in props


# ===========================================================================
# Test: Metadata dates are string-passed (typed as datetime)
# ===========================================================================

class TestMetadataDateFields:
    def test_created_date_passed_through(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["CreatedDate"] == "2019-06-14T17:35:22.000+0000"

    def test_last_modified_date_passed_through(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({"records": [SAMPLE_ACCOUNT_RECORD]}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items[0]["properties"]["LastModifiedDate"] == "2025-03-10T09:22:15.000+0000"


# ===========================================================================
# Test: Handler initialization edge cases
# ===========================================================================

class TestHandlerInit:
    def test_field_data_types_parsed(self):
        handler = SalesforceObjectHandler(OPPORTUNITY_CONFIG)
        assert handler.field_data_types["Amount"] == "float"
        assert handler.field_data_types["CloseDate"] == "datetime"
        assert handler.field_data_types["LastModifiedDate"] == "datetime"
        assert handler.field_data_types["CreatedDate"] == "datetime"

    def test_object_fields_parsed(self):
        handler = SalesforceObjectHandler(CONTACT_CONFIG)
        assert "Account" in handler.object_fields
        assert set(handler.object_fields["Account"]) == {"Id", "Name", "OwnerId", "Owner.Name"}

    def test_no_parent_object_name(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        assert handler.parent_object_name is None
        assert handler.object_name_as_child is None

    def test_has_parent_object_name(self):
        handler = SalesforceObjectHandler(CONTACT_CONFIG)
        assert handler.parent_object_name == "Account"
        assert handler.object_name_as_child == "Contacts"


# ===========================================================================
# Test: Hashing integration
# ===========================================================================

class TestHashingIntegration:
    def test_hashing_pipeline(self):
        item_id = "0012v00002RkkJnAAJ"
        truncated = construct_item_id_without_hashing(item_id)
        hashed = generate_alphanumeric_128char_hash(truncated)
        full_hash = construct_item_id_with_hashing(item_id)
        assert hashed == full_hash
        assert len(full_hash) == 128


# ===========================================================================
# Test: Multiple record batch processing
# ===========================================================================

class TestBatchProcessing:
    def test_multiple_records_no_children(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        record2 = {**SAMPLE_ACCOUNT_RECORD, "Id": "001XXXXXXXXXXXXXXXXXXX", "Name": "Acme2"}
        items = handler.construct_ingestion_items(
            {"records": [SAMPLE_ACCOUNT_RECORD, record2]}, INSTANCE_URL, ALL_SCHEMA_PROPS
        )
        assert len(items) == 2
        assert items[0]["properties"]["Name"] == "GenePoint"
        assert items[1]["properties"]["Name"] == "Acme2"

    def test_missing_records_key(self):
        handler = SalesforceObjectHandler(ACCOUNT_CONFIG)
        items = handler.construct_ingestion_items({}, INSTANCE_URL, ALL_SCHEMA_PROPS)
        assert items == []


# ===========================================================================
# Test: SalesforceConverter facade
# ===========================================================================

FACADE_CONFIG = {"objectList": [ACCOUNT_CONFIG, CONTACT_CONFIG, OPPORTUNITY_CONFIG]}


class TestSalesforceConverterInit:
    def test_object_names(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        assert set(c.object_names) == {"Account", "Contact", "Opportunity"}

    def test_parent_object_names(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        assert set(c.parent_object_names) == {"Account", "Opportunity"}

    def test_icon_url_passthrough(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG, icon_url="https://icon.png")
        assert c._handlers["Account"].icon_url == "https://icon.png"

    def test_schema_properties_inferred(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        props = c.schema_properties
        assert "ObjectName" in props
        assert "Url" in props
        assert "Name" in props
        assert "AccountNumber" in props
        assert "Email" in props
        assert "Amount" in props
        assert AUTHORS_SOURCE_PROPERTY in props
        assert SYSTEM_CREATED_BY_USER_ID in props

    def test_explicit_schema_overrides_inferred(self):
        limited = {"ObjectName", "Url", "Name"}
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG, schema_properties=limited)
        assert c.schema_properties == limited

    def test_default_config_from_json_file(self):
        c = SalesforceConverter(INSTANCE_URL)
        assert "Account" in c.object_names
        assert "Contact" in c.object_names
        assert "Lead" in c.object_names
        assert "Opportunity" in c.object_names


class TestSalesforceConverterConvert:
    def test_convert_account_inferred(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        items = c.convert({"records": [SAMPLE_ACCOUNT_RECORD]})
        assert len(items) == 1
        assert items[0]["properties"]["ObjectName"] == "Account"
        assert items[0]["properties"]["Name"] == "GenePoint"

    def test_convert_with_explicit_object_name(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        items = c.convert({"records": [SAMPLE_ACCOUNT_RECORD]}, object_name="Account")
        assert len(items) == 1
        assert items[0]["properties"]["ObjectName"] == "Account"

    def test_convert_with_children(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        record = {
            **SAMPLE_ACCOUNT_RECORD,
            "Contacts": {"totalSize": 1, "done": True, "records": [
                _make_contact_record("003AAAAAAAAAAAAAAQ", "Edna", "e@g.com"),
            ]},
        }
        items = c.convert({"records": [record]})
        assert len(items) == 2
        assert items[0]["properties"]["ObjectName"] == "Contact"
        assert items[1]["properties"]["ObjectName"] == "Account"

    def test_convert_deleted(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        record = {**SAMPLE_ACCOUNT_RECORD, "IsDeleted": True}
        items = c.convert({"records": [record]})
        assert items[0]["type"] == "deleted"

    def test_convert_unknown_object_raises(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        with pytest.raises(ValueError, match="Unknown object 'Task'"):
            c.convert({"records": []}, object_name="Task")

    def test_convert_empty_records_no_object_name_raises(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        with pytest.raises(ValueError, match="Cannot infer object_name from an empty"):
            c.convert({"records": []})

    def test_convert_no_attributes_raises(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        with pytest.raises(ValueError, match="no attributes.type"):
            c.convert({"records": [{"Id": "001X"}]})

    def test_convert_empty_with_explicit_name(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        assert c.convert({"records": []}, object_name="Account") == []

    def test_convert_multiple_batches(self):
        c = SalesforceConverter(INSTANCE_URL, config=FACADE_CONFIG)
        items1 = c.convert({"records": [SAMPLE_ACCOUNT_RECORD]})
        record2 = {**SAMPLE_ACCOUNT_RECORD, "Id": "001XXXXXXXXXXXXXXXXXXX", "Name": "Acme"}
        items2 = c.convert({"records": [record2]})
        assert len(items1) == 1
        assert len(items2) == 1
        assert items1[0]["properties"]["Name"] == "GenePoint"
        assert items2[0]["properties"]["Name"] == "Acme"

    def test_convert_using_default_config(self):
        c = SalesforceConverter(INSTANCE_URL)
        items = c.convert({"records": [SAMPLE_ACCOUNT_RECORD]})
        assert len(items) == 1
        assert items[0]["properties"]["ObjectName"] == "Account"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
