"""
Unit tests for content enrichment with non-schema fields in SalesforceObjectHandler.

Tests the logic where fields from selectedFields that are NOT in schema
are appended to content.value instead of being added to properties.
"""

import pytest

from salesforce_converter.handler import SalesforceObjectHandler


class TestContentEnrichmentWithNonSchemaFields:
    """Test content enrichment with fields not in schema."""

    def test_non_schema_field_appended_to_empty_content(self):
        """Test that non-schema field is added to content when content is initially empty."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Subject": "Title",  # In schema
                "CustomField__c": "CustomFieldLabel",  # NOT in schema
            },
        }
        handler = SalesforceObjectHandler(config, icon_url="https://cdn.com/task.png")
        
        record = {
            "Id": "00T5w000001abcd",
            "Subject": "API Integration",
            "CustomField__c": "Custom Value",
        }
        
        schema_properties = {"Id", "Title", "Url", "ObjectName"}  # CustomFieldLabel NOT in schema
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["properties"]["Title"] == "API Integration"
        assert "CustomFieldLabel" not in item["properties"]
        assert item["content"]["parsedData"] == "CustomFieldLabel: Custom Value"

    def test_non_schema_field_appended_to_existing_content(self):
        """Test that non-schema field is appended to existing content."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Description": "Description",  # This is the content field
                "Priority__c": "PriorityLabel",  # NOT in schema
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Description": "Integrate new payment API",
            "Priority__c": "High",
        }
        
        schema_properties = {"Id", "Description", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["content"]["parsedData"] == "Integrate new payment API. PriorityLabel: High"

    def test_multiple_non_schema_fields_appended_with_dot_separator(self):
        """Test that multiple non-schema fields are joined with '. '."""
        # Arrange
        config = {
            "objectName": "Account",
            "selectedFields": {
                "Id": "Id",
                "Name": "Name",  # In schema
                "CustomField1__c": "CustomLabel1",  # NOT in schema
                "CustomField2__c": "CustomLabel2",  # NOT in schema
                "CustomField3__c": "CustomLabel3",  # NOT in schema
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "001xx000003DHP0",
            "Name": "Contoso",
            "CustomField1__c": "Value1",
            "CustomField2__c": "Value2",
            "CustomField3__c": "Value3",
        }
        
        schema_properties = {"Id", "Name", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["properties"]["Name"] == "Contoso"
        content = item["content"]["parsedData"]
        assert "CustomLabel1: Value1" in content
        assert "CustomLabel2: Value2" in content
        assert "CustomLabel3: Value3" in content
        # Check separator
        assert content == "CustomLabel1: Value1. CustomLabel2: Value2. CustomLabel3: Value3"

    def test_non_schema_field_with_description_content(self):
        """Test non-schema fields appended to Description content field."""
        # Arrange
        config = {
            "objectName": "Opportunity",
            "selectedFields": {
                "Id": "Id",
                "Name": "Name",  # In schema
                "Description": "Description",  # In schema (content field)
                "ExpectedRevenue__c": "ExpectedRevenue",  # NOT in schema
                "Probability__c": "WinProbability",  # NOT in schema
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "006xx000001dF7z",
            "Name": "Big Deal",
            "Description": "Enterprise license deal with Fortune 500 company",
            "ExpectedRevenue__c": "$500,000",
            "Probability__c": "75%",
        }
        
        schema_properties = {"Id", "Name", "Description", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["properties"]["Name"] == "Big Deal"
        assert item["properties"]["Description"] == "Enterprise license deal with Fortune 500 company"
        expected_content = (
            "Enterprise license deal with Fortune 500 company. "
            "ExpectedRevenue: $500,000. WinProbability: 75%"
        )
        assert item["content"]["parsedData"] == expected_content

    def test_non_schema_field_with_null_value_skipped(self):
        """Test that non-schema fields with null values are skipped."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Subject": "Title",
                "CustomField1__c": "CustomLabel1",
                "CustomField2__c": "CustomLabel2",  # Will be null
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Subject": "Test Task",
            "CustomField1__c": "Has Value",
            "CustomField2__c": None,
        }
        
        schema_properties = {"Id", "Title", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        # Only CustomLabel1 should be in content, CustomLabel2 is null
        assert item["content"]["parsedData"] == "CustomLabel1: Has Value"

    def test_non_schema_field_with_empty_string_skipped(self):
        """Test that non-schema fields with empty string values are skipped."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Subject": "Title",
                "CustomField1__c": "CustomLabel1",
                "CustomField2__c": "CustomLabel2",  # Will be empty
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Subject": "Test Task",
            "CustomField1__c": "Has Value",
            "CustomField2__c": "",
        }
        
        schema_properties = {"Id", "Title", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        # Only CustomLabel1 should be in content, CustomLabel2 is empty
        assert item["content"]["parsedData"] == "CustomLabel1: Has Value"

    def test_non_schema_field_with_array_value(self):
        """Test that non-schema fields with array values are formatted correctly."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "AssignedTo__c": "AssignedToLabel",  # NOT in schema, array value
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "AssignedTo__c": ["Alice", "Bob", "Charlie"],
        }
        
        schema_properties = {"Id", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["content"]["parsedData"] == "AssignedToLabel: Alice, Bob, Charlie"

    def test_non_schema_field_with_numeric_value(self):
        """Test that non-schema fields with numeric values are formatted correctly."""
        # Arrange
        config = {
            "objectName": "Opportunity",
            "selectedFields": {
                "Id": "Id",
                "Amount": "Amount",  # In schema
                "Discount__c": "DiscountPercent",  # NOT in schema, numeric
                "Quantity__c": "Quantity",  # NOT in schema, integer
            },
            "SfColumnTypes": {
                "Amount": "System.Double, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089",
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "006xx000001dF7z",
            "Amount": 100000.50,
            "Discount__c": 15.5,
            "Quantity__c": 42,
        }
        
        schema_properties = {"Id", "Amount", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["properties"]["Amount"] == 100000.50
        assert "DiscountPercent: 15.5" in item["content"]["parsedData"]
        assert "Quantity: 42" in item["content"]["parsedData"]

    def test_non_schema_field_with_boolean_value(self):
        """Test that non-schema fields with boolean values are formatted correctly."""
        # Arrange
        config = {
            "objectName": "Account",
            "selectedFields": {
                "Id": "Id",
                "IsActive__c": "ActiveStatus",  # NOT in schema, boolean
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "001xx000003DHP0",
            "IsActive__c": True,
        }
        
        schema_properties = {"Id", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["content"]["parsedData"] == "ActiveStatus: True"

    def test_all_fields_in_schema_no_content_enrichment(self):
        """Test that when all fields are in schema, no extra content is added."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Subject": "Title",
                "Status": "Status",
                "Priority": "Priority",
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Subject": "API Integration",
            "Status": "In Progress",
            "Priority": "High",
        }
        
        # All fields are in schema
        schema_properties = {"Id", "Title", "Status", "Priority", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        assert item["properties"]["Title"] == "API Integration"
        assert item["properties"]["Status"] == "In Progress"
        assert item["properties"]["Priority"] == "High"
        # Content should be empty since no Description field and no non-schema fields
        assert item["content"]["parsedData"] == ""

    def test_mixed_schema_and_non_schema_fields(self):
        """Test comprehensive scenario with mix of schema and non-schema fields."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Subject": "Title",  # In schema
                "Description": "Description",  # In schema (content field)
                "Status": "Status",  # In schema
                "AssignedTo": "AssignedTo",  # In schema
                "CustomPriority__c": "Priority",  # NOT in schema
                "CustomTags__c": "Tags",  # NOT in schema
                "InternalNotes__c": "Notes",  # NOT in schema
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Subject": "API Integration",
            "Description": "Integrate new payment API",
            "Status": "In Progress",
            "AssignedTo": "Alex Johnson",
            "CustomPriority__c": "P1",
            "CustomTags__c": "backend, api, payments",
            "InternalNotes__c": "Needs security review",
        }
        
        schema_properties = {"Id", "Title", "Description", "Status", "AssignedTo", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        
        # Schema fields in properties
        assert item["properties"]["Title"] == "API Integration"
        assert item["properties"]["Description"] == "Integrate new payment API"
        assert item["properties"]["Status"] == "In Progress"
        assert item["properties"]["AssignedTo"] == "Alex Johnson"
        
        # Non-schema fields NOT in properties
        assert "Priority" not in item["properties"]
        assert "Tags" not in item["properties"]
        assert "Notes" not in item["properties"]
        
        # Non-schema fields appended to content
        expected_content = (
            "Integrate new payment API. "
            "Priority: P1. "
            "Tags: backend, api, payments. "
            "Notes: Needs security review"
        )
        assert item["content"]["parsedData"] == expected_content

    def test_non_schema_field_with_dict_value_json_serialized(self):
        """Test that non-schema fields with dict values are JSON serialized."""
        # Arrange
        config = {
            "objectName": "Task",
            "selectedFields": {
                "Id": "Id",
                "Metadata__c": "MetadataLabel",  # NOT in schema, dict value
            },
        }
        handler = SalesforceObjectHandler(config)
        
        record = {
            "Id": "00T5w000001abcd",
            "Metadata__c": {"category": "engineering", "team": "backend"},
        }
        
        schema_properties = {"Id", "Url", "ObjectName"}
        
        # Act
        items = handler.construct_ingestion_items(
            {"records": [record]},
            "https://contoso.salesforce.com",
            schema_properties,
        )
        
        # Assert
        assert len(items) == 1
        item = items[0]
        content = item["content"]["parsedData"]
        # Should contain JSON representation
        assert "MetadataLabel:" in content
        assert '"category": "engineering"' in content or '"category":"engineering"' in content
        assert '"team": "backend"' in content or '"team":"backend"' in content


class TestNonSchemaFieldHelper:
    """Test the _add_non_schema_field_to_content helper method."""

    def test_add_non_schema_field_string_value(self):
        """Test adding string value."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "CustomField", "Test Value"
        )
        
        # Assert
        assert content_parts == ["CustomField: Test Value"]

    def test_add_non_schema_field_numeric_value(self):
        """Test adding numeric value."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "Amount", 12345.67
        )
        
        # Assert
        assert content_parts == ["Amount: 12345.67"]

    def test_add_non_schema_field_boolean_value(self):
        """Test adding boolean value."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "IsActive", True
        )
        
        # Assert
        assert content_parts == ["IsActive: True"]

    def test_add_non_schema_field_list_value(self):
        """Test adding list value."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "Tags", ["tag1", "tag2", "tag3"]
        )
        
        # Assert
        assert content_parts == ["Tags: tag1, tag2, tag3"]

    def test_add_non_schema_field_none_value_skipped(self):
        """Test that None values are skipped."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "NullField", None
        )
        
        # Assert
        assert content_parts == []

    def test_add_non_schema_field_empty_string_skipped(self):
        """Test that empty strings are skipped."""
        # Arrange
        content_parts = []
        
        # Act
        SalesforceObjectHandler._add_non_schema_field_to_content(
            content_parts, "EmptyField", ""
        )
        
        # Assert
        assert content_parts == []
