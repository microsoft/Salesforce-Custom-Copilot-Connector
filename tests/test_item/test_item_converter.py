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
