"""Tests for graph.schema functions."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from graph.client import GraphApiError, EXTERNAL_CONNECTIONS_PATH
from graph.schema import create_schema, schema_exists, ensure_schema


@pytest.fixture
def mock_client():
    return MagicMock()


def test_create_schema_patches_with_payload(test_config, mock_client):
    create_schema(test_config, mock_client)
    mock_client.patch.assert_called_once()
    call_kwargs = mock_client.patch.call_args
    body = call_kwargs.kwargs.get("json_body") or call_kwargs[1].get("json_body")
    assert body["baseType"] == "microsoft.graph.externalItem"
    assert body["properties"] == test_config.connector.schema


def test_schema_exists_true(test_config, mock_client):
    mock_client.get.return_value = {"properties": []}
    assert schema_exists(test_config, mock_client) is True


def test_schema_exists_false_on_error(test_config, mock_client):
    mock_client.get.side_effect = GraphApiError(404, "not found")
    assert schema_exists(test_config, mock_client) is False


@patch("graph.schema.delay")
def test_ensure_schema_returns_if_exists(mock_delay, test_config, mock_client):
    mock_client.get.return_value = {"properties": []}
    ensure_schema(test_config, mock_client)
    mock_client.patch.assert_not_called()


@patch("graph.schema.delay")
def test_ensure_schema_creates_when_404(mock_delay, test_config, mock_client):
    mock_client.get.side_effect = GraphApiError(404, "not found")
    ensure_schema(test_config, mock_client)
    mock_client.patch.assert_called_once()
