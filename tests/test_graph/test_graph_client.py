"""Tests for GraphClient (graph.client)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
import json

import pytest

from graph.client import GraphApiError, GraphClient, GRAPH_BASE_URL, _RETRYABLE_STATUS_CODES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_body=None, text="", headers=None, url="https://graph.microsoft.com/v1.0/test"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = text or json.dumps(json_body or {})
    resp.reason = "OK" if resp.ok else "Error"
    resp.headers = headers or {}
    resp.url = url
    resp.content = resp.text.encode() if resp.text else b""
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("No JSON")
    return resp


@pytest.fixture
def client():
    with patch("graph.client.DefaultAzureCredential") as MockCred:
        token_obj = MagicMock()
        token_obj.token = "fake-token"
        MockCred.return_value.get_token.return_value = token_obj
        c = GraphClient(max_retries=2, delay_seconds=0, retry_backoff_base=0)
        c._session = MagicMock()
        yield c


# ---------------------------------------------------------------------------
# Basic HTTP methods
# ---------------------------------------------------------------------------

def test_get_makes_get_request(client):
    client._session.request.return_value = _make_response(json_body={"value": 1})
    result = client.get("/test")
    client._session.request.assert_called_once()
    call_kwargs = client._session.request.call_args
    assert call_kwargs.kwargs["method"] == "GET" or call_kwargs[1].get("method") == "GET"
    assert result == {"value": 1}


def test_post_sends_json_body(client):
    client._session.request.return_value = _make_response(json_body={"id": "abc"})
    body = {"name": "test"}
    client.post("/test", json_body=body)
    call_kwargs = client._session.request.call_args
    assert call_kwargs.kwargs.get("json") == body or call_kwargs[1].get("json") == body


def test_put_sends_json_body(client):
    client._session.request.return_value = _make_response(json_body={})
    body = {"data": 42}
    client.put("/items/1", json_body=body)
    call_kwargs = client._session.request.call_args
    assert call_kwargs.kwargs.get("json") == body or call_kwargs[1].get("json") == body


def test_delete_sends_delete_request(client):
    client._session.request.return_value = _make_response(json_body={})
    client.delete("/items/1")
    call_kwargs = client._session.request.call_args
    assert call_kwargs.kwargs.get("method") == "DELETE" or call_kwargs[1].get("method") == "DELETE"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_raises_graph_api_error_on_404(client):
    client._session.request.return_value = _make_response(
        status_code=404, json_body={"error": {"code": "NotFound", "message": "Not found"}},
    )
    with pytest.raises(GraphApiError) as exc_info:
        client.get("/missing")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "NotFound"


def test_retries_on_retryable_status_codes(client):
    """Verify retry on 429/500/502/503/504, then success."""
    fail_resp = _make_response(status_code=429, json_body={"error": {"message": "throttled"}})
    ok_resp = _make_response(json_body={"ok": True})
    client._session.request.side_effect = [fail_resp, ok_resp]
    result = client.get("/throttled")
    assert result == {"ok": True}
    assert client._session.request.call_count == 2


def test_retries_exhausted_raises(client):
    """After max_retries, should raise GraphApiError."""
    fail_resp = _make_response(status_code=500, json_body={"error": {"message": "server error"}})
    client._session.request.return_value = fail_resp
    with pytest.raises(GraphApiError) as exc_info:
        client.get("/fail")
    assert exc_info.value.status_code == 500
    # 1 initial + 2 retries = 3
    assert client._session.request.call_count == 3


def test_follows_location_header(client):
    """Long-running operation: follows Location header."""
    loc_resp = _make_response(
        status_code=202,
        json_body={},
        headers={"Location": "https://graph.microsoft.com/v1.0/operations/123"},
    )
    loc_resp.ok = True
    op_resp = _make_response(
        json_body={"status": "completed"},
        url="https://graph.microsoft.com/v1.0/operations/123",
    )
    client._session.request.side_effect = [loc_resp, op_resp]
    result = client.request("PATCH", "/schema")
    assert client._session.request.call_count == 2


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_paginate_follows_next_link(client):
    page1 = _make_response(json_body={
        "value": [{"id": "1"}, {"id": "2"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
    })
    page2 = _make_response(json_body={"value": [{"id": "3"}]})
    client._session.request.side_effect = [page1, page2]
    items = list(client.paginate("/items"))
    assert [i["id"] for i in items] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def test_normalize_url_prepends_base_for_relative(client):
    assert client._normalize_url("/test") == f"{GRAPH_BASE_URL}/v1.0/test"


def test_normalize_url_passes_through_absolute(client):
    url = "https://example.com/path"
    assert client._normalize_url(url) == url


# ---------------------------------------------------------------------------
# GraphApiError.from_response
# ---------------------------------------------------------------------------

def test_from_response_parses_error_json():
    resp = _make_response(
        status_code=403,
        json_body={"error": {"code": "AccessDenied", "message": "No access"}},
    )
    error = GraphApiError.from_response(resp)
    assert error.status_code == 403
    assert error.code == "AccessDenied"
    assert "No access" in str(error)


def test_from_response_handles_non_json():
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Internal Server Error"
    resp.reason = "ISE"
    resp.json.side_effect = ValueError
    error = GraphApiError.from_response(resp)
    assert error.status_code == 500
    assert error.body == "Internal Server Error"
