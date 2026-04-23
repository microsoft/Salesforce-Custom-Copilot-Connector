"""Tests for the full-deployment command."""
from __future__ import annotations

import argparse
from unittest.mock import patch, MagicMock

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
def _deployment_patches(test_config):
    """Patch all external dependencies for cmd_full_deployment."""
    patches = {
        "load_config": patch("commands.deploy.load_config", return_value=test_config),
        "graph_client": patch("commands.deploy.GraphClient"),
        "ensure_connection": patch("commands.deploy.ensure_connection", return_value="created"),
        "ensure_schema": patch("commands.deploy.ensure_schema"),
        "set_search_settings": patch("commands.deploy.set_search_settings"),
        "is_connection_ready": patch("commands.deploy.is_connection_ready", return_value=True),
        "run_identity_sync": patch(
            "commands.deploy.run_identity_sync",
            return_value=SyncSessionStats(),
        ),
        "record_content_crawl": patch("commands.deploy.record_content_crawl"),
        "get_last_content_crawl_time": patch("commands.deploy.get_last_content_crawl_time", return_value=None),
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


def test_clamp_minimum():
    from commands.deploy import _clamp
    assert _clamp(5, 12, 168) == 12


def test_clamp_maximum():
    from commands.deploy import _clamp
    assert _clamp(200, 12, 168) == 168


def test_identity_sync_skipped_when_group_acl_disabled(mock_args, _deployment_patches):
    """When use_group_acl=False (default), run_identity_sync should NOT be called."""
    from commands.deploy import cmd_full_deployment
    cmd_full_deployment(mock_args)
    _deployment_patches["run_identity_sync"].assert_not_called()


def test_identity_sync_runs_when_group_acl_enabled(mock_args, _deployment_patches):
    """When use_group_acl=True, run_identity_sync should be called before ingestion."""
    config = _deployment_patches["load_config"].return_value
    # Create a new config with use_group_acl=True
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
    _deployment_patches["load_config"].return_value = new_config
    from commands.deploy import cmd_full_deployment
    result = cmd_full_deployment(mock_args)
    assert result is True
    _deployment_patches["run_identity_sync"].assert_called_once()


def test_clamp_incremental_hours():
    from commands.deploy import _clamp
    assert _clamp(0, 1, 168) == 1
    assert _clamp(4, 1, 168) == 4


def test_non_continuous_runs_once(mock_args, _deployment_patches):
    """Without --continuous, cmd_full_deployment returns after one run."""
    from commands.deploy import cmd_full_deployment
    mock_args.continuous = False
    result = cmd_full_deployment(mock_args)
    assert result is True
    assert _deployment_patches["ingest_content"].call_count == 1
