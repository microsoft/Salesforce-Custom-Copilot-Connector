# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for SalesforceItemTransformer (salesforce.item_transformer)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from salesforce.item_transformer import SalesforceItemTransformer, _fallback_acl, COLLECTION_SCHEMA_TO_ODATA_TYPE, PRINCIPAL_ODATA_TYPE


@pytest.fixture
def schema():
    """Minimal Graph schema for testing."""
    return [
        {"name": "Url", "type": "String"},
        {"name": "ObjectName", "type": "String"},
        {"name": "Title", "type": "String"},
        {"name": "Description", "type": "String"},
        {"name": "CreatedDate", "type": "DateTime"},
        {"name": "LastModifiedDate", "type": "DateTime"},
        {"name": "Owner", "type": "String"},
        {"name": "Tags", "type": "StringCollection"},
    ]


@pytest.fixture
def transformer(schema):
    return SalesforceItemTransformer(
        instance_url="https://test.my.salesforce.com",
        schema=schema,
    )


def test_can_instantiate(transformer):
    assert transformer is not None


@patch("salesforce.item_transformer.SalesforceConverter")
def test_transform_record_produces_output(mock_converter_cls, schema):
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {
            "id": "001abc",
            "properties": {"Url": "https://sf.com/001abc", "Title": "Acme"},
            "content": {"parsedData": "description text"},
        }
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    raw = {"Id": "001abc", "objectType": "Account", "url": "https://sf.com/001abc"}
    result = t.transform_record(raw)
    assert len(result) == 1
    assert result[0]["id"] == "001abc"
    assert result[0]["content"]["type"] == "text"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_acl_included_when_provided(mock_converter_cls, schema):
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {"id": "001", "properties": {"Url": "https://sf.com/001"}, "content": {}}
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    acl = [{"accessType": "grant", "type": "user", "value": "user-id"}]
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"}, acl=acl)
    assert result[0]["acl"] == acl


@patch("salesforce.item_transformer.SalesforceConverter")
def test_fallback_acl_used_when_none(mock_converter_cls, schema, monkeypatch):
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {"id": "001", "properties": {"Url": "https://sf.com/001"}, "content": {}}
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema, tenant_id="test-tenant")
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"}, acl=None)
    assert result[0]["acl"][0]["type"] == "everyone"
    assert result[0]["acl"][0]["value"] == "everyone"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_deleted_items_pass_through(mock_converter_cls, schema):
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [{"type": "deleted", "id": "001del"}]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    result = t.transform_record({"Id": "001del", "objectType": "Account", "url": "https://sf.com/001del"})
    assert result[0]["type"] == "deleted"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_collection_types_get_odata_annotation(mock_converter_cls, schema):
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {"id": "001", "properties": {"Url": "https://sf.com/001", "Tags": ["a", "b"]}, "content": {}}
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"})
    props = result[0]["properties"]
    assert "Tags@odata.type" in props
    assert props["Tags@odata.type"] == "Collection(String)"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_principal_collection_gets_odata_annotation(mock_converter_cls):
    schema = [
        {"name": "Url", "type": "String"},
        {"name": "ObjectName", "type": "String"},
        {"name": "Authors", "type": "PrincipalCollection"},
    ]
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {"id": "001", "properties": {"Url": "https://sf.com/001", "Authors": [{"externalName": "user1", "externalId": "id1"}, {"externalName": "user2", "externalId": "id2"}]}, "content": {}}
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"})
    props = result[0]["properties"]
    assert "Authors@odata.type" in props
    assert props["Authors@odata.type"] == "Collection(microsoft.graph.externalConnectors.principal)"
    # each principal dict gets @odata.type injected
    assert props["Authors"][0]["@odata.type"] == PRINCIPAL_ODATA_TYPE
    assert props["Authors"][0]["externalId"] == "id1"
    assert props["Authors"][1]["@odata.type"] == PRINCIPAL_ODATA_TYPE
    assert props["Authors"][1]["externalId"] == "id2"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_principal_collection_items_get_odata_type_injected(mock_converter_cls):
    schema = [
        {"name": "Url", "type": "String"},
        {"name": "ObjectName", "type": "String"},
        {"name": "Assignees", "type": "PrincipalCollection"},
    ]
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {
            "id": "001",
            "properties": {
                "Url": "https://sf.com/001",
                "Assignees": [
                    {"entraId": "aaa", "upn": "a@test.com"},
                    {"@odata.type": PRINCIPAL_ODATA_TYPE, "entraId": "bbb", "upn": "b@test.com"},
                ],
            },
            "content": {},
        }
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"})
    assignees = result[0]["properties"]["Assignees"]
    # @odata.type injected where missing
    assert assignees[0]["@odata.type"] == PRINCIPAL_ODATA_TYPE
    assert assignees[0]["entraId"] == "aaa"
    # @odata.type preserved when already present
    assert assignees[1]["@odata.type"] == PRINCIPAL_ODATA_TYPE
    assert assignees[1]["entraId"] == "bbb"


@patch("salesforce.item_transformer.SalesforceConverter")
def test_single_principal_gets_odata_type_injected(mock_converter_cls):
    schema = [
        {"name": "Url", "type": "String"},
        {"name": "ObjectName", "type": "String"},
        {"name": "CreatedBy", "type": "Principal"},
    ]
    mock_converter = mock_converter_cls.return_value
    mock_converter.object_names = ["Account"]
    mock_converter.get_handler.return_value = None
    mock_converter.convert.return_value = [
        {
            "id": "001",
            "properties": {
                "Url": "https://sf.com/001",
                "CreatedBy": {"entraId": "b671a5be", "upn": "alex@contoso.com"},
            },
            "content": {},
        }
    ]
    t = SalesforceItemTransformer("https://test.my.salesforce.com", schema)
    result = t.transform_record({"Id": "001", "objectType": "Account", "url": "https://sf.com/001"})
    created_by = result[0]["properties"]["CreatedBy"]
    assert created_by["@odata.type"] == PRINCIPAL_ODATA_TYPE
    assert created_by["entraId"] == "b671a5be"
    # single Principal should NOT produce a collection annotation
    assert "CreatedBy@odata.type" not in result[0]["properties"]
