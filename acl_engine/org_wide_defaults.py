# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

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

# ── EntityDefinition.InternalSharingModel → OWDVisibility value mapping ──────
# EntityDefinition returns different string literals than the Organization table
# fields.  This dict normalises them to the OWDVisibility values the rest of
# the engine already understands.
_ENTITY_DEF_TO_OWD_VALUE: dict[str, str] = {
    "Private":                    OWDVisibility.PRIVATE.value,
    "Read":                       OWDVisibility.PUBLIC_READ.value,
    "ReadSelect":                 OWDVisibility.PUBLIC_READ.value,
    "ReadWrite":                  OWDVisibility.PUBLIC_READ_WRITE.value,
    "ReadWriteTransfer":          OWDVisibility.PUBLIC_READ_WRITE_TRANSFER.value,
    "FullAccess":                 OWDVisibility.ALL.value,
    "ControlledByParent":         OWDVisibility.CONTROLLED_BY_PARENT.value,
    "ControlledByCampaign":       OWDVisibility.CONTROLLED_BY_CAMPAIGN.value,
    "ControlledByLeadOrContact":  OWDVisibility.CONTROLLED_BY_LEAD_OR_CONTACT.value,
}


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


def _load_object_names(object_names: list[str] | None = None) -> list[str]:
    """Return all object names from schema.json."""
    if object_names is not None:
        return list(object_names)
    try:
        from salesforce.settings import build_object_name_list
        return build_object_name_list()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[OWD] Cannot load object names from schema.json: %s", exc)
        return []


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
        use_entity_definition_owd: bool = False,
        object_names: list[str] | None = None,
    ) -> None:
        self._sf = sf_client
        # {objectName → owdField}  e.g. {"Account": "DefaultAccountAccess"}
        self._owd_field_map: dict[str, str] = _load_owd_field_map(owd_field_map)
        # Populated on the first get_owd call; {objectName → owd_value}
        self._org_owd_cache: Optional[dict[str, str]] = None
        # Optional overrides from config (e.g. {"Account": "Private"})
        self._owd_overrides: dict[str, str] = owd_overrides or {}

        # ── EntityDefinition flight ──────────────────────────────────────────
        self._use_entity_definition_owd = use_entity_definition_owd
        # All object names from schema.json (used for the EntityDefinition query)
        self._object_names: list[str] = _load_object_names(object_names) if use_entity_definition_owd else []
        # Populated once; {objectName → OWDVisibility value string}
        self._entity_def_cache: Optional[dict[str, str]] = None
        self._entity_def_lock: asyncio.Lock = asyncio.Lock()
        # ── End EntityDefinition flight ──────────────────────────────────────

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

        When ``use_entity_definition_owd`` is enabled:
          1. Queries ``EntityDefinition`` for all objects in schema.json (once).
          2. Maps ``InternalSharingModel`` to the canonical ``OWDVisibility`` value.
          3. Falls back to the Organisation-table approach for any object not
             found in the EntityDefinition result set.

        When disabled: behaves exactly as before (Organisation table query).

        Parameters
        ----------
        object_type : Salesforce API name, e.g. "Account", "MyCustomObj__c".

        Returns
        -------
        str – one of the OWDVisibility values (e.g. "Private", "Read", …).
        """
        owd: Optional[str] = None

        # ── NEW PATH: EntityDefinition ────────────────────────────────────────
        if self._use_entity_definition_owd:
            if self._entity_def_cache is None:
                async with self._entity_def_lock:
                    if self._entity_def_cache is None:
                        self._entity_def_cache = await self._prime_entity_definition_cache()

            if object_type in (self._entity_def_cache or {}):
                owd = self._entity_def_cache[object_type]  # type: ignore[index]
                logger.debug("[OWD] %s → %s (via EntityDefinition)", object_type, owd)
            else:
                logger.debug(
                    "[OWD] %s not found in EntityDefinition result; falling back to Organization query",
                    object_type,
                )
        # ── END NEW PATH ──────────────────────────────────────────────────────

        # ── OLD PATH (fallback): Organization table ───────────────────────────
        if owd is None:
            if object_type not in self._owd_field_map:
                logger.debug(
                    "[OWD] %s has no owdField in schema.json → defaulting to Private",
                    object_type,
                )
                owd = OWDVisibility.PRIVATE.value
            else:
                if self._org_owd_cache is None:
                    async with self._prime_lock_async:
                        if self._org_owd_cache is None:
                            candidate = await self._prime_org_owd_cache()
                            with self._prime_lock:
                                self._org_owd_cache = candidate

                owd_field = self._owd_field_map[object_type]
                owd = (self._org_owd_cache or {}).get(object_type, OWDVisibility.PRIVATE.value)
                logger.debug("[OWD] %s (Organization.%s) → %s", object_type, owd_field, owd)
        # ── END OLD PATH ──────────────────────────────────────────────────────

        # ── OWD OVERRIDE (from OWD_OVERRIDES config) ─────────────────────────
        if object_type in self._owd_overrides:
            logger.warning(
                "[OWD] ⚠️  OWD OVERRIDE: %s OWD forced from '%s' → '%s' (via OWD_OVERRIDES config)",
                object_type, owd, self._owd_overrides[object_type],
            )
            owd = self._owd_overrides[object_type]
        # ── END OWD OVERRIDE ──────────────────────────────────────────────────

        return owd


    # ── EntityDefinition query (new path) ─────────────────────────────────────

    async def _prime_entity_definition_cache(self) -> dict[str, str]:
        """
        Query ``EntityDefinition`` for all objects in schema.json and return a
        cache dict ``{objectName: OWDVisibility value}``.

        Fires:
            SELECT QualifiedApiName, InternalSharingModel
            FROM EntityDefinition
            WHERE QualifiedApiName IN ('Account', 'Contact', …)

        ``InternalSharingModel`` values are normalised to ``OWDVisibility``
        string values via ``_ENTITY_DEF_TO_OWD_VALUE``.  Unknown values
        default to ``Private`` (principle of least privilege).
        """
        if not self._object_names:
            logger.warning("[OWD] No object names available for EntityDefinition query")
            return {}

        quoted = ", ".join(f"'{name}'" for name in self._object_names)
        soql = (
            "SELECT QualifiedApiName, InternalSharingModel "
            "FROM EntityDefinition "
            f"WHERE QualifiedApiName IN ({quoted})"
        )
        logger.info("[OWD] Fetching OWD via EntityDefinition: %s", soql)

        try:
            records = await self._sf.query_all(soql, tooling=True)
        except RuntimeError as exc:
            logger.warning(
                "[OWD] EntityDefinition query failed (%s); will fall back to Organization query",
                exc,
            )
            return {}

        cache: dict[str, str] = {}
        for row in records:
            api_name: Optional[str] = row.get("QualifiedApiName")
            raw_model: Optional[str] = row.get("InternalSharingModel")
            if not api_name:
                continue
            owd_value = _ENTITY_DEF_TO_OWD_VALUE.get(
                raw_model or "", OWDVisibility.PRIVATE.value
            )
            cache[api_name] = owd_value
            logger.info(
                "[OWD] EntityDefinition: %s → InternalSharingModel='%s' → OWD='%s'",
                api_name, raw_model, owd_value,
            )

        # Log objects that were queried but not returned by EntityDefinition
        missing = set(self._object_names) - set(cache.keys())
        if missing:
            logger.warning("[OWD] EntityDefinition returned no rows for: %s", missing)

        logger.info(
            "[OWD] EntityDefinition cache primed for %d/%d object(s)",
            len(cache), len(self._object_names),
        )
        return cache

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
