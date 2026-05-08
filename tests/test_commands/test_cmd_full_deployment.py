"""Tests for the full-deployment command."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

import pytest

from graph.ingest import IngestionStats
from graph.identity_store import SyncSessionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False, continuous=False, full_crawl_hours=24, incremental_hours=4)


@pytest.fixture
def _deployment_patches(test_config):
    """Patch all external dependencies for cmd_full_deployment."""
    patches = {
        "load_config": patch("commands.deploy.load_config", return_value=test_config),
        "graph_client": patch("commands.deploy.GraphClient"),
        "ensure_connection": patch("commands.deploy.ensure_connection", return_value="created"),
        "ensure_schema": patch("commands.deploy.ensure_schema"),
        "set_search_settings": patch("commands.deploy.set_search_settings"),
        "is_connection_ready": patch("commands.deploy.is_connection_ready", return_value=True),
        "clear_checkpoint": patch("commands.deploy.clear_checkpoint"),
        "run_identity_sync": patch(
            "commands.deploy.run_identity_sync",
            return_value=SyncSessionStats(),
        ),
        "record_content_crawl": patch("commands.deploy.record_content_crawl"),
        "get_last_content_crawl_time": patch(
            "commands.deploy.get_last_content_crawl_time",
            return_value=None,
        ),
        "ingest_content": patch(
            "commands.deploy.ingest_content",
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


def test_successful_deployment(mock_args, _deployment_patches):
    from commands.deploy import cmd_full_deployment
    result = cmd_full_deployment(mock_args)
    assert result is True


def test_ensure_connection_returns_none(mock_args, _deployment_patches):
    _deployment_patches["ensure_connection"].return_value = None
    from commands.deploy import cmd_full_deployment
    result = cmd_full_deployment(mock_args)
    assert result is False


def test_connection_not_ready(mock_args, _deployment_patches):
    _deployment_patches["is_connection_ready"].return_value = False
    from commands.deploy import cmd_full_deployment
    result = cmd_full_deployment(mock_args)
    assert result is False


def test_ingest_raises_returns_false(mock_args, _deployment_patches):
    _deployment_patches["ingest_content"].side_effect = RuntimeError("boom")
    from commands.deploy import cmd_full_deployment
    result = cmd_full_deployment(mock_args)
    assert result is False


def test_setup_logging_is_called(mock_args, _deployment_patches):
    from commands.deploy import cmd_full_deployment
    cmd_full_deployment(mock_args)
    _deployment_patches["setup_logging"].assert_called_once()


def test_clamp_hours_minimum():
    from commands.deploy import _clamp_hours
    assert _clamp_hours(5) == 12


def test_clamp_hours_maximum():
    from commands.deploy import _clamp_hours
    assert _clamp_hours(200) == 168


def test_clamp_hours_within_range():
    from commands.deploy import _clamp_hours
    assert _clamp_hours(24) == 24


def test_non_continuous_runs_once(mock_args, _deployment_patches):
    """Without --continuous, cmd_full_deployment returns after one run."""
    from commands.deploy import cmd_full_deployment
    mock_args.continuous = False
    result = cmd_full_deployment(mock_args)
    assert result is True
    assert _deployment_patches["ingest_content"].call_count == 1


def test_identity_sync_skipped_on_incremental(mock_args, _deployment_patches, test_config):
    """Identity crawl should NOT run during incremental sync."""
    from salesforce.settings import AppConfig
    # Enable group ACL
    new_config = AppConfig(
        client_id=test_config.client_id, tenant_id=test_config.tenant_id,
        connector=test_config.connector, repo_root=test_config.repo_root,
        tuning=test_config.tuning, schema_config=test_config.schema_config,
        owd_field_map=test_config.owd_field_map, parent_map=test_config.parent_map,
        owd_overrides=test_config.owd_overrides,
        use_new_acl_engine=False, use_group_acl=True,
        debug_object_type=None, debug_item_id=None,
    )
    _deployment_patches["load_config"].return_value = new_config

    # Simulate incremental by calling _run_full_deployment with since
    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    # Identity sync should NOT be called for incremental
    _deployment_patches["run_identity_sync"].assert_not_called()


def test_identity_sync_runs_on_full(mock_args, _deployment_patches, test_config):
    """Identity crawl SHOULD run during full sync."""
    from salesforce.settings import AppConfig
    new_config = AppConfig(
        client_id=test_config.client_id, tenant_id=test_config.tenant_id,
        connector=test_config.connector, repo_root=test_config.repo_root,
        tuning=test_config.tuning, schema_config=test_config.schema_config,
        owd_field_map=test_config.owd_field_map, parent_map=test_config.parent_map,
        owd_overrides=test_config.owd_overrides,
        use_new_acl_engine=False, use_group_acl=True,
        debug_object_type=None, debug_item_id=None,
    )
    _deployment_patches["load_config"].return_value = new_config

    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=None)

    _deployment_patches["run_identity_sync"].assert_called_once()


def test_full_deployment_records_content_crawl(mock_args, _deployment_patches):
    """Content crawl stats should be recorded in SQLite after ingestion."""
    from commands.deploy import cmd_full_deployment
    cmd_full_deployment(mock_args)
    _deployment_patches["record_content_crawl"].assert_called_once()
    call_args = _deployment_patches["record_content_crawl"].call_args
    assert call_args.kwargs.get("sync_type") == "full" or call_args[1].get("sync_type") == "full"


def test_incremental_records_sync_type(mock_args, _deployment_patches):
    """Incremental runs should record sync_type='incremental'."""
    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    call_args = _deployment_patches["record_content_crawl"].call_args
    assert "incremental" in str(call_args)


def test_incremental_does_not_clear_checkpoint(mock_args, _deployment_patches):
    """Incremental runs should not clear the checkpoint."""
    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _deployment_patches["clear_checkpoint"].assert_not_called()


def test_full_clears_checkpoint(mock_args, _deployment_patches):
    """Full runs should clear the checkpoint."""
    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=None)
    _deployment_patches["clear_checkpoint"].assert_called_once()


def test_ingest_content_receives_since(mock_args, _deployment_patches):
    """ingest_content should receive the 'since' parameter."""
    since_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
    from commands.deploy import _run_full_deployment
    _run_full_deployment(mock_args, since=since_time)
    call_args = _deployment_patches["ingest_content"].call_args
    assert call_args.kwargs.get("since") == since_time or call_args[1].get("since") == since_time
