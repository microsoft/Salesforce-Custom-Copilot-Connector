"""Tests for the single-object command."""
from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False, object_type="Case")


@pytest.fixture
def _single_object_patches(test_config):
    patches = {
        "load_config": patch("commands.single_object.load_config", return_value=test_config),
        "graph_client": patch("commands.single_object.GraphClient"),
        "is_connection_ready": patch("commands.single_object.is_connection_ready", return_value=True),
        "ingest_content": patch(
            "commands.single_object.ingest_content",
            return_value=IngestionStats(total_fetched=3, success_count=3),
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


def test_sets_debug_object_type_env(mock_args, _single_object_patches, monkeypatch):
    monkeypatch.delenv("DEBUG_OBJECT_TYPE", raising=False)
    from commands.single_object import cmd_single_object
    cmd_single_object(mock_args)
    assert os.environ.get("DEBUG_OBJECT_TYPE") == "Case"


def test_calls_ingest_content(mock_args, _single_object_patches):
    from commands.single_object import cmd_single_object
    cmd_single_object(mock_args)
    _single_object_patches["ingest_content"].assert_called_once()


def test_connection_not_ready_returns_early(mock_args, _single_object_patches):
    _single_object_patches["is_connection_ready"].return_value = False
    from commands.single_object import cmd_single_object
    cmd_single_object(mock_args)
    _single_object_patches["ingest_content"].assert_not_called()
