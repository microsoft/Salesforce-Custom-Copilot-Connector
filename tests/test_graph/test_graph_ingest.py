"""Tests for the graph.ingest ingestion pipeline."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch, call
from urllib.parse import quote

import pytest

from graph.client import GraphApiError, EXTERNAL_CONNECTIONS_PATH
from graph.ingest import load_content, delete_content, ingest_content, IngestionStats


@pytest.fixture
def mock_client():
    return MagicMock()


# ---------------------------------------------------------------------------
# load_content / delete_content
# ---------------------------------------------------------------------------

def test_load_content_puts_correct_url(test_config, mock_client):
    item = {"id": "item-1", "properties": {"Url": "https://example.com"}, "acl": [], "content": {"value": ""}}
    load_content(test_config, mock_client, item)
    expected_url = f"{EXTERNAL_CONNECTIONS_PATH}/{test_config.connector.id}/items/{quote('item-1', safe='')}"
    mock_client.put.assert_called_once()
    assert mock_client.put.call_args[0][0] == expected_url


def test_delete_content_calls_delete(test_config, mock_client):
    delete_content(test_config, mock_client, "item-1")
    mock_client.delete.assert_called_once()


# ---------------------------------------------------------------------------
# ingest_content
# ---------------------------------------------------------------------------

@patch("graph.ingest.get_all_items_from_api", return_value=iter([]))
@patch("graph.ingest.LegacyAclResolver")
def test_ingest_no_items_returns_empty_stats(mock_acl, mock_api, test_config, mock_client):
    stats = ingest_content(test_config, mock_client)
    assert stats.total_fetched == 0
    assert stats.success_count == 0


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_transforms_and_loads(mock_transformer_cls, mock_acl_cls, mock_api, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    raw = [{"Id": "001", "objectType": "Account", "url": "https://sf.com/001"}]
    mock_api.return_value = iter(raw)

    mock_acl_inst = MagicMock()
    mock_acl_inst.resolve.return_value = {"Account": {"001": [{"accessType": "grant", "type": "everyone", "value": "everyone"}]}}
    mock_acl_cls.return_value = mock_acl_inst

    transformed = [{"id": "001", "properties": {"Url": "https://sf.com/001"}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    stats = ingest_content(test_config, mock_client)
    assert stats.total_fetched == 1
    assert stats.success_count == 1


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_handles_deleted_items(mock_transformer_cls, mock_acl_cls, mock_api, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    raw = [{"Id": "002", "objectType": "Case", "url": "https://sf.com/002", "IsDeleted": True}]
    mock_api.return_value = iter(raw)
    mock_acl_cls.return_value.resolve.return_value = {}

    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = [{"type": "deleted", "id": "002"}]
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    stats = ingest_content(test_config, mock_client)
    assert stats.deleted_count == 1


@patch("graph.ingest.load_content")
@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_handles_failed_items(mock_transformer_cls, mock_acl_cls, mock_api, mock_load, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    raw = [{"Id": "003", "objectType": "Lead", "url": "https://sf.com/003"}]
    mock_api.return_value = iter(raw)
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "003", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_load.side_effect = GraphApiError(400, "bad request")
    stats = ingest_content(test_config, mock_client)
    assert stats.failed_count == 1
    assert "003" in stats.failed_ids


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_acl_failure_falls_back_to_public(mock_transformer_cls, mock_acl_cls, mock_api, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    raw = [{"Id": "004", "objectType": "Account", "url": "https://sf.com/004"}]
    mock_api.return_value = iter(raw)
    mock_acl_cls.return_value.resolve.side_effect = RuntimeError("ACL boom")

    transformed = [{"id": "004", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    stats = ingest_content(test_config, mock_client)
    assert stats.acl_fallback_used is True


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_filters_by_debug_item_id(mock_transformer_cls, mock_acl_cls, mock_api, test_config, mock_client, monkeypatch):
    cfg = replace(test_config, debug_item_id="005")
    raw = [
        {"Id": "005", "objectType": "Case", "url": "https://sf.com/005"},
        {"Id": "006", "objectType": "Case", "url": "https://sf.com/006"},
    ]
    mock_api.return_value = iter(raw)
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "005", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 1


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_filters_by_debug_object_type(mock_transformer_cls, mock_acl_cls, mock_api, test_config, mock_client, monkeypatch):
    cfg = replace(test_config, debug_object_type="Account")
    raw = [
        {"Id": "007", "objectType": "Account", "url": "https://sf.com/007"},
        {"Id": "008", "objectType": "Case", "url": "https://sf.com/008"},
    ]
    mock_api.return_value = iter(raw)
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "007", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 1
