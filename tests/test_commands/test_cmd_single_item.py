"""Tests for the single-item command."""
from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False, item_id="500abc123")


@pytest.fixture
def _single_item_patches(test_config):
    patches = {
        "load_config": patch("commands.single_item.load_config", return_value=test_config),
        "graph_client": patch("commands.single_item.GraphClient"),
        "is_connection_ready": patch("commands.single_item.is_connection_ready", return_value=True),
        "ingest_content": patch(
            "commands.single_item.ingest_content",
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


def test_sets_debug_item_id_env(mock_args, _single_item_patches, monkeypatch):
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    from commands.single_item import cmd_single_item
    cmd_single_item(mock_args)
    assert os.environ.get("DEBUG_ITEM_ID") == "500abc123"


def test_calls_ingest_content(mock_args, _single_item_patches):
    from commands.single_item import cmd_single_item
    cmd_single_item(mock_args)
    _single_item_patches["ingest_content"].assert_called_once()


def test_connection_not_ready_returns_early(mock_args, _single_item_patches):
    _single_item_patches["is_connection_ready"].return_value = False
    from commands.single_item import cmd_single_item
    cmd_single_item(mock_args)
    _single_item_patches["ingest_content"].assert_not_called()


def test_item_id_passed_through(mock_args, _single_item_patches, monkeypatch):
    monkeypatch.delenv("DEBUG_ITEM_ID", raising=False)
    from commands.single_item import cmd_single_item
    cmd_single_item(mock_args)
    assert os.environ["DEBUG_ITEM_ID"] == "500abc123"
