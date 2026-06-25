# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Microsoft Graph API client with retry and pagination support.

This module provides :class:`GraphClient`, a thin wrapper around the Microsoft
Graph REST API that handles:

* **Authentication** — acquires tokens via ``DefaultAzureCredential`` (supports
  Azure CLI, managed identity, environment variables, etc.).
* **Retry with exponential back-off** — automatically retries on transient HTTP
  errors (429, 500, 502, 503, 504) up to a configurable number of attempts.
* **Long-running operations** — follows ``Location`` headers returned by
  asynchronous Graph operations (e.g. schema provisioning) and polls until the
  operation completes.
* **Pagination** — the :meth:`GraphClient.paginate` iterator transparently
  follows ``@odata.nextLink`` across paged result sets.

Constants
---------
GRAPH_BASE_URL : str
    ``https://graph.microsoft.com``
EXTERNAL_CONNECTIONS_PATH : str
    ``/external/connections`` — base path for the External Connectors API.

Classes
-------
GraphApiError
    Raised when the Graph API returns a non-success status that is not retryable
    (or retries have been exhausted).  Carries ``status_code``, ``code``, and
    the raw ``body``.
GraphClient
    Stateful HTTP client.  Instantiate once per command and reuse for all Graph
    calls within that run.
"""

from __future__ import annotations

from typing import Any, Iterator
import logging
import threading
import time

from azure.core.credentials import AccessToken
from azure.identity import DefaultAzureCredential
import requests


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com"
GRAPH_DEFAULT_API_VERSION = "v1.0"
EXTERNAL_CONNECTIONS_PATH = "/external/connections"

# Hard limit imposed by the Graph API — do not exceed.
# See: https://learn.microsoft.com/en-us/graph/json-batching#batch-size-limitations
GRAPH_BATCH_MAX_SIZE = 20

# Transient HTTP status codes that should be retried
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

logger = logging.getLogger("salesforce_connector")

# Suppress noisy Azure Identity token-acquisition logs
logging.getLogger("azure.identity").setLevel(logging.WARNING)


class GraphApiError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, code: str | None = None, body: Any = None):
        """Initialize with HTTP status code, message, optional error code and response body."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body

    @classmethod
    def from_response(cls, response: requests.Response) -> "GraphApiError":
        """Create a GraphApiError from an HTTP response, extracting error details from JSON body."""
        code = None
        body: Any = response.text
        message = response.text or response.reason

        try:
            body = response.json()
        except ValueError:
            return cls(response.status_code, message, body=body)

        if isinstance(body, dict):
            error = body.get("error") or {}
            code = error.get("code")
            message = error.get("message") or message

        return cls(response.status_code, message, code=code, body=body)


class GraphClient:
    def __init__(
        self,
        api_version: str = GRAPH_DEFAULT_API_VERSION,
        delay_seconds: int = 60,
        max_retries: int = 4,
        retry_backoff_base: int = 2,
    ):
        """Initialize the Graph client with authentication, retry, and polling settings."""
        self._credential = DefaultAzureCredential()
        self._token: AccessToken | None = None
        self._token_lock = threading.Lock()
        self._local = threading.local()
        self._base_url = f"{GRAPH_BASE_URL}/{api_version}"
        self._delay_seconds = delay_seconds
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base

    def get(self, path_or_url: str, *, headers: dict[str, str] | None = None) -> Any:
        """Send a GET request to the Graph API."""
        return self.request("GET", path_or_url, headers=headers)

    def post(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a POST request to the Graph API."""
        return self.request("POST", path_or_url, json_body=json_body, headers=headers)

    def patch(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a PATCH request to the Graph API."""
        return self.request("PATCH", path_or_url, json_body=json_body, headers=headers)

    def put(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a PUT request to the Graph API."""
        return self.request("PUT", path_or_url, json_body=json_body, headers=headers)

    def delete(self, path_or_url: str, *, headers: dict[str, str] | None = None) -> Any:
        """Send a DELETE request to the Graph API."""
        return self.request("DELETE", path_or_url, headers=headers)

    def batch_requests(
        self,
        requests_payload: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Send up to ``GRAPH_BATCH_MAX_SIZE`` (20) requests in a single ``POST /$batch`` call.

        Each element in *requests_payload* must be a dict with:
            ``id``      — unique string identifier within this batch
            ``method``  — HTTP verb (e.g. ``"PUT"``, ``"DELETE"``)
            ``url``     — relative path without the API version prefix
                          (e.g. ``/external/connections/{id}/items/{itemId}``)
            ``headers`` — (optional) dict of extra headers; required when ``body`` is set
            ``body``    — (optional) JSON-serialisable request body

        Returns a list of response dicts, each with ``id``, ``status``, ``headers``,
        and ``body`` fields.  The outer ``POST /$batch`` is retried on transient
        errors using the standard retry logic; individual per-item failures (non-2xx
        ``status``) are returned as-is for the caller to handle.

        Raises ``ValueError`` if more than ``GRAPH_BATCH_MAX_SIZE`` requests are passed.
        """
        if len(requests_payload) > GRAPH_BATCH_MAX_SIZE:
            raise ValueError(
                f"Batch size {len(requests_payload)} exceeds the Graph API maximum of {GRAPH_BATCH_MAX_SIZE}. "
                "Split the payload into smaller chunks before calling batch_requests()."
            )

        response = self.post("/$batch", json_body={"requests": requests_payload})
        if isinstance(response, dict):
            return response.get("responses", [])
        return []

    def paginate(self, path_or_url: str) -> Iterator[dict[str, Any]]:
        """Iterate over all pages of a Graph API collection, yielding each item dict."""
        next_url: str | None = path_or_url
        while next_url:
            payload = self.get(next_url)
            if not isinstance(payload, dict):
                return
            for value in payload.get("value", []):
                if isinstance(value, dict):
                    yield value
            next_url = payload.get("@odata.nextLink")

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute an HTTP request with retry, long-running operation polling, and error handling.

        Args:
            method: HTTP method (GET, POST, PATCH, PUT, DELETE).
            path_or_url: Relative path or absolute URL.
            json_body: Optional JSON-serializable request body.
            headers: Optional extra headers merged with auth headers.

        Returns:
            Parsed response body (dict, str, or empty dict for no-content responses).

        Raises:
            GraphApiError: On non-retryable errors or after retries are exhausted.
        """
        url = self._normalize_url(path_or_url)
        request_headers = self._get_headers(headers)
        attempt = 0

        while True:
            response = self._session.request(
                method=method,
                url=url,
                headers=request_headers,
                json=json_body,
                timeout=120,
            )

            location = response.headers.get("Location") or response.headers.get("location")
            if location and "/operations/" in location:
                time.sleep(self._delay_seconds)
                method = "GET"
                url = self._normalize_url(location)
                json_body = None
                request_headers = self._get_headers(headers)
                continue

            if not response.ok:
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = self._retry_backoff_base * (2 ** attempt)
                    else:
                        wait = self._retry_backoff_base * (2 ** attempt)
                    attempt += 1
                    logger.warning(
                        "Graph API transient error %d for %s %s — retrying in %.0fs (attempt %d/%d)",
                        response.status_code,
                        method,
                        url,
                        wait,
                        attempt,
                        self._max_retries,
                    )
                    time.sleep(wait)
                    request_headers = self._get_headers(headers)  # refresh token
                    continue
                raise GraphApiError.from_response(response)

            payload = self._parse_response(response)

            if "/operations/" in response.url:
                status = payload.get("status") if isinstance(payload, dict) else None
                if status == "inprogress":
                    time.sleep(self._delay_seconds)
                    method = "GET"
                    url = self._normalize_url(response.url)
                    json_body = None
                    request_headers = self._get_headers(headers)
                    continue

            return payload

    @property
    def _session(self) -> requests.Session:
        """Thread-local session so concurrent batch calls don't share state."""
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    @_session.setter
    def _session(self, value: requests.Session) -> None:
        self._local.session = value

    def _get_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Build request headers with a cached Bearer token, refreshed 5 min before expiry."""
        _REFRESH_BUFFER = 300  # refresh 5 minutes before expiry
        with self._token_lock:
            if self._token is None or self._token.expires_on - time.time() < _REFRESH_BUFFER:
                self._token = self._credential.get_token(GRAPH_SCOPE)
        base_headers = {
            "Authorization": f"Bearer {self._token.token}",
            "Accept": "application/json",
        }
        if headers:
            base_headers.update(headers)
        return base_headers

    def _normalize_url(self, path_or_url: str) -> str:
        """Convert a relative path to a full Graph API URL, passing absolute URLs through."""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self._base_url}{path_or_url}"

    @staticmethod
    def _parse_response(response: requests.Response) -> Any:
        """Parse the HTTP response body as JSON, falling back to plain text."""
        if not response.content:
            return {}

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()

        try:
            return response.json()
        except ValueError:
            return response.text
