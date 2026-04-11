"""
acl_engine/principal_mapper.py
-------------------------------
Converts raw Salesforce User IDs (output of AclResolver) into
Graph-API-ready ACL entries.

Pipeline
--------
  AclResult.user_ids  (set of Salesforce User IDs)
        │
        ▼  Step 1 – bulk SOQL fetch (single query)
  {user_id: {FederationIdentifier, UserName, Email}}
        │
        ▼  Step 2 – pick best identifier per user
  FederationIdentifier  →  UserName  →  Email
        │
        ▼  Step 3 – resolve to AAD GUID (if GraphClient present)
  GET /users/<identifier>?$select=id
  or filter by userPrincipalName / mail
        │
        ▼  Step 4 – format as Graph ACL entry
  {"accessType": "grant", "type": "user", "value": "<aad_guid_or_upn>"}

PUBLIC_SENTINEL handling
------------------------
When AclResult.is_public is True (or PUBLIC_SENTINEL is in user_ids),
returns a single tenant-wide grant entry:
  {"accessType": "grant", "type": "everyone", "value": "<tenant_id>"}

Batch size
----------
The SOQL IN clause is capped at _BATCH_SIZE (100) IDs per query to avoid
hitting Salesforce's per-query limit.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional
from urllib.parse import quote

from acl_engine.models import AclResult, PUBLIC_SENTINEL
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")

# Maximum IDs per SOQL IN clause
_BATCH_SIZE = int(os.getenv("SALESFORCE_BATCH_SIZE", "100"))


class PrincipalMapper:
    """
    Maps Salesforce User IDs → Graph-API-ready ACL list entries.

    Parameters
    ----------
    sf_client    : SalesforceClient – used for bulk user identity SOQL.
    graph_client : Optional GraphClient (connector.graph.GraphClient).
                   When provided, identifiers are resolved to AAD Object GUIDs.
                   When None, FederationIdentifier / UserName / Email are used
                   directly as ACL values (works when UPN == SF identifier).
    tenant_id    : Azure tenant ID used in "everyone" ACL entries.
                   Defaults to the AZURE_TENANT_ID env var, or "everyone".
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        graph_client: Any = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self._sf = sf_client
        self._graph_client = graph_client
        self._tenant_id = tenant_id or os.getenv("AZURE_TENANT_ID") or "everyone"
        # identifier (FedId/UPN/email) → resolved AAD GUID or None
        self._principal_cache: dict[str, Optional[str]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def to_acl_entries(self, acl_result: AclResult) -> list[dict[str, str]]:
        """
        Convert one AclResult into a Graph-API-ready ACL list.

        Returns
        -------
        list[dict]  e.g.
            [{"accessType": "grant", "type": "user", "value": "<aad_guid>"}]
        or the public sentinel:
            [{"accessType": "grant", "type": "everyone", "value": "<tenant_id>"}]
        or a deny-all if no users could be resolved:
            [{"accessType": "deny",  "type": "everyone", "value": "<tenant_id>"}]
        """
        if acl_result.is_public or PUBLIC_SENTINEL in acl_result.user_ids:
            return self._public_acl()

        if not acl_result.user_ids:
            return self._deny_all_acl()

        # Bulk-fetch identity fields for all user IDs in a single SOQL call
        user_details = await self._fetch_user_details_bulk(acl_result.user_ids)

        entries: list[dict[str, str]] = []
        seen: set[str] = set()

        for user_id in sorted(acl_result.user_ids):
            details = user_details.get(user_id)
            if not details:
                logger.debug("[PrincipalMapper] User %s not found in bulk fetch; skipped", user_id)
                continue

            principal = await asyncio.to_thread(self._resolve_principal, details)
            if not principal:
                logger.warning(
                    "[PrincipalMapper] User %s (%s): no M365 principal found",
                    user_id,
                    details.get("Email") or details.get("UserName") or "no-identifier",
                )
                continue

            normalized = principal.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            entries.append({"accessType": "grant", "type": "user", "value": principal})

        logger.info(
            "[PrincipalMapper] %s/%s → %d ACL entries from %d user ID(s)",
            acl_result.object_type,
            acl_result.record_id,
            len(entries),
            len(acl_result.user_ids),
        )
        return entries or self._deny_all_acl()

    # ── Bulk user identity fetch ──────────────────────────────────────────────

    async def _fetch_user_details_bulk(
        self, user_ids: set[str]
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch FederationIdentifier, UserName, and Email for all *user_ids*
        in batches of _BATCH_SIZE using a single SOQL per batch.

        Returns {user_id: {field: value, ...}}.

        curl equivalent (one batch):
            GET /services/data/v60.0/query
                ?q=SELECT+Id%2CFederationIdentifier%2CUserName%2CEmail
                   +FROM+User+WHERE+Id+IN+('005...','005...')
                   +AND+IsActive=true
        """
        id_list = sorted(user_ids)
        batches = [
            id_list[i: i + _BATCH_SIZE] for i in range(0, len(id_list), _BATCH_SIZE)
        ]

        all_details: dict[str, dict[str, Any]] = {}

        for batch in batches:
            quoted = ", ".join(f"'{uid}'" for uid in batch)
            soql = (
                f"SELECT Id, FederationIdentifier, UserName, Email "
                f"FROM User "
                f"WHERE Id IN ({quoted}) AND IsActive = true"
            )
            try:
                records = await self._sf.query_all(soql)
            except RuntimeError as exc:
                logger.warning("[PrincipalMapper] Bulk user fetch failed: %s", exc)
                continue

            for r in records:
                uid = r.get("Id")
                if uid:
                    all_details[uid] = r

        logger.debug(
            "[PrincipalMapper] Bulk fetch: %d requested, %d returned",
            len(user_ids),
            len(all_details),
        )
        return all_details

    # ── Principal resolution (sync, run in thread) ────────────────────────────

    def _resolve_principal(self, user_details: dict[str, Any]) -> Optional[str]:
        """
        Pick the best identifier and resolve to an AAD GUID if possible.

        Priority: FederationIdentifier → UserName → Email
        If GraphClient is available, attempts a Graph API lookup for each
        until one succeeds.
        """
        for field in ("FederationIdentifier", "UserName", "Email"):
            identifier = (user_details.get(field) or "").strip()
            if not identifier:
                continue
            resolved = self._resolve_identifier(identifier)
            if resolved:
                return resolved
        return None

    def _resolve_identifier(self, identifier: str) -> Optional[str]:
        """
        Return the AAD GUID (or the identifier itself if it already looks like
        a GUID or if no Graph client is available).
        """
        # Cache hit
        if identifier in self._principal_cache:
            return self._principal_cache[identifier]

        # If it already looks like a GUID, use it directly
        if _looks_like_guid(identifier):
            self._principal_cache[identifier] = identifier
            return identifier

        # No Graph client – use the raw identifier (UPN / email)
        if self._graph_client is None:
            self._principal_cache[identifier] = identifier
            return identifier

        # Attempt Graph lookup
        aad_id = self._lookup_graph_user_id(identifier)
        self._principal_cache[identifier] = aad_id
        return aad_id

    def _lookup_graph_user_id(self, identifier: str) -> Optional[str]:
        """
        Resolve *identifier* (UPN / email / FederationIdentifier) to an AAD
        Object GUID via two Graph API attempts:

        1. Direct path:  GET /users/<encoded_identifier>?$select=id
        2. Filter query: GET /users?$filter=userPrincipalName eq '...' or mail eq '...'

        Returns the GUID string, or None if not found.
        """
        from graph.client import GraphApiError  # imported here to avoid circular dep

        # Attempt 1 – direct lookup by UPN / object ID
        direct_path = f"/users/{quote(identifier, safe='')}?$select=id"
        try:
            payload = self._graph_client.get(direct_path)
            if isinstance(payload, dict) and payload.get("id"):
                logger.debug(
                    "[PrincipalMapper] Graph direct lookup → %s → %s",
                    identifier,
                    payload["id"],
                )
                return str(payload["id"])
        except GraphApiError as exc:
            if exc.status_code not in (400, 403, 404):
                raise
        except Exception:
            pass

        # Attempt 2 – filter by UPN or mail
        safe_id = identifier.replace("'", "''")
        filter_path = (
            f"/users?$select=id&$top=1&$filter="
            f"userPrincipalName eq '{safe_id}' or mail eq '{safe_id}'"
        )
        try:
            payload = self._graph_client.get(filter_path)
            values = payload.get("value", []) if isinstance(payload, dict) else []
            if values and isinstance(values[0], dict) and values[0].get("id"):
                guid = str(values[0]["id"])
                logger.debug(
                    "[PrincipalMapper] Graph filter lookup → %s → %s",
                    identifier,
                    guid,
                )
                return guid
        except GraphApiError as exc:
            if exc.status_code not in (400, 403, 404):
                raise
        except Exception:
            pass

        logger.debug("[PrincipalMapper] No AAD user found for identifier: %s", identifier)
        return None

    # ── ACL entry helpers ─────────────────────────────────────────────────────

    def _public_acl(self) -> list[dict[str, str]]:
        return [{"accessType": "grant", "type": "everyone", "value": self._tenant_id}]

    def _deny_all_acl(self) -> list[dict[str, str]]:
        return [{"accessType": "deny", "type": "everyone", "value": self._tenant_id}]


# ── Standalone helpers ────────────────────────────────────────────────────────

def _looks_like_guid(value: str) -> bool:
    """Return True if *value* is already an AAD Object GUID."""
    parts = value.strip().split("-")
    if len(parts) != 5:
        return False
    expected = (8, 4, 4, 4, 12)
    return all(
        len(p) == exp and all(c in "0123456789abcdefABCDEF" for c in p)
        for p, exp in zip(parts, expected)
    )
