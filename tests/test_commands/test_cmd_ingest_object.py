# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the ingest-object command."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from graph.ingest import IngestionStats


@pytest.fixture
def mock_args():
    return argparse.Namespace(verbose=False, type="Case")


@pytest.fixture
def _ingest_object_patches(test_config):
    patches = {
        "load_config": patch("commands.ingest_object.load_config", return_value=test_config),
        "graph_client": patch("commands.ingest_object.GraphClient"),
        "is_connection_ready": patch("commands.ingest_object.is_connection_ready", return_value=True),
        "ingest_content": patch(
            "commands.ingest_object.ingest_content",
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


def test_sets_debug_object_type_on_config(mock_args, _ingest_object_patches):
    from commands.ingest_object import cmd_ingest_object
    cmd_ingest_object(mock_args)
    call_args = _ingest_object_patches["ingest_content"].call_args
    config_used = call_args[0][0]
    assert config_used.debug_object_type == "Case"


def test_calls_ingest_content(mock_args, _ingest_object_patches):
    from commands.ingest_object import cmd_ingest_object
    cmd_ingest_object(mock_args)
    _ingest_object_patches["ingest_content"].assert_called_once()


def test_connection_not_ready(mock_args, _ingest_object_patches):
    _ingest_object_patches["is_connection_ready"].return_value = False
    from commands.ingest_object import cmd_ingest_object
    cmd_ingest_object(mock_args)
    _ingest_object_patches["ingest_content"].assert_not_called()
