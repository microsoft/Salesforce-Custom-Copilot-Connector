"""Tests for the ingest command."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False)


@pytest.fixture
def _ingest_patches(test_config):
    patches = {
        "load_config": patch("commands.ingest.load_config", return_value=test_config),
        "graph_client": patch("commands.ingest.GraphClient"),
        "is_connection_ready": patch("commands.ingest.is_connection_ready", return_value=True),
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
