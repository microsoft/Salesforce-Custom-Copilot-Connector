"""
acl_engine/share_fetcher.py
---------------------------
Step 3.1: Fetch record-level share data from Salesforce.

Responsibilities
----------------
* get_owner_id      – Read the OwnerId field from a record.
* get_share_entries – Query the <ObjectType>Share table and return every
                      UserOrGroupId that has been explicitly granted access,
                      along with the RowCause and AccessLevel for that grant.

Dynamic field discovery (no hard-coding)
-----------------------------------------
Two share-table fields require discovery because their names vary by object:

  Parent reference field
    - Standard objects : "<ObjectType>Id"  (e.g. "AccountId", "CaseId")
    - Custom objects   : "ParentId"
    Discovered via sObject describe → referenceTo matching object_type.

  Access level field
    - Standard objects : "<ObjectType>AccessLevel"  (e.g. "AccountAccessLevel")
    - Custom objects   : "AccessLevel"
    Discovered via sObject describe → picklist field ending in "AccessLevel".

Both are cached in-process so each object type only pays the describe cost once.

curl equivalent for share table query:
    GET /services/data/v60.0/query
        ?q=SELECT+AccountId%2CUserOrGroupId%2CAccountAccessLevel%2CRowCause
           +FROM+AccountShare+WHERE+AccountId%3D'<record_id>'
"""
from __future__ import annotations

import logging
from typing import Optional

from acl_engine.models import ShareEntry
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


def _share_table_name(object_type: str) -> str:
    """
    Derive the share table API name for a given sObject type.

    Standard objects : Account   → AccountShare
    Custom objects   : Work_Order__c → Work_Order__Share
                       (strip '__c', append '__Share')
    """
    if object_type.endswith("__c"):
        return object_type[:-3] + "__Share"
    return object_type + "Share"


class ShareFetcher:
    """
    Fetches the owner and explicit share grants for a single Salesforce record.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client
        # Cache: object_type → parent field name on the share table
        self._parent_field_cache: dict[str, Optional[str]] = {}
        # Cache: object_type → access level field name on the share table
        self._access_level_field_cache: dict[str, Optional[str]] = {}
        # Bulk pre-warm caches (populated by prewarm_chunk)
        # record_id → OwnerId
        self._owner_cache: dict[str, Optional[str]] = {}
        # record_id → list of ShareEntry
        self._share_cache: dict[str, list[ShareEntry]] = {}

    # ── Bulk pre-warm (call once per chunk before resolving records) ──────────

    async def prewarm_chunk(
        self,
        object_type: str,
        record_ids: list[str],
        batch_size: int = 100,
    ) -> None:
        """
        Bulk-fetch all owner IDs and all share entries for *record_ids* in
        O(ceil(N/batch_size)) SOQL calls instead of O(N).

        After this call, get_owner_id() and get_share_entries() serve every
        record in *record_ids* from memory without hitting Salesforce.

        Call once per chunk before launching asyncio.gather() over the records.
        """
        if not record_ids:
            return

        share_object = _share_table_name(object_type)
        parent_field, access_level_field = await self._get_share_fields(object_type, share_object)

        # Pre-initialise share cache so records with zero shares don't fall
        # back to a per-record SOQL (empty list = "queried, no results").
        for rid in record_ids:
            self._owner_cache.setdefault(rid, None)
            self._share_cache.setdefault(rid, [])

        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i : i + batch_size]
            ids_in = ", ".join(f"'{rid}'" for rid in batch)

            # ── Bulk owner fetch ──────────────────────────────────────────────
            try:
                owner_rows = await self._sf.query_all(
                    f"SELECT Id, OwnerId FROM {object_type} WHERE Id IN ({ids_in})"
                )
                for r in owner_rows:
                    if r.get("Id"):
                        self._owner_cache[r["Id"]] = r.get("OwnerId")
            except RuntimeError as exc:
                logger.warning(
                    "[ShareFetcher] Bulk owner fetch failed for %s batch %d: %s",
                    object_type, i // batch_size + 1, exc,
                )

            # ── Bulk share-table fetch ────────────────────────────────────────
            if not parent_field:
                continue

            select_fields = ["UserOrGroupId", "RowCause", parent_field]
            if access_level_field:
                select_fields.append(access_level_field)

            try:
                share_rows = await self._sf.query_all(
                    f"SELECT {', '.join(select_fields)} "
                    f"FROM {share_object} "
                    f"WHERE {parent_field} IN ({ids_in})"
                )
                for r in share_rows:
                    parent_id = r.get(parent_field)
                    if not parent_id or not r.get("UserOrGroupId"):
                        continue
                    entry = ShareEntry(
                        user_or_group_id=r["UserOrGroupId"],
                        row_cause=r.get("RowCause"),
                        access_level=r.get(access_level_field) if access_level_field else None,
                    )
                    self._share_cache.setdefault(parent_id, []).append(entry)
            except RuntimeError as exc:
                logger.warning(
                    "[ShareFetcher] Bulk share fetch failed for %s batch %d: %s",
                    share_object, i // batch_size + 1, exc,
                )

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_owner_id(self, object_type: str, record_id: str) -> Optional[str]:
        """
        Return the OwnerId for the given record, or None if not found.

        Serves from the bulk pre-warm cache when prewarm_chunk() has been
        called for this record; falls back to a per-record SOQL otherwise.
        """
        # Fast path — bulk cache hit
        if record_id in self._owner_cache:
            return self._owner_cache[record_id]

        # Slow path — per-record fallback (incremental ingest / single-record mode)
        soql = f"SELECT OwnerId FROM {object_type} WHERE Id = '{record_id}' LIMIT 1"
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning("[ShareFetcher] Could not fetch owner for %s/%s: %s", object_type, record_id, exc)
            return None

        if not records:
            logger.warning("[ShareFetcher] Record not found: %s / %s", object_type, record_id)
            return None

        owner_id: Optional[str] = records[0].get("OwnerId")
        logger.info("[ShareFetcher] Owner of %s/%s → %s", object_type, record_id, owner_id)
        return owner_id

    async def get_share_entries(self, object_type: str, record_id: str) -> list[ShareEntry]:
        """
        Return all explicit share grants for *record_id*.

        Serves from the bulk pre-warm cache when prewarm_chunk() has been
        called for this record; falls back to a per-record SOQL otherwise.
        """
        # Fast path — bulk cache hit (empty list is a valid cached result)
        if record_id in self._share_cache:
            return self._share_cache[record_id]

        # Slow path — per-record fallback
        share_object = _share_table_name(object_type)

        # Discover both fields concurrently in a single describe call
        parent_field, access_level_field = await self._get_share_fields(object_type, share_object)

        if not parent_field:
            logger.warning(
                "[ShareFetcher] Cannot determine parent field for %s; "
                "share table will be skipped for %s/%s",
                share_object,
                object_type,
                record_id,
            )
            return []

        # Build SELECT list generically – include access level only if discovered
        select_fields = ["UserOrGroupId", "RowCause"]
        if access_level_field:
            select_fields.append(access_level_field)

        soql = (
            f"SELECT {', '.join(select_fields)} "
            f"FROM {share_object} "
            f"WHERE {parent_field} = '{record_id}'"
        )

        logger.debug("[ShareFetcher] Share query: %s", soql)

        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning(
                "[ShareFetcher] Share table query failed for %s/%s: %s",
                object_type,
                record_id,
                exc,
            )
            return []

        entries = [
            ShareEntry(
                user_or_group_id=r["UserOrGroupId"],
                row_cause=r.get("RowCause"),
                access_level=r.get(access_level_field) if access_level_field else None,
            )
            for r in records
            if r.get("UserOrGroupId")
        ]

        logger.info(
            "[ShareFetcher] %s/%s → %d share entry/entries in %s  "
            "(parent_field=%s  access_level_field=%s)",
            object_type,
            record_id,
            len(entries),
            share_object,
            parent_field,
            access_level_field or "n/a",
        )
        return entries

    # ── Field discovery (describe-based, cached) ───────────────────────────────

    async def _get_share_fields(
        self, object_type: str, share_object: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Return (parent_field, access_level_field) for *share_object*.

        Both values are resolved once via sObject describe and then cached.
        A single describe call populates both caches simultaneously.
        """
        # Fast path – both already cached
        if object_type in self._parent_field_cache and object_type in self._access_level_field_cache:
            return (
                self._parent_field_cache[object_type],
                self._access_level_field_cache[object_type],
            )

        # Fetch describe once and extract both fields
        try:
            describe = await self._sf.describe_sobject(share_object)
        except RuntimeError as exc:
            logger.warning("[ShareFetcher] describe(%s) failed: %s", share_object, exc)
            self._parent_field_cache[object_type] = await self._discover_parent_field_via_probe(
                object_type, share_object
            )
            self._access_level_field_cache[object_type] = None
            return (
                self._parent_field_cache[object_type],
                self._access_level_field_cache[object_type],
            )

        fields: list[dict] = describe.get("fields", [])

        # ── Parent reference field ─────────────────────────────────────────────
        parent_field: Optional[str] = None
        # 1st pass – reference field whose referenceTo contains object_type
        for f in fields:
            if f.get("type") == "reference":
                ref_to: list[str] = f.get("referenceTo") or []
                if object_type in ref_to:
                    parent_field = f["name"]
                    break
        # 2nd pass – generic "ParentId"
        if not parent_field:
            for f in fields:
                if f.get("name") == "ParentId":
                    parent_field = "ParentId"
                    break
        # 3rd pass – probe
        if not parent_field:
            parent_field = await self._discover_parent_field_via_probe(object_type, share_object)

        # ── Access level field ─────────────────────────────────────────────────
        # Convention: "<ObjectType>AccessLevel" for standard, "AccessLevel" for custom.
        # We find it via describe by looking for a picklist (or string) field whose
        # name ends with "AccessLevel" – no hard-coded names.
        access_level_field: Optional[str] = None
        for f in fields:
            fname: str = f.get("name", "")
            if fname.endswith("AccessLevel"):
                access_level_field = fname
                break

        # Populate both caches
        self._parent_field_cache[object_type] = parent_field
        self._access_level_field_cache[object_type] = access_level_field

        logger.debug(
            "[ShareFetcher] %s → parent_field=%s  access_level_field=%s",
            share_object,
            parent_field or "(none)",
            access_level_field or "(none)",
        )
        return parent_field, access_level_field

    async def _discover_parent_field_via_probe(
        self, object_type: str, share_object: str
    ) -> Optional[str]:
        """
        Last-resort probe: try "<ObjectType>Id" then "ParentId" with a LIMIT 1
        query to see which field exists on the share table.
        """
        for candidate in [object_type + "Id", "ParentId"]:
            probe_soql = f"SELECT {candidate} FROM {share_object} LIMIT 1"
            try:
                await self._sf.query_all(probe_soql)
                logger.debug("[ShareFetcher] Probe confirmed parent field %s on %s", candidate, share_object)
                return candidate
            except RuntimeError:
                continue
        return None
