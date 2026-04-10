from __future__ import annotations

from typing import Any, Iterator
import logging
import time

from azure.identity import DefaultAzureCredential
import requests


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

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
    def __init__(self, delay_seconds: int = 60, max_retries: int = 4, retry_backoff_base: int = 2):
        self._credential = DefaultAzureCredential()
        self._session = requests.Session()
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

    @staticmethod
    def _normalize_url(path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{GRAPH_BASE_URL}{path_or_url}"

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
