"""Tests for checkpoint, delta sync, and Ctrl+X resume behavior.

Simulates the scenarios:
1. Normal completion → checkpoint cleared, sync timestamp saved
2. Ctrl+X stop → checkpoint preserved, sync timestamp NOT saved
3. Resume after Ctrl+X → skips already-completed chunks
4. --full flag → clears checkpoint and starts fresh
"""
from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from graph.ingest import ingest_content, IngestionStats
from config.sync_state import (
    read_checkpoint,
    write_checkpoint,
    clear_checkpoint,
    read_last_sync,
    write_last_sync,
)


@pytest.fixture(autouse=True)
def _isolate_sync_state(tmp_path, monkeypatch):
    """Redirect all sync state files to a temp directory."""
    monkeypatch.setattr("config.sync_state.LOGS_DIR", tmp_path)
    # Also patch the imports in graph.ingest (already imported at module level)
    monkeypatch.setattr("graph.ingest.read_checkpoint", read_checkpoint)
    monkeypatch.setattr("graph.ingest.write_checkpoint", write_checkpoint)
    monkeypatch.setattr("graph.ingest.clear_checkpoint", clear_checkpoint)
    monkeypatch.setattr("graph.ingest.failed_records_path",
                        lambda cid: tmp_path / f"failed_records_{cid}.jsonl")
    monkeypatch.setattr("graph.ingest.append_failed_records", lambda *a, **kw: None)


def _make_records(object_type, count, start=1):
    return [
        {"Id": f"{object_type[:3]}{i:04d}", "objectType": object_type, "url": f"https://sf/{i}"}
        for i in range(start, start + count)
    ]


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_normal_completion_clears_checkpoint(
    mock_transformer_cls, mock_acl_cls, mock_api, test_config, monkeypatch,
):
    """After a full successful run, the checkpoint file should be deleted."""
    connector_id = test_config.connector.id

    # Pre-plant a checkpoint to verify it gets cleared
    write_checkpoint(connector_id, None, "Account", 2)
    assert read_checkpoint(connector_id) is not None

    records = _make_records("Account", 3)
    mock_api.return_value = iter(records)
    mock_acl_cls.return_value.resolve.return_value = {"Account": {r["Id"]: [] for r in records}}
    mock_transformer_cls.return_value.handlers = {}
    mock_transformer_cls.return_value.transform_record.return_value = [
        {"id": "x", "properties": {}, "acl": [], "content": {"value": ""}}
    ]

    client = MagicMock()
    client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(test_config, client, since=None, dashboard=None)
    assert stats.total_fetched == 3
    # Checkpoint should be cleared after normal completion
    assert read_checkpoint(connector_id) is None


@patch("salesforce.api_client.get_object_counts", return_value={})
@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_ctrlx_preserves_checkpoint(
    mock_transformer_cls, mock_acl_cls, mock_api, mock_counts, test_config, monkeypatch,
):
    """When Ctrl+X is pressed, the checkpoint must NOT be cleared."""
    connector_id = test_config.connector.id

    # 10 records = 1 chunk (chunk_size=500, all fit in one chunk)
    # We use 2 object types so the loop runs twice
    records = _make_records("Account", 5) + _make_records("Case", 5)
    mock_api.return_value = iter(records)

    acl_account = {r["Id"]: [] for r in records if r["objectType"] == "Account"}
    acl_case = {r["Id"]: [] for r in records if r["objectType"] == "Case"}
    mock_acl_cls.return_value.resolve.side_effect = [
        {"Account": acl_account},
        {"Case": acl_case},
    ]
    mock_transformer_cls.return_value.handlers = {}
    mock_transformer_cls.return_value.transform_record.return_value = [
        {"id": "x", "properties": {}, "acl": [], "content": {"value": ""}}
    ]

    client = MagicMock()
    client.batch_requests.return_value = [{"id": "0", "status": 200}]

    # Simulate Ctrl+X: set stop_requested during Account's Graph push.
    # This causes the stop check at the start of the Case iteration to break.
    dashboard = MagicMock()
    dashboard.stop_requested = False

    def _trigger_stop_during_account_push(obj_type, *args, **kwargs):
        if obj_type == "Account":
            dashboard.stop_requested = True

    dashboard.chunk_ingested.side_effect = _trigger_stop_during_account_push

    stats = ingest_content(test_config, client, since=None, dashboard=dashboard)

    # Account was processed (5 records), Case was skipped due to Ctrl+X
    assert stats.total_fetched == 5

    # Checkpoint must still exist (NOT cleared)
    cp = read_checkpoint(connector_id)
    assert cp is not None
    assert cp["completed"]["Account"] == 1


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_resume_skips_checkpointed_chunks(
    mock_transformer_cls, mock_acl_cls, mock_api, test_config, monkeypatch,
):
    """After a Ctrl+X stop, the next run should skip already-completed chunks."""
    connector_id = test_config.connector.id

    # Pre-plant a checkpoint: Account chunk 1 was completed
    write_checkpoint(connector_id, None, "Account", 1)

    # Same records as before (Account chunk 1 = 5 records)
    records = _make_records("Account", 5)
    mock_api.return_value = iter(records)
    mock_acl_cls.return_value.resolve.return_value = {"Account": {r["Id"]: [] for r in records}}
    mock_transformer_cls.return_value.handlers = {}
    mock_transformer_cls.return_value.transform_record.return_value = [
        {"id": "x", "properties": {}, "acl": [], "content": {"value": ""}}
    ]

    client = MagicMock()
    client.batch_requests.return_value = [{"id": "0", "status": 200}]

    stats = ingest_content(test_config, client, since=None, dashboard=None)

    # The chunk was skipped (checkpointed), so skipped_count should be 5
    assert stats.skipped_count == 5
    # No new records actually processed through ACL/Graph
    assert stats.success_count == 0

    # Checkpoint cleared after successful completion
    assert read_checkpoint(connector_id) is None


def test_full_flag_clears_checkpoint(test_config):
    """The --full flag should explicitly clear the checkpoint."""
    connector_id = test_config.connector.id
    write_checkpoint(connector_id, None, "Account", 5)
    assert read_checkpoint(connector_id) is not None

    clear_checkpoint(connector_id)
    assert read_checkpoint(connector_id) is None


@patch("graph.ingest.get_all_items_from_api")
@patch("graph.ingest.LegacyAclResolver")
@patch("graph.ingest.SalesforceItemTransformer")
def test_stale_checkpoint_ignored_when_since_changes(
    mock_transformer_cls, mock_acl_cls, mock_api, test_config, monkeypatch,
):
    """A checkpoint with a different `since` value should be ignored (not used for skipping)."""
    connector_id = test_config.connector.id
    from datetime import datetime, timezone

    # Checkpoint from a previous run with since=None
    write_checkpoint(connector_id, None, "Account", 3)

    records = _make_records("Account", 5)
    mock_api.return_value = iter(records)
    mock_acl_cls.return_value.resolve.return_value = {"Account": {r["Id"]: [] for r in records}}
    mock_transformer_cls.return_value.handlers = {}
    mock_transformer_cls.return_value.transform_record.return_value = [
        {"id": "x", "properties": {}, "acl": [], "content": {"value": ""}}
    ]

    client = MagicMock()
    client.batch_requests.return_value = [{"id": "0", "status": 200}]

    # Run with a different `since` — checkpoint should NOT match
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stats = ingest_content(test_config, client, since=since, dashboard=None)

    # All 5 records processed (checkpoint was ignored because since changed)
    assert stats.total_fetched == 5
    assert stats.skipped_count == 0
