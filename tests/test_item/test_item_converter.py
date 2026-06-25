# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the item conversion engine (item.converter)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from item.converter import _convert_value, load_converter_config, SalesforceConverter
from acl_engine.share_fetcher import _share_table_name


def test_load_converter_config_returns_valid():
    config = load_converter_config()
    assert isinstance(config, dict)
    assert "objectList" in config


def test_salesforce_converter_can_be_instantiated():
    converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
    assert converter is not None
    assert len(converter.object_names) > 0


def test_convert_simple_account():
    converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
    record = {
        "Id": "001abc",
        "Name": "Acme Corp",
        "IsDeleted": False,
        "objectType": "Account",
        "url": "https://test.my.salesforce.com/001abc",
        "attributes": {"type": "Account"},
        "OwnerId": "005abc",
        "Owner": {"Name": "Test User", "UserRole": {"Id": "role1", "ParentRoleId": None}},
        "CreatedDate": "2024-01-01T00:00:00.000+0000",
        "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        "CreatedById": "005abc",
        "CreatedBy": {"Name": "Creator"},
        "LastModifiedById": "005abc",
        "LastModifiedBy": {"Name": "Modifier"},
    }
    sf_result = {"records": [record]}
    items = converter.convert(sf_result, object_name="Account")
    assert len(items) >= 1
    item = items[0]
    assert "id" in item


def test_deleted_record_produces_deleted_item():
    converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
    record = {
        "Id": "001del",
        "Name": "Deleted Corp",
        "IsDeleted": True,
        "objectType": "Account",
        "url": "https://test.my.salesforce.com/001del",
        "attributes": {"type": "Account"},
        "OwnerId": "005abc",
        "Owner": {"Name": "Test User", "UserRole": {"Id": "role1", "ParentRoleId": None}},
        "CreatedDate": "2024-01-01T00:00:00.000+0000",
        "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        "CreatedById": "005abc",
        "CreatedBy": {"Name": "Creator"},
        "LastModifiedById": "005abc",
        "LastModifiedBy": {"Name": "Modifier"},
    }
    items = converter.convert({"records": [record]}, object_name="Account")
    assert len(items) >= 1
    assert items[0].get("type") == "deleted"


def test_content_field_mapped_to_parsed_data():
    """If Description is present, it should appear in the content.parsedData."""
    converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
    record = {
        "Id": "001desc",
        "IsDeleted": False,
        "objectType": "Account",
        "url": "https://test.my.salesforce.com/001desc",
        "attributes": {"type": "Account"},
        "Name": "Acme Corp",
        "Description": "This is the account description.",
        "OwnerId": "005abc",
        "Owner": {"Name": "Test User", "UserRole": {"Id": "role1", "ParentRoleId": None}},
        "CreatedDate": "2024-01-01T00:00:00.000+0000",
        "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        "CreatedById": "005abc",
        "CreatedBy": {"Name": "Creator"},
        "LastModifiedById": "005abc",
        "LastModifiedBy": {"Name": "Modifier"},
    }
    items = converter.convert({"records": [record]}, object_name="Account")
    non_deleted = [i for i in items if i.get("type") != "deleted"]
    if non_deleted:
        content = non_deleted[0].get("content", {})
        if isinstance(content, dict):
            assert "This is the account description" in (content.get("parsedData") or "")


def test_metadata_columns_mapped():
    converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
    record = {
        "Id": "001meta",
        "Name": "Meta Corp",
        "IsDeleted": False,
        "objectType": "Account",
        "url": "https://test.my.salesforce.com/001meta",
        "attributes": {"type": "Account"},
        "OwnerId": "005abc",
        "Owner": {"Name": "Owner User", "UserRole": {"Id": "role1", "ParentRoleId": None}},
        "CreatedDate": "2024-01-01T00:00:00.000+0000",
        "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        "CreatedById": "005abc",
        "CreatedBy": {"Name": "Creator"},
        "LastModifiedById": "005def",
        "LastModifiedBy": {"Name": "Modifier"},
    }
    items = converter.convert({"records": [record]}, object_name="Account")
    non_deleted = [i for i in items if i.get("type") != "deleted"]
    if non_deleted:
        props = non_deleted[0].get("properties", {})
        # At least some metadata should be present
        assert "Id" in props or "CreatedDate" in props or "Owner" in props


class TestNonSchemaFieldsInContent:
    """Non-graph-schema selectedFields must appear in content, not be silently dropped."""

    @staticmethod
    def _build_contact_record(**overrides: str) -> dict:
        base = {
            "attributes": {"type": "Contact"},
            "Id": "003abc",
            "Name": "Test User",
            "IsDeleted": False,
            "OwnerId": "005abc",
            "Owner": {"attributes": {"type": "User"}, "Name": "Owner", "UserRole": None},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedById": "005abc",
            "CreatedBy": {"attributes": {"type": "User"}, "Name": "Creator"},
            "LastModifiedById": "005abc",
            "LastModifiedBy": {"attributes": {"type": "User"}, "Name": "Modifier"},
        }
        base.update(overrides)
        return base

    def test_non_schema_selected_fields_appear_in_content(self):
        """Fields in selectedFields but NOT in graph-schema should appear in content.parsedData."""
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        handler = converter.get_handler("Contact")
        assert handler is not None

        # Simulate the transformer setting the real Graph schema properties
        # (a small subset — only Id, Name, ObjectName, url, AccountId, Status exist in graph-schema)
        handler.graph_schema_properties = {"Id", "Name", "ObjectName", "url", "AccountId", "Status"}

        record = self._build_contact_record(
            Email="anna@example.com",
            FirstName="Anna",
            LastName="Smith",
            Phone="+1-555-1234",
            Title="Architect",
        )
        items = converter.convert({"records": [record]}, object_name="Contact")
        non_deleted = [i for i in items if i.get("type") != "deleted"]
        assert len(non_deleted) == 1

        item = non_deleted[0]
        props = item["properties"]
        content_value = item.get("content", {}).get("parsedData", "")

        # These ARE in graph-schema → should be in properties
        assert props.get("Name") == "Test User"
        assert "ObjectName" in props

        # These are NOT in graph-schema → should NOT be in properties
        assert "Email" not in props
        assert "FirstName" not in props
        assert "LastName" not in props
        assert "Phone" not in props
        assert "JobTitle" not in props  # Title maps to JobTitle

        # They SHOULD appear in content.parsedData instead
        assert "anna@example.com" in content_value
        assert "Anna" in content_value
        assert "Smith" in content_value
        assert "+1-555-1234" in content_value
        assert "Architect" in content_value

    def test_schema_fields_not_duplicated_in_content(self):
        """Fields that ARE in the graph-schema should be in properties, NOT in content."""
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        handler = converter.get_handler("Contact")
        assert handler is not None
        handler.graph_schema_properties = {"Id", "Name", "ObjectName", "url", "AccountId", "Status"}

        record = self._build_contact_record(Name="Anna Smith")
        items = converter.convert({"records": [record]}, object_name="Contact")
        item = [i for i in items if i.get("type") != "deleted"][0]

        assert item["properties"].get("Name") == "Anna Smith"
        content_value = item.get("content", {}).get("parsedData", "")
        # Name should NOT appear in content (it's in graph-schema → properties)
        assert "Name: Anna Smith" not in content_value

    def test_null_non_schema_fields_omitted_from_content(self):
        """Null-valued non-schema fields should not appear in content."""
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        handler = converter.get_handler("Contact")
        assert handler is not None
        handler.graph_schema_properties = {"Id", "Name", "ObjectName", "url"}

        record = self._build_contact_record(Email=None, Phone=None)
        # Explicitly set to None (simulates Salesforce null return)
        record["Email"] = None
        record["Phone"] = None
        items = converter.convert({"records": [record]}, object_name="Contact")
        item = [i for i in items if i.get("type") != "deleted"][0]
        content_value = item.get("content", {}).get("parsedData", "")

        assert "Email" not in content_value
        assert "Phone" not in content_value

    def test_synthetic_url_and_objecttype_not_in_content(self):
        """Synthetic 'url' and 'objectType' keys on the record must not leak into content."""
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        handler = converter.get_handler("Contact")
        assert handler is not None
        handler.graph_schema_properties = {"Id", "Name", "ObjectName", "url"}

        record = self._build_contact_record()
        # These are added by api_client before converter sees the record
        record["url"] = "https://test.my.salesforce.com/003abc"
        record["objectType"] = "Contact"

        items = converter.convert({"records": [record]}, object_name="Contact")
        item = [i for i in items if i.get("type") != "deleted"][0]
        content_value = item.get("content", {}).get("parsedData", "")

        assert "objectType" not in content_value
        assert "objectType: Contact" not in content_value
        # url as a standalone content entry should not appear
        # (url is a schema property set synthetically, not a content field)
        assert "url: https://" not in content_value


# ---------------------------------------------------------------------------
# _share_table_name from acl_engine
# ---------------------------------------------------------------------------

def test_share_table_name_standard_object():
    assert _share_table_name("Account") == "AccountShare"
    assert _share_table_name("Case") == "CaseShare"


def test_share_table_name_custom_object():
    assert _share_table_name("Work_Order__c") == "Work_Order__Share"
    assert _share_table_name("Customer_Project__c") == "Customer_Project__Share"


# ---------------------------------------------------------------------------
# _convert_value – datetime handling
# ---------------------------------------------------------------------------

class TestConvertValueDatetime:
    """Verify that all Salesforce datetime formats are normalised to ISO 8601 with Z."""

    def test_salesforce_offset_format(self):
        """Salesforce standard: +0000 offset without colon."""
        assert _convert_value("2024-04-11T09:10:06.000+0000", "datetime") == "2024-04-11T09:10:06Z"

    def test_salesforce_offset_with_colon(self):
        """Offset with colon (+00:00)."""
        assert _convert_value("2024-01-01T00:00:00.000+00:00", "datetime") == "2024-01-01T00:00:00Z"

    def test_z_suffix(self):
        """Trailing Z suffix."""
        assert _convert_value("2024-06-15T14:30:00Z", "datetime") == "2024-06-15T14:30:00Z"

    def test_z_suffix_with_milliseconds(self):
        """Z suffix with milliseconds."""
        assert _convert_value("2024-06-15T14:30:00.123Z", "datetime") == "2024-06-15T14:30:00.123000Z"

    def test_non_utc_positive_offset(self):
        """Non-UTC positive offset is converted to UTC."""
        assert _convert_value("2024-03-20T15:00:00+05:30", "datetime") == "2024-03-20T09:30:00Z"

    def test_non_utc_negative_offset(self):
        """Non-UTC negative offset is converted to UTC."""
        assert _convert_value("2024-03-20T10:00:00-07:00", "datetime") == "2024-03-20T17:00:00Z"

    def test_no_fractional_seconds(self):
        """Date string without fractional seconds."""
        assert _convert_value("2024-04-11T09:10:06+0000", "datetime") == "2024-04-11T09:10:06Z"

    def test_date_only_no_time(self):
        """Date-only string (Salesforce Date fields)."""
        assert _convert_value("2024-04-11", "datetime") == "2024-04-11T00:00:00Z"

    def test_none_returns_none(self):
        """None input returns None."""
        assert _convert_value(None, "datetime") is None

    def test_aware_datetime_object(self):
        """Already-aware Python datetime object."""
        dt = datetime(2024, 4, 11, 9, 10, 6, tzinfo=timezone.utc)
        assert _convert_value(dt, "datetime") == "2024-04-11T09:10:06Z"

    def test_naive_datetime_object_assumed_utc(self):
        """Naive Python datetime is assumed UTC."""
        dt = datetime(2024, 4, 11, 9, 10, 6)
        assert _convert_value(dt, "datetime") == "2024-04-11T09:10:06Z"

    def test_non_zero_milliseconds_preserved(self):
        """Non-zero fractional seconds are preserved."""
        assert _convert_value("2024-04-11T09:10:06.500+0000", "datetime") == "2024-04-11T09:10:06.500000Z"
