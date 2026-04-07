"""
acl_engine/sf_client.py
-----------------------
Thin, async-friendly Salesforce REST client.

Responsibilities
----------------
* Execute SOQL queries against the standard (/query) or Tooling (/tooling/query)
  API endpoint.
* Transparently page through multi-page result sets via nextRecordsUrl.
* Fetch sObject field metadata via the describe endpoint (used by ShareFetcher
  and the resolver's parent-field discovery).

No caching, no retry logic – kept intentionally simple so that the callers
(OWDFetcher, ShareFetcher, the various handlers) stay in full control.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import requests

logger = logging.getLogger("salesforce_connector.acl_engine")

_TIMEOUT_SECS = 60


class SalesforceClient:
    """
    All HTTP calls to Salesforce go through this class.

    Parameters
    ----------
    instance_url  : Full URL of the org  (e.g. "https://myorg.my.salesforce.com")
    api_version   : API version string   (e.g. "60.0")
    access_token  : OAuth Bearer token
    """

    def __init__(self, instance_url: str, api_version: str, access_token: str) -> None:
        self._base_url = instance_url.rstrip("/")
        # Normalise "v60.0" → "60.0" so URL construction (which prepends 'v') is correct
        self._api_version = api_version.lstrip("v")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    # ── Public async API ──────────────────────────────────────────────────────

    async def query(self, soql: str, *, tooling: bool = False) -> dict[str, Any]:
        """Execute a SOQL query and return the raw Salesforce response dict."""
        return await asyncio.to_thread(self._sync_query, soql, tooling)

    async def query_all(self, soql: str, *, tooling: bool = False) -> list[dict[str, Any]]:
        """
        Execute a SOQL query and collect *all* records, following nextRecordsUrl
        pagination automatically.

        Returns a flat list of record dicts.
        """
        return await asyncio.to_thread(self._sync_query_all, soql, tooling)

    async def describe_sobject(self, sobject_name: str) -> dict[str, Any]:
        """
        Fetch the full describe metadata for an sObject.

        Used by ShareFetcher to discover the parent reference field name on
        <ObjectType>Share objects, and to discover the access-level field.
        """
        return await asyncio.to_thread(self._sync_describe, sobject_name)

    async def get_sobject(self, sobject_name: str, record_id: str) -> dict[str, Any]:
        """
        Fetch a single record via the sObject REST endpoint.

        Equivalent to:
            GET /services/data/vXX.X/sobjects/<sobject_name>/<record_id>

        Returns the full record payload as a dict.  Raises RuntimeError on
        any non-2xx response.

        curl equivalent:
            GET /services/data/v60.0/sobjects/User/<record_id>
            Authorization: Bearer <access_token>
        """
        return await asyncio.to_thread(self._sync_get_sobject, sobject_name, record_id)

    # ── Sync helpers (run in a thread so the event loop is never blocked) ──────

    def _sync_query(self, soql: str, tooling: bool) -> dict[str, Any]:
        endpoint = "tooling/query" if tooling else "query"
        url = f"{self._base_url}/services/data/v{self._api_version}/{endpoint}"
        response = requests.get(
            url,
            headers=self._headers,
            params={"q": soql},
            timeout=_TIMEOUT_SECS,
        )
        if not response.ok:
            raise RuntimeError(
                f"Salesforce {'tooling ' if tooling else ''}query failed "
                f"[{response.status_code}]: {response.text}"
            )
        return response.json()

    def _sync_query_all(self, soql: str, tooling: bool) -> list[dict[str, Any]]:
        """Fetch all pages for a SOQL query."""
        records: list[dict[str, Any]] = []
        result = self._sync_query(soql, tooling)
        records.extend(result.get("records", []))

        while not result.get("done", True) and result.get("nextRecordsUrl"):
            # nextRecordsUrl is a relative path – prepend the base URL
            next_path: str = result["nextRecordsUrl"]
            if not next_path.startswith("http"):
                next_path = self._base_url + next_path

            response = requests.get(next_path, headers=self._headers, timeout=_TIMEOUT_SECS)
            if not response.ok:
                raise RuntimeError(
                    f"Salesforce pagination failed [{response.status_code}]: {response.text}"
                )
            result = response.json()
            records.extend(result.get("records", []))

        return records

    def _sync_describe(self, sobject_name: str) -> dict[str, Any]:
        url = f"{self._base_url}/services/data/v{self._api_version}/sobjects/{sobject_name}/describe"
        response = requests.get(url, headers=self._headers, timeout=_TIMEOUT_SECS)
        if not response.ok:
            raise RuntimeError(
                f"Salesforce describe({sobject_name}) failed "
                f"[{response.status_code}]: {response.text}"
            )
        return response.json()

    def _sync_get_sobject(self, sobject_name: str, record_id: str) -> dict[str, Any]:
        """
        Fetch a single record via the sObject REST resource endpoint.

        GET /services/data/vXX.X/sobjects/<sobject_name>/<record_id>

        Returns the full record dict.  Raises RuntimeError on any non-2xx
        response.
        """
        url = (
            f"{self._base_url}/services/data/v{self._api_version}"
            f"/sobjects/{sobject_name}/{record_id}"
        )
        response = requests.get(url, headers=self._headers, timeout=_TIMEOUT_SECS)
        if not response.ok:
            raise RuntimeError(
                f"Salesforce GET {sobject_name}/{record_id} failed "
                f"[{response.status_code}]: {response.text}"
            )
        return response.json()
