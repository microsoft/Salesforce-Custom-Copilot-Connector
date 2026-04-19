"""Tests for the ingest-item command."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False, id="500abc123")


@pytest.fixture
def _ingest_item_patches(test_config):
    patches = {
        "load_config": patch("commands.ingest_item.load_config", return_value=test_config),
        "graph_client": patch("commands.ingest_item.GraphClient"),
        "is_connection_ready": patch("commands.ingest_item.is_connection_ready", return_value=True),
        "ingest_content": patch(
            "commands.ingest_item.ingest_content",
            return_value=IngestionStats(total_fetched=1, success_count=1),
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


def test_sets_debug_item_id_on_config(mock_args, _ingest_item_patches):
    from commands.ingest_item import cmd_ingest_item
    cmd_ingest_item(mock_args)
    call_args = _ingest_item_patches["ingest_content"].call_args
    config_used = call_args[0][0]
    assert config_used.debug_item_id == "500abc123"


def test_calls_ingest_content(mock_args, _ingest_item_patches):
    from commands.ingest_item import cmd_ingest_item
    cmd_ingest_item(mock_args)
    _ingest_item_patches["ingest_content"].assert_called_once()


def test_connection_not_ready(mock_args, _ingest_item_patches):
    _ingest_item_patches["is_connection_ready"].return_value = False
    from commands.ingest_item import cmd_ingest_item
    cmd_ingest_item(mock_args)
    _ingest_item_patches["ingest_content"].assert_not_called()
