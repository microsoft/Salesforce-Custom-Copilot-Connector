# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for graph.connection functions."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from graph.client import GraphApiError, EXTERNAL_CONNECTIONS_PATH
from graph.connection import (
    create_connection,
    get_connection,
    connection_exists,
    ensure_connection,
    is_connection_ready,
    set_search_settings,
    clear_connection_items,
    delete_connection,
)


@pytest.fixture
def mock_client():
    return MagicMock()


# ---------------------------------------------------------------------------
# create_connection
# ---------------------------------------------------------------------------

def test_create_connection_posts_payload(test_config, mock_client):
    create_connection(test_config, mock_client)
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs.get("json_body") or call_kwargs[1].get("json_body")
    assert body["id"] == test_config.connector.id
    assert body["name"] == test_config.connector.name


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------

def test_get_connection_calls_get(test_config, mock_client):
    mock_client.get.return_value = {"id": test_config.connector.id, "state": "ready"}
    result = get_connection(test_config, mock_client)
    assert result["state"] == "ready"
    mock_client.get.assert_called_once_with(f"{EXTERNAL_CONNECTIONS_PATH}/{test_config.connector.id}")


# ---------------------------------------------------------------------------
# connection_exists
# ---------------------------------------------------------------------------

def test_connection_exists_true(test_config, mock_client):
    mock_client.get.return_value = {"id": "x"}
    assert connection_exists(test_config, mock_client) is True


def test_connection_exists_false_on_error(test_config, mock_client):
    mock_client.get.side_effect = GraphApiError(404, "not found")
    assert connection_exists(test_config, mock_client) is False


# ---------------------------------------------------------------------------
# ensure_connection
# ---------------------------------------------------------------------------

def test_ensure_connection_existing(test_config, mock_client):
    mock_client.get.return_value = {"id": test_config.connector.id}
    result = ensure_connection(test_config, mock_client, time.monotonic())
    assert result == "existing"


def test_ensure_connection_created_on_404(test_config, mock_client):
    mock_client.get.side_effect = GraphApiError(404, "not found")
    mock_client.post.return_value = {}
    result = ensure_connection(test_config, mock_client, time.monotonic())
    assert result == "created"


def test_ensure_connection_returns_none_on_other_error(test_config, mock_client):
    mock_client.get.side_effect = RuntimeError("unexpected")
    result = ensure_connection(test_config, mock_client, time.monotonic())
    assert result is None


# ---------------------------------------------------------------------------
# is_connection_ready
# ---------------------------------------------------------------------------

@patch("graph.connection.schema_exists", return_value=True)
def test_is_connection_ready_true(mock_schema, test_config, mock_client):
    mock_client.get.return_value = {"state": "ready"}
    assert is_connection_ready(test_config, mock_client) is True


@patch("graph.connection.schema_exists", return_value=True)
def test_is_connection_ready_false_when_not_ready(mock_schema, test_config, mock_client):
    mock_client.get.return_value = {"state": "draft"}
    assert is_connection_ready(test_config, mock_client) is False


@patch("graph.connection.schema_exists", return_value=False)
def test_is_connection_ready_false_when_no_schema(mock_schema, test_config, mock_client):
    mock_client.get.return_value = {"state": "ready"}
    assert is_connection_ready(test_config, mock_client) is False


# ---------------------------------------------------------------------------
# set_search_settings
# ---------------------------------------------------------------------------

def test_set_search_settings_skips_when_present(test_config, mock_client):
    mock_client.get.return_value = {"searchSettings": {"searchResultTemplates": []}}
    set_search_settings(test_config, mock_client)
    mock_client.patch.assert_not_called()


def test_set_search_settings_patches_when_absent(test_config, mock_client):
    mock_client.get.return_value = {}
    set_search_settings(test_config, mock_client)
    mock_client.patch.assert_called_once()


# ---------------------------------------------------------------------------
# clear_connection_items
# ---------------------------------------------------------------------------

def test_clear_connection_items_deletes_all(test_config, mock_client):
    mock_client.paginate.return_value = iter([{"id": "item1"}, {"id": "item2"}])
    count = clear_connection_items(test_config, mock_client)
    assert count == 2
    assert mock_client.delete.call_count == 2


# ---------------------------------------------------------------------------
# delete_connection
# ---------------------------------------------------------------------------

def test_delete_connection_success(test_config, mock_client):
    mock_client.get.return_value = {"id": test_config.connector.id}
    result = delete_connection(test_config, mock_client, time.monotonic())
    assert result is True
    mock_client.delete.assert_called_once()
