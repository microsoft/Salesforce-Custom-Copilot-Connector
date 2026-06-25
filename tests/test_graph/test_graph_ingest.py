# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the graph.ingest ingestion pipeline."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch, call
from urllib.parse import quote

import pytest

from graph.client import GraphApiError, EXTERNAL_CONNECTIONS_PATH
from graph.ingest import load_content, delete_content, ingest_content, IngestionStats
from salesforce.api_client import SalesforceObjectConfig

# A minimal object config used when mocking get_object_config
_ACCOUNT_CFG = SalesforceObjectConfig(object_type="Account", fields=("Id",))


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture(autouse=True)
def _patch_sync_state(tmp_path):
    """Prevent ingest_content from touching the filesystem (checkpoint / dead-letter)."""
    with (
        patch("graph.ingest.read_checkpoint", return_value=None),
        patch("graph.ingest.write_checkpoint"),
        patch("graph.ingest.clear_checkpoint"),
        patch("graph.ingest.failed_records_path", return_value=tmp_path / "dl.jsonl"),
        patch("graph.ingest.append_failed_records"),
    ):
        yield


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

@patch("graph.ingest.iter_object_chunks", return_value=iter([]))
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
def test_ingest_no_items_returns_empty_stats(mock_acl, mock_get_cfg, mock_chunks, test_config, mock_client):
    cfg = replace(test_config, debug_object_type="Account")
    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 0
    assert stats.success_count == 0


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_transforms_and_loads(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    cfg = replace(test_config, debug_object_type="Account")
    raw = [{"Id": "001", "objectType": "Account", "url": "https://sf.com/001"}]
    mock_chunks.return_value = iter([raw])

    mock_acl_inst = MagicMock()
    mock_acl_inst.resolve.return_value = {"Account": {"001": [{"accessType": "grant", "type": "everyone", "value": "everyone"}]}}
    mock_acl_cls.return_value = mock_acl_inst

    transformed = [{"id": "001", "properties": {"Url": "https://sf.com/001"}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 1
    assert stats.success_count == 1


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_handles_deleted_items(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    cfg = replace(test_config, debug_object_type="Account")
    raw = [{"Id": "002", "objectType": "Account", "url": "https://sf.com/002", "IsDeleted": True}]
    mock_chunks.return_value = iter([raw])
    mock_acl_cls.return_value.resolve.return_value = {}

    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = [{"type": "deleted", "id": "002"}]
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(cfg, mock_client)
    assert stats.deleted_count == 1


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_handles_failed_items(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    cfg = replace(test_config, debug_object_type="Account")
    raw = [{"Id": "003", "objectType": "Account", "url": "https://sf.com/003"}]
    mock_chunks.return_value = iter([raw])
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "003", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 400, "body": "bad request"}]
    stats = ingest_content(cfg, mock_client)
    assert stats.failed_count == 1
    assert "003" in stats.failed_ids


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_acl_failure_falls_back_to_public(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    cfg = replace(test_config, debug_object_type="Account")
    raw = [{"Id": "004", "objectType": "Account", "url": "https://sf.com/004"}]
    mock_chunks.return_value = iter([raw])
    mock_acl_cls.return_value.resolve.side_effect = RuntimeError("ACL boom")

    transformed = [{"id": "004", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(cfg, mock_client)
    assert stats.acl_fallback_used is True


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_filters_by_debug_item_id(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    cfg = replace(test_config, debug_item_id="005", debug_object_type="Account")
    raw = [
        {"Id": "005", "objectType": "Account", "url": "https://sf.com/005"},
        {"Id": "006", "objectType": "Account", "url": "https://sf.com/006"},
    ]
    mock_chunks.return_value = iter([raw])
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "005", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 1


@patch("graph.ingest.iter_object_chunks")
@patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ingest_filters_by_debug_object_type(mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks, test_config, mock_client, monkeypatch):
    cfg = replace(test_config, debug_object_type="Account")
    raw = [
        {"Id": "007", "objectType": "Account", "url": "https://sf.com/007"},
    ]
    mock_chunks.return_value = iter([raw])
    mock_acl_cls.return_value.resolve.return_value = {}

    transformed = [{"id": "007", "properties": {}, "acl": [], "content": {"value": ""}}]
    mock_transformer_inst = MagicMock()
    mock_transformer_inst.transform_record.return_value = transformed
    mock_transformer_inst.handlers = {}
    mock_transformer_cls.return_value = mock_transformer_inst

    mock_client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(cfg, mock_client)
    assert stats.total_fetched == 1
