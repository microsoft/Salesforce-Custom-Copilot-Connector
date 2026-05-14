"""
acl_engine/owd.py
-----------------
Step 2: Org-Wide Default (OWD) fetching and interpretation.

How OWD is fetched
------------------
A single ``SELECT <owd_fields> FROM Organization`` is executed once per
OWDFetcher lifetime.  The SELECT field list is built dynamically from
schema.json's ``owdField`` entries – no field names are ever hard-coded here.
Adding a new object to schema.json with an ``owdField`` is all that is needed.

For objects that have no ``owdField`` in schema.json (e.g. Contact, which
inherits from its parent Account), ``get_owd`` returns "Private" as the safe
default, and the resolver's ControlledByParent path handles inheritance.

Predicates
----------
    is_public(owd)               → skip ACL work, grant everyone
    is_controlled_by_parent(owd) → recurse into parent record
    requires_private_acl(owd)    → proceed to Step 3 (record-level ACL)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Optional

from acl_engine.models import OWDVisibility
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")

# OWD values that mean "any authenticated user in the org can read the record"
_PUBLIC_VALUES: frozenset[str] = frozenset({
    OWDVisibility.PUBLIC_READ.value,                 # "Read"
    OWDVisibility.PUBLIC_READ_WRITE.value,           # "Edit"
    OWDVisibility.PUBLIC_READ_WRITE_TRANSFER.value,  # "ReadEditTransfer"
    OWDVisibility.ALL.value,                         # "All"
})

# OWD values that mean "inherit sharing from the controlling parent record"
_CONTROLLED_BY_PARENT_VALUES: frozenset[str] = frozenset({
    OWDVisibility.CONTROLLED_BY_PARENT.value,
    OWDVisibility.CONTROLLED_BY_CAMPAIGN.value,
    OWDVisibility.CONTROLLED_BY_LEAD_OR_CONTACT.value,
})


def _load_owd_field_map(owd_field_map: dict[str, str] | None = None) -> dict[str, str]:
    """
    Return ``{objectName: owdField}`` for every object that declares an ``owdField``.

    Objects without ``owdField`` (e.g. Contact, which is ControlledByParent)
    are intentionally excluded – the resolver's parent-chain path handles them.
    """
    if owd_field_map is not None:
        return dict(owd_field_map)
    try:
        from salesforce.settings import build_owd_field_map
        mapping = build_owd_field_map()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[OWD] Cannot load schema.json: %s", exc)
        return {}

    logger.debug("[OWD] Loaded owdField map from schema.json: %s", mapping)
    return mapping


class OWDFetcher:
    """
    Fetches Org-Wide Default sharing settings for a given Salesforce object type.

    A single ``SELECT <all_owd_fields> FROM Organization`` is fired on the
    first call to ``get_owd`` and every subsequent call is served from memory.
    The SELECT list is derived entirely from schema.json's ``owdField`` values.

    For objects not present in schema.json, ``get_owd`` returns "Private".

    Parameters
    ----------
    sf_client      : SalesforceClient instance.
    sf_client      : SalesforceClient instance.
    owd_field_map  : Pre-built {objectName: owdField} dict.  If omitted,
                     loaded from config/schema.json.
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        owd_field_map: dict[str, str] | None = None,
        owd_overrides: dict[str, str] | None = None,
    ) -> None:
        self._sf = sf_client
        # {objectName → owdField}  e.g. {"Account": "DefaultAccountAccess"}
        self._owd_field_map: dict[str, str] = _load_owd_field_map(owd_field_map)
        # Populated on the first get_owd call; {objectName → owd_value}
        self._org_owd_cache: Optional[dict[str, str]] = None
        # Optional overrides from config (e.g. {"Account": "Private"})
        self._owd_overrides: dict[str, str] = owd_overrides or {}
        # asyncio.Lock prevents the thundering-herd problem where hundreds of
        # concurrent coroutines all see _org_owd_cache=None and each fire the
        # same failing OWD query.  Only ONE coroutine primes the cache; the
        # rest wait on the lock and find the cache already populated.
        self._prime_lock_async: asyncio.Lock = asyncio.Lock()
        # threading.Lock guards cross-thread reads of the cache dict.
        self._prime_lock: threading.Lock = threading.Lock()

    # ── Main fetch ────────────────────────────────────────────────────────────

    async def get_owd(self, object_type: str) -> str:
        """
        Return the OWD string for *object_type*.

        For objects with an ``owdField`` in schema.json: fires (once) a single
        ``SELECT <fields> FROM Organization`` and returns the cached value.

        For objects not in schema.json: returns "Private" (safe default).

        Parameters
        ----------
        object_type : Salesforce API name, e.g. "Account", "MyCustomObj__c".

        Returns
        -------
        str – one of the OWDVisibility values (e.g. "Private", "Read", …).
        """
        if object_type not in self._owd_field_map:
            logger.debug(
                "[OWD] %s has no owdField in schema.json → defaulting to Private",
                object_type,
            )
            return OWDVisibility.PRIVATE.value

        if self._org_owd_cache is None:
            async with self._prime_lock_async:
                # Double-check after acquiring the async lock — another coroutine
                # may have primed the cache while we were waiting.
                if self._org_owd_cache is None:
                    candidate = await self._prime_org_owd_cache()
                    with self._prime_lock:
                        self._org_owd_cache = candidate

        owd_field = self._owd_field_map[object_type]
        owd = (self._org_owd_cache or {}).get(object_type, OWDVisibility.PRIVATE.value)
        logger.debug("[OWD] %s (Organization.%s) → %s", object_type, owd_field, owd)

        # ── OWD OVERRIDE (from OWD_OVERRIDES config) ─────────────────────────
        if object_type in self._owd_overrides:
            logger.warning(
                "[OWD] ⚠️  OWD OVERRIDE: %s OWD forced from '%s' → '%s' (via OWD_OVERRIDES config)",
                object_type, owd, self._owd_overrides[object_type],
            )
            owd = self._owd_overrides[object_type]
        # ── END OWD OVERRIDE ──────────────────────────────────────────────────

        return owd


    # ── Organization query (primes the in-memory cache) ───────────────────────

    async def _prime_org_owd_cache(self) -> dict[str, str]:
        """
        Execute ONE ``SELECT <owd_fields> FROM Organization`` and return a
        cache dict keyed by objectName.

        Returns the dict \u2014 caller is responsible for assigning to _org_owd_cache
        under the threading.Lock.  This allows the await to happen outside the
        lock, preventing the deadlock that occurs when an awaiting coroutine
        holds a threading.Lock and suspends, blocking all other coroutines.
        """
        owd_fields = list(dict.fromkeys(self._owd_field_map.values()))
        soql = f"SELECT {', '.join(owd_fields)} FROM Organization"

        logger.info("[OWD] Fetching OWD with: %s", soql)

        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning(
                "[OWD] Bulk Organization query failed (%s); retrying per-field (defaulting to Private on failure)",
                exc,
            )
            return await self._fetch_owd_per_field()

        if not records:
            logger.warning("[OWD] Organization returned no rows; all objects default to Private")
            return {}

        org_row = records[0]  # There is always exactly one Organization record

        cache: dict[str, str] = {}
        for obj_name, owd_field in self._owd_field_map.items():
            raw: Optional[str] = org_row.get(owd_field)
            cache[obj_name] = raw if raw else OWDVisibility.PRIVATE.value

        logger.info("[OWD] Cache primed for %d object(s): %s", len(cache), cache)
        return cache

    async def _fetch_owd_per_field(self) -> dict[str, str]:
        """
        Fall back: query each owdField individually against the Organization table.

        Multiple objects can share the same owdField (e.g. both Account and
        Opportunity might use ``DefaultAccountAccess``), so we deduplicate the
        field names and fan the result back out to all affected objects.

        Any field whose query fails defaults to ``Private`` — never to a
        permissive value — to preserve the principle of least privilege.
        """
        # Invert map: owdField → [objectName, ...]
        field_to_objects: dict[str, list[str]] = {}
        for obj_name, owd_field in self._owd_field_map.items():
            field_to_objects.setdefault(owd_field, []).append(obj_name)

        cache: dict[str, str] = {}
        for owd_field, obj_names in field_to_objects.items():
            soql = f"SELECT {owd_field} FROM Organization"
            try:
                records = await self._sf.query_all(soql)
                if records:
                    raw: Optional[str] = records[0].get(owd_field)
                    value = raw if raw else OWDVisibility.PRIVATE.value
                else:
                    logger.warning("[OWD] Per-field query for %s returned no rows; defaulting to Private", owd_field)
                    value = OWDVisibility.PRIVATE.value
            except RuntimeError as exc:
                logger.warning(
                    "[OWD] Per-field query for %s failed (%s); defaulting to Private",
                    owd_field, exc,
                )
                value = OWDVisibility.PRIVATE.value

            for obj_name in obj_names:
                cache[obj_name] = value
                logger.info("[OWD] %s (Organization.%s) → %s (per-field fallback)", obj_name, owd_field, value)

        return cache

    # ── Predicates ────────────────────────────────────────────────────────────

    @staticmethod
    def is_public(owd: str) -> bool:
        """
        True when OWD grants read access to every user in the org.
        The resolver should emit a tenant-wide grant and skip further processing.
        """
        return owd in _PUBLIC_VALUES

    @staticmethod
    def is_controlled_by_parent(owd: str) -> bool:
        """
        True when the record's visibility is governed by its parent record.
        The resolver should fetch the parent record and repeat the OWD check.
        """
        return owd in _CONTROLLED_BY_PARENT_VALUES

    @staticmethod
    def requires_private_acl(owd: str) -> bool:
        """
        True when OWD is Private (or unrecognised).
        The resolver must proceed to Step 3: record-level share table analysis.
        """
        return owd not in _PUBLIC_VALUES and owd not in _CONTROLLED_BY_PARENT_VALUES
