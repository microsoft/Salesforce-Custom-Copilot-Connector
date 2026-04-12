"""Tests for the full-deployment command."""
from __future__ import annotations

import argparse
from unittest.mock import patch, MagicMock

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False)


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
