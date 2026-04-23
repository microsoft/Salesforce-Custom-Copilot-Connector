"""Tests for the ingest command."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats
from graph.identity_store import SyncSessionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(
        verbose=False, continuous=False,
        full_crawl_hours=24, incremental_hours=4,
    )


@pytest.fixture
def _ingest_patches(test_config):
    patches = {
        "load_config": patch("commands.ingest.load_config", return_value=test_config),
        "graph_client": patch("commands.ingest.GraphClient"),
        "is_connection_ready": patch("commands.ingest.is_connection_ready", return_value=True),
        "run_identity_sync": patch(
            "commands.ingest.run_identity_sync",
            return_value=SyncSessionStats(),
        ),
        "record_content_crawl": patch("commands.ingest.record_content_crawl"),
        "get_last_content_crawl_time": patch("commands.ingest.get_last_content_crawl_time", return_value=None),
        "ingest_content": patch(
            "commands.ingest.ingest_content",
            return_value=IngestionStats(total_fetched=5, success_count=5),
        ),
        "setup_logging": patch(
            "commands.setup_logging",
            return_value=("fake_log.log", "fake_summary.log"),
        ),
        "write_summary": patch("commands.write_summary"),
    }
    mocks = {}
    for name, p in patches.items():
        mocks[name] = p.start()
    yield mocks
    for p in patches.values():
        p.stop()


def test_successful_ingest(mock_args, _ingest_patches):
    from commands.ingest import cmd_ingest
    result = cmd_ingest(mock_args)
    assert result is True


def test_connection_not_ready(mock_args, _ingest_patches):
    _ingest_patches["is_connection_ready"].return_value = False
    from commands.ingest import cmd_ingest
    result = cmd_ingest(mock_args)
    assert result is False


def test_ingest_with_failures(mock_args, _ingest_patches):
    _ingest_patches["ingest_content"].return_value = IngestionStats(
        total_fetched=5, success_count=3, failed_count=2, failed_ids=["a", "b"],
    )
    from commands.ingest import cmd_ingest
    result = cmd_ingest(mock_args)
    assert result is False


def test_clamp_minimum():
    from commands.ingest import _clamp
    assert _clamp(5, 12, 168) == 12


def test_clamp_maximum():
    from commands.ingest import _clamp
    assert _clamp(200, 12, 168) == 168


def test_clamp_within_range():
    from commands.ingest import _clamp
    assert _clamp(24, 12, 168) == 24


def test_non_continuous_runs_once(mock_args, _ingest_patches):
    """Without --continuous, cmd_ingest returns after one run."""
    from commands.ingest import cmd_ingest
    mock_args.continuous = False
    result = cmd_ingest(mock_args)
    assert result is True
    assert _ingest_patches["ingest_content"].call_count == 1


def test_identity_sync_skipped_when_group_acl_disabled(mock_args, _ingest_patches):
    """When use_group_acl=False (default), run_identity_sync should NOT be called."""
    from commands.ingest import cmd_ingest
    cmd_ingest(mock_args)
    _ingest_patches["run_identity_sync"].assert_not_called()


def test_identity_sync_runs_when_group_acl_enabled(mock_args, _ingest_patches):
    """When use_group_acl=True, run_identity_sync should be called before ingestion."""
    config = _ingest_patches["load_config"].return_value
    from salesforce.settings import AppConfig
    new_config = AppConfig(
        client_id=config.client_id,
        tenant_id=config.tenant_id,
        connector=config.connector,
        repo_root=config.repo_root,
        tuning=config.tuning,
        schema_config=config.schema_config,
        owd_field_map=config.owd_field_map,
        parent_map=config.parent_map,
        owd_overrides=config.owd_overrides,
        use_new_acl_engine=config.use_new_acl_engine,
        use_group_acl=True,
        debug_object_type=None,
        debug_item_id=None,
    )
    _ingest_patches["load_config"].return_value = new_config
    from commands.ingest import cmd_ingest
    result = cmd_ingest(mock_args)
    assert result is True
    _ingest_patches["run_identity_sync"].assert_called_once()
