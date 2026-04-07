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
from acl_engine.sf_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


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

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_owner_id(self, object_type: str, record_id: str) -> Optional[str]:
        """
        Return the OwnerId for the given record, or None if not found.

        The owner is *always* granted access regardless of share table entries,
        so the resolver adds this ID before it even looks at the share table.

        curl equivalent:
            GET /services/data/v60.0/query
                ?q=SELECT+OwnerId+FROM+<ObjectType>+WHERE+Id='<record_id>'+LIMIT+1
        """
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
        Query <ObjectType>Share and return all explicit share grants.

        Each returned ShareEntry contains:
          user_or_group_id – a Salesforce User or Group Id
          row_cause        – why the share was created (e.g. "Manual", "Territory")
          access_level     – the level of access granted (e.g. "Read", "Edit")

        The parent reference field and access level field are discovered
        dynamically via sObject describe so this method works for any
        standard or custom object without any hard-coded field names.

        curl equivalent:
            GET /services/data/v60.0/query
                ?q=SELECT+AccountId%2CUserOrGroupId%2CAccountAccessLevel%2CRowCause
                   +FROM+AccountShare+WHERE+AccountId='<record_id>'
        """
        share_object = object_type + "Share"

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
