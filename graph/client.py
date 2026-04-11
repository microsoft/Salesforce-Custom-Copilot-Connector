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
import time

from azure.identity import DefaultAzureCredential
import requests


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com"
GRAPH_DEFAULT_API_VERSION = "v1.0"
EXTERNAL_CONNECTIONS_PATH = "/external/connections"

# Transient HTTP status codes that should be retried
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

logger = logging.getLogger("salesforce_connector")


class GraphApiError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, code: str | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body

    @classmethod
    def from_response(cls, response: requests.Response) -> "GraphApiError":
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
        self._credential = DefaultAzureCredential()
        self._session = requests.Session()
        self._base_url = f"{GRAPH_BASE_URL}/{api_version}"
        self._delay_seconds = delay_seconds
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base

    def get(self, path_or_url: str, *, headers: dict[str, str] | None = None) -> Any:
        return self.request("GET", path_or_url, headers=headers)

    def post(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.request("POST", path_or_url, json_body=json_body, headers=headers)

    def patch(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.request("PATCH", path_or_url, json_body=json_body, headers=headers)

    def put(
        self,
        path_or_url: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.request("PUT", path_or_url, json_body=json_body, headers=headers)

    def delete(self, path_or_url: str, *, headers: dict[str, str] | None = None) -> Any:
        return self.request("DELETE", path_or_url, headers=headers)

    def paginate(self, path_or_url: str) -> Iterator[dict[str, Any]]:
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

    def _get_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        token = self._credential.get_token(GRAPH_SCOPE).token
        base_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if headers:
            base_headers.update(headers)
        return base_headers

    def _normalize_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self._base_url}{path_or_url}"

    @staticmethod
    def _parse_response(response: requests.Response) -> Any:
        if not response.content:
            return {}

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()

        try:
            return response.json()
        except ValueError:
            return response.text
