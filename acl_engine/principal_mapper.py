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
import threading
from typing import Any, Optional
from urllib.parse import quote

from acl_engine.models import AclResult, PUBLIC_SENTINEL
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


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
    batch_size   : Maximum IDs per SOQL IN clause (from config).
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        graph_client: Any = None,
        tenant_id: str = "everyone",
        batch_size: int = 100,
    ) -> None:
        self._sf = sf_client
        self._graph_client = graph_client
        self._tenant_id = tenant_id
        self._batch_size = batch_size
        # identifier (FedId/UPN/email) → resolved AAD GUID or None
        self._principal_cache: dict[str, Optional[str]] = {}
        # user IDs already warned about missing M365 principal (suppress duplicates)
        # Protected by a threading.Lock because 3 parallel object workers share this instance.
        self._warned_missing_principals: set[str] = set()
        self._warned_lock = threading.Lock()
        # Pre-warm cache: user_id → {FederationIdentifier, UserName, Email}
        self._user_details_cache: dict[str, dict[str, Any]] = {}

    # ── Bulk pre-warm (call once per chunk with all owner/share user IDs) ──────

    async def prewarm_users(self, user_ids: set[str], batch_size: int = 200) -> None:
        """
        Bulk-fetch FederationIdentifier / UserName / Email for all *user_ids*
        that are not already in the cache.  After this, to_acl_entries() fires
        zero SOQL for identity lookups.
        """
        missing = [uid for uid in user_ids if uid not in self._user_details_cache]
        if not missing:
            return

        for i in range(0, len(missing), batch_size):
            batch = missing[i : i + batch_size]
            quoted = ", ".join(f"'{uid}'" for uid in batch)
            soql = (
                f"SELECT Id, FederationIdentifier, UserName, Email "
                f"FROM User WHERE Id IN ({quoted}) AND IsActive = true"
            )
            try:
                rows = await self._sf.query_all(soql)
                for r in rows:
                    uid = r.get("Id")
                    if uid:
                        self._user_details_cache[uid] = r
            except RuntimeError as exc:
                logger.warning("[PrincipalMapper] User details prewarm failed for batch: %s", exc)

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
        # Use pre-warm cache when available to avoid per-record SOQL
        cached = {uid: self._user_details_cache[uid] for uid in acl_result.user_ids if uid in self._user_details_cache}
        uncached = acl_result.user_ids - set(cached.keys())
        if uncached:
            fetched = await self._fetch_user_details_bulk(uncached)
            user_details = {**cached, **fetched}
        else:
            user_details = cached

        entries: list[dict[str, str]] = []
        seen: set[str] = set()

        for user_id in sorted(acl_result.user_ids):
            details = user_details.get(user_id)
            if not details:
                logger.debug("[PrincipalMapper] User %s not found in bulk fetch; skipped", user_id)
                continue

            principal = await asyncio.to_thread(self._resolve_principal, details)
            if not principal:
                with self._warned_lock:
                    already_warned = user_id in self._warned_missing_principals
                    if not already_warned:
                        self._warned_missing_principals.add(user_id)
                if not already_warned:
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
            id_list[i: i + self._batch_size] for i in range(0, len(id_list), self._batch_size)
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
        For UserName we also try stripping the Salesforce-appended org suffix
        (e.g. ``john@nokia.com.cape2104`` → ``john@nokia.com``) because SF
        UserNames are globally unique but AAD UPNs are not suffixed.
        If GraphClient is available, attempts a Graph API lookup for each
        until one succeeds.
        """
        seen: set[str] = set()
        candidates: list[str] = []

        for field in ("FederationIdentifier", "UserName", "Email"):
            identifier = (user_details.get(field) or "").strip()
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            candidates.append(identifier)
            # For UserName (and FederationIdentifier), also try stripping the
            # extra domain segment that Salesforce appends to make usernames
            # globally unique (e.g. "@company.com.sandboxSuffix" → "@company.com")
            if field in ("UserName", "FederationIdentifier"):
                stripped = _strip_sf_username_suffix(identifier)
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    candidates.append(stripped)

        for candidate in candidates:
            resolved = self._resolve_identifier(candidate)
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
                logger.info(
                    "[PrincipalMapper] Graph direct lookup ✓ %s → %s",
                    identifier,
                    payload["id"],
                )
                return str(payload["id"])
        except GraphApiError as exc:
            logger.debug("[PrincipalMapper] Graph direct lookup 404/400 for %s (status=%s)", identifier, exc.status_code)
            if exc.status_code not in (400, 403, 404):
                raise
        except Exception as exc:
            logger.debug("[PrincipalMapper] Graph direct lookup error for %s: %s", identifier, exc)
            pass

        # Attempt 2 – filter by UPN, mail, on-premises UPN, or employeeId.
        # IMPORTANT: Graph $filter with 'or' across properties is an "advanced"
        # query that requires ConsistencyLevel: eventual + $count=true, otherwise
        # the API returns 400 or silently returns an empty result set.
        # employeeId is purely alphanumeric, so only include it in the filter
        # when the identifier contains no special characters (no @, dots, etc.).
        safe_id = identifier.replace("'", "''")
        filter_clauses = (
            f"userPrincipalName eq '{safe_id}'"
            f" or mail eq '{safe_id}'"
            f" or onPremisesUserPrincipalName eq '{safe_id}'"
        )
        if safe_id.isalnum():
            filter_clauses += f" or employeeId eq '{safe_id}'"
        filter_path = f"/users?$select=id&$top=1&$count=true&$filter={filter_clauses}"
        eventual_headers = {"ConsistencyLevel": "eventual"}
        try:
            payload = self._graph_client.get(filter_path, headers=eventual_headers)
            values = payload.get("value", []) if isinstance(payload, dict) else []
            if values and isinstance(values[0], dict) and values[0].get("id"):
                guid = str(values[0]["id"])
                logger.info(
                    "[PrincipalMapper] Graph filter lookup ✓ %s → %s",
                    identifier,
                    guid,
                )
                return guid
        except GraphApiError as exc:
            logger.debug("[PrincipalMapper] Graph filter lookup error for %s (status=%s): %s", identifier, exc.status_code, exc)
            if exc.status_code not in (400, 403, 404):
                raise
        except Exception as exc:
            logger.debug("[PrincipalMapper] Graph filter lookup exception for %s: %s", identifier, exc)
            pass

        logger.warning("[PrincipalMapper] No AAD user found for '%s' (tried direct + filter on UPN/mail/onPremisesUPN/employeeId)", identifier)
        return None

    # ── ACL entry helpers ─────────────────────────────────────────────────────

    def _public_acl(self) -> list[dict[str, str]]:
        """Return a single tenant-wide grant entry for publicly visible records."""
        return [{"accessType": "grant", "type": "everyone", "value": "everyone"}]

    def _deny_all_acl(self) -> list[dict[str, str]]:
        """Return a deny-all entry when no users could be resolved."""
        return [{"accessType": "deny", "type": "everyone", "value": "everyone"}]


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


def _strip_sf_username_suffix(username: str) -> Optional[str]:
    """
    Salesforce makes every username globally unique by appending an extra label
    to the domain portion (e.g. ``john@acme.com.sandboxSuffix``).  This helper
    returns the version with that trailing label stripped so the caller can try
    it as a normal corporate UPN / email against AAD.

    Rules:
    - Must contain exactly one ``@``.
    - The domain part must have **more than two** dot-separated labels
      (``acme.com`` has two labels and should not be stripped).
    - Returns ``None`` (no stripping done) when the conditions are not met.

    Example::

        _strip_sf_username_suffix("john@nokia.com.cape2104")  # → "john@nokia.com"
        _strip_sf_username_suffix("john@nokia.com")           # → None
    """
    if "@" not in username:
        return None
    local, domain = username.rsplit("@", 1)
    labels = domain.split(".")
    # Need at least 3 labels to strip one (e.g. "nokia.com.suffix" → "nokia.com")
    if len(labels) <= 2:
        return None
    stripped_domain = ".".join(labels[:-1])
    return f"{local}@{stripped_domain}"
