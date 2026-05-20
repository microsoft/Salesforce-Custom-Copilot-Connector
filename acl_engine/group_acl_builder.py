"""
acl_engine/group_acl_builder.py
-------------------------------
Group-based ACL builder for content items.

Replaces the user-only ACL resolution with group-reference ACLs that point
to external groups created by the Identity Crawl.  This approach is more
efficient (fewer ACEs per item) and automatically stays in sync when group
membership changes.

Enabled by setting ``USE_GROUP_ACL=true`` in the environment.  When disabled,
the legacy user-only engines remain active.

ACL Construction Rules:
    - PUBLIC OWD   → single group ACE: "{Object}-TopLevel"
    - PRIVATE OWD  → "{Object}-GlobalUsers" + per-share user/group ACEs
    - ControlledByParent → inherits parent record's private ACLs
      (parent shares fetched and cached lazily across chunks)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from acl_engine.group_id_formats import SfGroupIdFormats
from acl_engine.identity_models import (
    EntityVisibility,
    SfGroup,
    SfUser,
    UserOrGroupType,
    is_public_visibility,
    is_controlled_by_parent,
    parse_visibility,
)
from acl_engine.identity_queries import IdentityQueryClient
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine.group_acl")


def _share_table_name(object_type: str) -> str:
    """Derive the share table API name for a given sObject type."""
    if object_type.endswith("__c"):
        return object_type[:-3] + "__Share"
    return object_type + "Share"


class GroupAclBuilder:
    """
    Builds group-based ACLs for content items.

    Each item's ACL references external groups (created by the identity crawl)
    instead of listing individual users.  The Microsoft Graph framework resolves
    group membership at search time.

    Parameters
    ----------
    sf_client    : Authenticated ``SalesforceClient``.
    owd_overrides : Optional dict ``{object_name: owd_value}`` to override
                    Salesforce OWD settings.
    parent_map   : ``{object_name: (parent_field, parent_object)}`` from config.
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        owd_overrides: dict[str, str] | None = None,
        parent_map: dict[str, tuple[str, str]] | None = None,
        owd_field_map: dict[str, str] | None = None,
        principal_mapper: Any | None = None,
        use_entity_definition_owd: bool = False,
        object_names: list[str] | None = None,
    ) -> None:
        self._sf = sf_client
        self._query_client = IdentityQueryClient(
            sf_client,
            owd_field_map=owd_field_map,
            use_entity_definition_owd=use_entity_definition_owd,
            object_names=object_names,
        )
        self._owd_overrides = owd_overrides or {}
        self._parent_map = parent_map or {}
        self._principal_mapper = principal_mapper
        # Caches populated on first use
        self._owd_map: dict[str, EntityVisibility] | None = None
        self._users_by_id: dict[str, SfUser] | None = None
        self._groups_by_id: dict[str, SfGroup] | None = None
        self._frozen_users: set[str] | None = None
        self._no_share_table_types: set[str] = set()
        # Lazy cache: {parent_record_id: [acl_entry, ...]}
        # Accumulates across chunks so repeated parent lookups are free.
        self._parent_acl_cache: dict[str, list[dict[str, str]]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def prewarm_owd(self) -> dict[str, str]:
        """Pre-fetch the OWD map and return human-readable labels.

        Returns ``{object_type: "Private" | "Public Read" | ...}``.
        """
        _LABELS = {
            "None": "Private",
            "Read": "Public Read",
            "Edit": "Public Read/Write",
            "ReadEditTransfer": "Public Read/Write/Transfer",
            "ControlledByParent": "ControlledByParent",
            "ControlledByLeadOrContact": "ControlledByParent",
            "ControlledByCampaign": "ControlledByParent",
        }
        if self._owd_map is None:
            self._owd_map = await self._query_client.get_org_wide_defaults()
            for obj_name, raw_owd in self._owd_overrides.items():
                self._owd_map[obj_name] = parse_visibility(raw_owd)
        return {
            obj: _LABELS.get(vis.value, vis.value)
            for obj, vis in self._owd_map.items()
        }

    def resolve(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        """
        Resolve group-based ACLs for all records.

        Returns ``{object_type: {record_id: [acl_entry]}}``.

        Same shape as the legacy and new user-only engines so it is a
        drop-in replacement in ``graph/ingest.py``.
        """
        return asyncio.run(self._resolve_async(records_by_object_type))

    async def _resolve_async(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Async implementation of the resolve pipeline."""
        import time as _time
        logger.info("[GroupACL] Starting group-based ACL resolution")

        # Load OWD map once
        if self._owd_map is None:
            _t0 = _time.monotonic()
            self._owd_map = await self._query_client.get_org_wide_defaults()
            logger.info("[GroupACL][TIMING] OWD query: %.1fs", _time.monotonic() - _t0)
            # Apply overrides
            for obj_name, raw_owd in self._owd_overrides.items():
                self._owd_map[obj_name] = parse_visibility(raw_owd)

        acl_maps: dict[str, dict[str, list[dict[str, str]]]] = {}

        for object_type, records in records_by_object_type.items():
            if not records:
                continue
            logger.info("[GroupACL] Resolving %d %s record(s)", len(records), object_type)
            acl_maps[object_type] = await self._build_acl_map(object_type, records, acl_maps)

        logger.info("[GroupACL] Resolution complete: %d object type(s)", len(acl_maps))
        return acl_maps

    # ── Per-object-type resolution ────────────────────────────────────────────

    async def _build_acl_map(
        self,
        object_type: str,
        records: list[dict[str, Any]],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
        """Build ACL map for one object type based on its OWD."""
        visibility = self._get_visibility(object_type)

        if is_public_visibility(visibility):
            logger.info("[GroupACL] %s: PUBLIC → TopLevel group", object_type)
            return self._build_public_acls(object_type, records)

        if is_controlled_by_parent(visibility):
            parent_info = self._parent_map.get(object_type)
            if parent_info:
                parent_obj = parent_info[1]
                parent_vis = self._get_visibility(parent_obj)
                if is_public_visibility(parent_vis):
                    logger.info("[GroupACL] %s: ControlledByParent, parent %s is PUBLIC", object_type, parent_obj)
                    return self._build_public_acls(object_type, records)

            logger.info("[GroupACL] %s: ControlledByParent → inheriting parent share ACLs", object_type)
            return await self._build_controlled_by_parent_acls(object_type, records)

        # PRIVATE
        logger.info("[GroupACL] %s: PRIVATE → per-record share-based ACLs", object_type)
        return await self._build_private_acls(object_type, records)

    # ── PUBLIC OWD ────────────────────────────────────────────────────────────

    def _build_public_acls(
        self,
        object_type: str,
        records: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, str]]]:
        """All records get a grant-everyone ACE — no external group needed."""
        acl = [{"accessType": "grant", "type": "everyone", "value": "everyone"}]
        return {str(r["Id"]): acl for r in records if r.get("Id")}

    # ── PRIVATE OWD ───────────────────────────────────────────────────────────

    async def _build_private_acls(
        self,
        object_type: str,
        records: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, str]]]:
        """
        Build per-record ACLs using share data.

        Each record gets:
        1. The GlobalUsers group (ViewAll/ModifyAll admins)
        2. Per-share ACEs: user ACEs for direct user shares, group ACEs for
           role/queue/manager/organization shares
        """
        import time as _time

        # Ensure we have user and group data loaded
        if self._users_by_id is None:
            _t0 = _time.monotonic()
            users = await self._query_client.get_users_with_roles(object_type)
            self._users_by_id = {u.id: u for u in users}
            logger.info("[GroupACL][TIMING] Load users for %s: %.1fs (%d users)", object_type, _time.monotonic() - _t0, len(self._users_by_id))

        if self._frozen_users is None:
            _t0 = _time.monotonic()
            self._frozen_users = await self._query_client.get_frozen_user_ids()
            logger.info("[GroupACL][TIMING] Load frozen users: %.1fs (%d frozen)", _time.monotonic() - _t0, len(self._frozen_users))

        # Bulk-fetch shares so the per-record loop has data to process
        _share_t0 = _time.monotonic()
        await self._fetch_and_inject_shares(object_type, records)
        logger.info("[GroupACL][TIMING] Share fetch for %s: %.1fs", object_type, _time.monotonic() - _share_t0)

        acl_map: dict[str, list[dict[str, str]]] = {}
        _group_query_count = 0
        _group_query_time = 0.0

        for record in records:
            record_id = str(record.get("Id", ""))
            if not record_id:
                continue

            acls: list[dict[str, str]] = [
                _group_ace(SfGroupIdFormats.GLOBAL_USERS.format(object_type))
            ]

            shares = record.get("Shares", {})
            share_records = shares.get("records", []) if isinstance(shares, dict) else []
            processed_roles: set[str] = set()
            acl_failed = False

            for share in share_records:
                uog_id = share.get("UserOrGroupId", "")
                uog_type = (share.get("UserOrGroup") or {}).get("Type", "")

                if uog_type == "User":
                    ok = self._process_user_share(
                        acls, uog_id, object_type, processed_roles
                    )
                    if not ok:
                        acl_failed = True
                        break
                elif uog_id:
                    # Any non-User share (Queue, Role, Organization, etc.)
                    _gq_t0 = _time.monotonic()
                    await self._process_group_share(
                        acls, uog_id, object_type, processed_roles
                    )
                    _gq_dur = _time.monotonic() - _gq_t0
                    if _gq_dur > 0.001:  # only count actual SOQL calls
                        _group_query_count += 1
                        _group_query_time += _gq_dur

            if acl_failed:
                acl_map[record_id] = _deny_everyone_acl()
            else:
                acl_map[record_id] = acls

        if _group_query_count > 0:
            logger.info(
                "[GroupACL][TIMING] Group lookups for %s: %d SOQL queries in %.1fs (%.0f ms/query avg)",
                object_type, _group_query_count, _group_query_time,
                (_group_query_time / _group_query_count) * 1000,
            )

        return acl_map

    async def _fetch_and_inject_shares(
        self,
        object_type: str,
        records: list[dict[str, Any]],
        batch_size: int = 200,
    ) -> None:
        """Bulk-fetch share records from Salesforce and inject into record dicts.

        Queries ``{Object}Share`` in batches of *batch_size* so
        ``_build_private_acls`` can read ``record["Shares"]["records"]``
        without per-record SOQL.

        Skips records that already have ``Shares`` populated (e.g. tests).
        """
        # Skip if all records already have shares injected
        needs_fetch = [r for r in records if r.get("Id") and "Shares" not in r]
        if not needs_fetch:
            return

        # Skip objects whose share table doesn't exist (e.g. Product2, Pricebook2)
        if object_type in self._no_share_table_types:
            return

        share_table = _share_table_name(object_type)
        parent_field = "ParentId" if object_type.endswith("__c") else f"{object_type}Id"

        record_ids = [str(r["Id"]) for r in needs_fetch]
        shares_by_record: dict[str, list[dict]] = {rid: [] for rid in record_ids}
        total_shares = 0

        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i : i + batch_size]
            ids_in = ", ".join(f"'{rid}'" for rid in batch)
            soql = (
                f"SELECT {parent_field}, UserOrGroupId, UserOrGroup.Type "
                f"FROM {share_table} "
                f"WHERE {parent_field} IN ({ids_in})"
            )
            try:
                rows = await self._sf.query_all(soql)
                for r in rows:
                    parent_id = r.get(parent_field)
                    if parent_id and parent_id in shares_by_record:
                        shares_by_record[parent_id].append({
                            "UserOrGroupId": r.get("UserOrGroupId", ""),
                            "UserOrGroup": r.get("UserOrGroup") or {},
                        })
                        total_shares += 1
            except Exception as exc:
                exc_str = str(exc)
                if "INVALID_TYPE" in exc_str or "is not supported" in exc_str:
                    logger.info(
                        "[GroupACL] Share table %s does not exist — %s uses non-standard sharing; skipping",
                        share_table, object_type,
                    )
                    self._no_share_table_types.add(object_type)
                    return
                logger.warning(
                    "[GroupACL] Bulk share fetch from %s failed (batch %d): %s",
                    share_table, i // batch_size + 1, exc,
                )

        # Inject into records
        for record in needs_fetch:
            rid = str(record.get("Id", ""))
            if rid in shares_by_record:
                record["Shares"] = {"records": shares_by_record[rid]}

        logger.info(
            "[GroupACL] Fetched %d share(s) for %d %s record(s) from %s",
            total_shares, len(record_ids), object_type, share_table,
        )

    def _process_user_share(
        self,
        acls: list[dict[str, str]],
        user_id: str,
        object_type: str,
        processed_roles: set[str],
    ) -> bool:
        """Add ACEs for a direct user share.

        Returns True on success, False if user ACE resolution failed
        (caller should set the entire item ACL to deny-everyone).
        """
        user = (self._users_by_id or {}).get(user_id)
        frozen = self._frozen_users or set()

        if not user or not user.permission_sets or user.id in frozen:
            return True  # Skipped user, not a failure

        # Resolve user to AAD GUID — try FederationIdentifier → stripped UserName → Email → employeeId
        # in order, stopping at the first candidate that resolves successfully.
        # _resolve_identifier chains to _lookup_graph_user_id which covers both
        # Attempt 1 (direct path) and Attempt 2 (filter, including employeeId for alphanumeric identifiers).
        ace = None
        candidates = _best_user_identifiers(user)
        for identifier in candidates:
            if self._principal_mapper is not None:
                resolved = self._principal_mapper._resolve_identifier(identifier)
                if resolved:
                    ace = _user_ace_aad(resolved)
                    if ace:
                        break  # resolved and valid GUID
            else:
                ace = _user_ace_aad(identifier)
                if ace:
                    break  # identifier was already a GUID

        if not candidates:
            logger.warning(
                "[GroupACL] User %s (%s) — no valid identifier (FederationIdentifier/UserName/Email/employeeId all missing or invalid)",
                user_id, user.name,
            )
        elif not ace:
            logger.warning(
                "[GroupACL] User %s (%s) — tried %d candidate(s) %s, none resolved to AAD GUID → ACL resolution failed",
                user_id, user.name, len(candidates), candidates,
            )

        if ace:
            acls.append(ace)
        else:
            # Cannot resolve user to GUID — ACL resolution failed
            return False

        # Parent role group ACE (hierarchy-based implicit sharing)
        if user.parent_role_id and user.parent_role_id not in processed_roles:
            processed_roles.add(user.parent_role_id)
            acls.append(_group_ace(
                SfGroupIdFormats.ROLE.format(object_type, user.parent_role_id)
            ))

        return True

    async def _process_group_share(
        self,
        acls: list[dict[str, str]],
        group_id: str,
        object_type: str,
        processed_roles: set[str],
    ) -> None:
        """Add ACE for a group-type share."""
        # Lazy-load group details
        if self._groups_by_id is None:
            self._groups_by_id = {}

        if group_id not in self._groups_by_id:
            groups = await self._query_client.get_groups_by_ids([group_id])
            for g in groups:
                self._groups_by_id[g.id] = g

        group = self._groups_by_id.get(group_id)
        if not group:
            return

        ace_id: str | None = None
        gt = group.type

        if gt == UserOrGroupType.ROLE:
            if group.related_id not in processed_roles:
                processed_roles.add(group.related_id)
                ace_id = SfGroupIdFormats.ROLE.format(object_type, group.related_id)

        elif gt in (UserOrGroupType.ROLE_AND_SUBORDINATES, UserOrGroupType.ROLE_AND_SUBORDINATES_INTERNAL):
            ace_id = SfGroupIdFormats.ROLE_AND_SUBORDINATES.format(object_type, group.related_id)

        elif gt == UserOrGroupType.ORGANIZATION:
            ace_id = SfGroupIdFormats.ALL_INTERNAL_USERS.format(object_type)

        elif gt == UserOrGroupType.MANAGER:
            ace_id = SfGroupIdFormats.MANAGER.format(object_type, group.related_id)

        elif gt == UserOrGroupType.MANAGER_AND_SUBORDINATES_INTERNAL:
            ace_id = SfGroupIdFormats.MANAGER_AND_SUBORDINATES.format(object_type, group.related_id)

        elif gt == UserOrGroupType.TERRITORY:
            ace_id = SfGroupIdFormats.TERRITORY.format(object_type, group.related_id)

        elif gt in (UserOrGroupType.TERRITORY_AND_SUBORDINATES, UserOrGroupType.TERRITORY_AND_SUBORDINATES_INTERNAL):
            ace_id = SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format(object_type, group.related_id)

        else:
            # Regular / Queue → Public Group
            ace_id = SfGroupIdFormats.PUBLIC_GROUP.format(object_type, group_id)

        if ace_id:
            acls.append(_group_ace(ace_id))

    # ── CONTROLLED BY PARENT ─────────────────────────────────────────────────

    async def _build_controlled_by_parent_acls(
        self,
        object_type: str,
        records: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, str]]]:
        """Build ACLs for ControlledByParent objects by inheriting parent record ACLs.

        For each child record:
        1. Look up the parent record ID (e.g. Contact.AccountId).
        2. If the parent's ACL is already cached, reuse it.
        3. Otherwise, fetch the parent record + its share entries and build
           private ACLs using the same logic as ``_build_private_acls``.
        4. The child inherits the parent's ACL entries plus its own
           ``{child_object}-GlobalUsers`` group (ViewAll/ModifyAll admins
           for the child object type).

        The ``_parent_acl_cache`` accumulates across chunks so repeated
        parent lookups are free.
        """
        import time as _time

        parent_info = self._parent_map.get(object_type)
        if not parent_info:
            logger.warning(
                "[GroupACL] %s: ControlledByParent but no parent_map entry — "
                "falling back to GlobalUsers + deny-everyone",
                object_type,
            )
            return {
                str(r["Id"]): _deny_everyone_acl()
                for r in records if r.get("Id")
            }

        parent_field, parent_obj = parent_info

        # Ensure user data is loaded (needed for owner resolution on parent records)
        if self._users_by_id is None:
            users = await self._query_client.get_users_with_roles(object_type)
            self._users_by_id = {u.id: u for u in users}

        if self._frozen_users is None:
            self._frozen_users = await self._query_client.get_frozen_user_ids()

        # 1. Collect unique parent IDs that are NOT already cached
        parent_ids_needed: set[str] = set()
        for record in records:
            pid = record.get(parent_field)
            if pid and pid not in self._parent_acl_cache:
                parent_ids_needed.add(pid)

        # 2. Fetch and resolve uncached parent records
        if parent_ids_needed:
            _t0 = _time.monotonic()
            parent_records = await self._fetch_parent_records(
                parent_obj, parent_ids_needed,
            )
            logger.info(
                "[GroupACL][TIMING] Fetch %d parent %s record(s): %.1fs",
                len(parent_records), parent_obj, _time.monotonic() - _t0,
            )

            # Build private ACLs for the parent records (reuse existing logic).
            # _build_private_acls internally calls _fetch_and_inject_shares,
            # so we don't need to call it separately here.
            parent_acl_map = await self._build_private_acls(parent_obj, parent_records)

            # Store in cache
            for pid, acls in parent_acl_map.items():
                self._parent_acl_cache[pid] = acls

            logger.info(
                "[GroupACL] Cached %d new parent %s ACL(s) (total cache: %d)",
                len(parent_acl_map), parent_obj, len(self._parent_acl_cache),
            )

        # 3. Map each child record to its parent's ACL
        cache_hits = 0
        cache_misses = 0
        acl_map: dict[str, list[dict[str, str]]] = {}

        # Child's own GlobalUsers group — admins with ViewAll/ModifyAll on the
        # child object type can see all children regardless of parent ACL.
        child_global_ace = _group_ace(
            SfGroupIdFormats.GLOBAL_USERS.format(object_type)
        )

        for record in records:
            record_id = str(record.get("Id", ""))
            if not record_id:
                continue

            pid = record.get(parent_field)
            if pid and pid in self._parent_acl_cache:
                cache_hits += 1
                # Inherit parent ACL + child's own GlobalUsers
                parent_acls = self._parent_acl_cache[pid]
                # Prepend child GlobalUsers if not already present
                # (parent ACLs have parent's GlobalUsers; child needs its own too)
                acl_map[record_id] = [child_global_ace] + parent_acls
            else:
                cache_misses += 1
                if not pid:
                    # No parent reference — fall back to owner-based ACL
                    owner_acl = self._resolve_owner_acl(
                        record, object_type, child_global_ace,
                    )
                    if owner_acl is not None:
                        acl_map[record_id] = owner_acl
                    else:
                        acl_map[record_id] = _deny_everyone_acl()
                    logger.debug(
                        "[GroupACL] %s/%s: no %s value — owner-based ACL",
                        object_type, record_id, parent_field,
                    )
                else:
                    logger.warning(
                        "[GroupACL] %s/%s: parent %s/%s not resolved — deny-everyone",
                        object_type, record_id, parent_obj, pid,
                    )
                    acl_map[record_id] = _deny_everyone_acl()

        logger.info(
            "[GroupACL] %s CBP: %d records, %d cache hits, %d misses (no parent)",
            object_type, len(acl_map), cache_hits, cache_misses,
        )
        return acl_map

    def _resolve_owner_acl(
        self,
        record: dict[str, Any],
        object_type: str,
        global_ace: dict[str, str],
    ) -> list[dict[str, str]] | None:
        """Build a GlobalUsers + owner ACL for a record.

        Returns the ACL list, or ``None`` if the owner cannot be resolved
        (caller should fall back to deny-everyone).
        """
        acls: list[dict[str, str]] = [global_ace]
        owner_id = record.get("OwnerId", "")
        if not owner_id:
            return acls  # GlobalUsers only — no owner to resolve

        owner = (self._users_by_id or {}).get(owner_id)
        if not owner:
            logger.debug(
                "[GroupACL] Owner %s not in user cache for %s/%s — GlobalUsers only",
                owner_id, object_type, record.get("Id", ""),
            )
            return acls

        candidates = _best_user_identifiers(owner)
        for identifier in candidates:
            if self._principal_mapper is not None:
                resolved = self._principal_mapper._resolve_identifier(identifier)
                if resolved:
                    ace = _user_ace_aad(resolved)
                    if ace:
                        acls.append(ace)
                        return acls
            else:
                ace = _user_ace_aad(identifier)
                if ace:
                    acls.append(ace)
                    return acls

        # Couldn't resolve owner — still return GlobalUsers-only ACL
        logger.debug(
            "[GroupACL] Owner %s (%s) unresolvable — GlobalUsers only for %s/%s",
            owner_id, owner.name, object_type, record.get("Id", ""),
        )
        return acls

    async def _fetch_parent_records(
        self,
        parent_obj: str,
        parent_ids: set[str],
        batch_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch minimal parent records (Id + OwnerId) for ACL resolution.

        Returns synthetic record dicts suitable for ``_build_private_acls``.
        """
        records: list[dict[str, Any]] = []
        parent_id_list = sorted(parent_ids)

        for i in range(0, len(parent_id_list), batch_size):
            batch = parent_id_list[i : i + batch_size]
            ids_in = ", ".join(f"'{pid}'" for pid in batch)
            soql = f"SELECT Id, OwnerId FROM {parent_obj} WHERE Id IN ({ids_in})"
            try:
                rows = await self._sf.query_all(soql)
                for row in rows:
                    records.append({
                        "Id": row.get("Id"),
                        "OwnerId": row.get("OwnerId", ""),
                    })
            except Exception as exc:
                logger.warning(
                    "[GroupACL] Failed to fetch parent %s records (batch %d): %s",
                    parent_obj, i // batch_size + 1, exc,
                )

        logger.info(
            "[GroupACL] Fetched %d/%d parent %s record(s)",
            len(records), len(parent_ids), parent_obj,
        )
        return records

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_visibility(self, object_type: str) -> EntityVisibility:
        """Get the effective OWD visibility for an object type."""
        if self._owd_map and object_type in self._owd_map:
            return self._owd_map[object_type]
        return EntityVisibility.NONE  # Safe default


# ── Identifier resolution helper ────────────────────────────────────────────


def _best_user_identifiers(user: "SfUser") -> list[str]:
    """
    Return an ordered list of all valid identifiers for AAD ACE resolution.

    Priority: FederationIdentifier → stripped UserName → Email
    Each candidate is de-duplicated.  Bare values without '@' that are not
    GUIDs (e.g. numeric Salesforce IDs like '61273255') are rejected.

    Returning a list (instead of just the first match) lets callers try every
    candidate through Graph until one resolves — mirroring the behaviour of
    PrincipalMapper._resolve_principal.
    """
    def _strip_sf_suffix(username: str) -> str | None:
        """Strip the single Salesforce-appended org label from a username.

        Rules (mirrors principal_mapper._strip_sf_username_suffix):
        - Must contain exactly one '@'.
        - Domain must have MORE than 2 labels — 'acme.com' is never stripped.
        - Only the LAST label is removed: 'acme.com.sandbox' → 'acme.com'.
        """
        if "@" not in username:
            return None
        local, domain = username.rsplit("@", 1)
        labels = domain.split(".")
        if len(labels) <= 2:
            return None
        return f"{local}@{'.' .join(labels[:-1])}"

    seen: set[str] = set()
    candidates: list[str] = []

    def _add(val: str | None) -> None:
        if not val:
            return
        val = val.strip()
        if not val or val in seen:
            return
        # Reject bare values that have no '@' and are not GUIDs and are not
        # purely alphanumeric (which may be an employeeId, e.g. 'E12345').
        # e.g. mixed garbage like '61273255abc!!' is still rejected.
        if "@" not in val and not _looks_like_guid(val) and not val.isalnum():
            logger.debug(
                "[GroupACL] Skipping identifier '%s' for user %s — not a UPN, email, GUID, or alphanumeric employeeId",
                val, user.id,
            )
            return
        seen.add(val)
        candidates.append(val)

    # FederationIdentifier (raw, then stripped)
    _add(user.federation_identifier)
    if user.federation_identifier:
        _add(_strip_sf_suffix(user.federation_identifier))

    # UserName (stripped first — the raw SF username is rarely a valid UPN)
    if user.user_name:
        _add(_strip_sf_suffix(user.user_name))
        _add(user.user_name)  # raw as last resort

    # Email
    _add(user.email)

    return candidates


def _best_user_identifier(user: "SfUser") -> str | None:
    """Return the single highest-priority identifier (first from _best_user_identifiers)."""
    candidates = _best_user_identifiers(user)
    return candidates[0] if candidates else None


# ── Module-level ACE factories ────────────────────────────────────────────────


def _deny_everyone_acl() -> list[dict[str, str]]:
    """Return a deny-everyone ACL.

    Used when ACL resolution fails for an item (e.g. user cannot be resolved
    to an AAD GUID).  The item will be ingested but hidden from all users.
    """
    return [{"accessType": "deny", "type": "everyone", "value": "everyone"}]


def _group_ace(group_id: str) -> dict[str, str]:
    """Create a Group ACE referencing an external group."""
    return {
        "accessType": "grant",
        "type": "externalGroup",
        "value": group_id,
    }


def _user_ace_aad(federation_identifier: str) -> dict[str, str] | None:
    """Create a User ACE using AAD identity.

    Returns None if the identifier is not a valid GUID — Graph API rejects
    non-GUID values with ``InvalidGuid`` error.
    """
    if not _looks_like_guid(federation_identifier):
        return None
    return {
        "accessType": "grant",
        "type": "user",
        "value": federation_identifier,
    }


def _user_ace_external(user: SfUser) -> dict[str, str] | None:
    """Create a User ACE with external identity.

    Returns None — raw Salesforce IDs (005...) are never valid GUIDs and
    Graph API rejects them.  Users without AAD GUIDs should be resolved
    via the identity crawl group membership instead.
    """
    return None


def _looks_like_guid(value: str) -> bool:
    """Return True if *value* looks like an AAD Object GUID."""
    parts = value.strip().split("-")
    if len(parts) != 5:
        return False
    expected = (8, 4, 4, 4, 12)
    return all(
        len(p) == exp and all(c in "0123456789abcdefABCDEF" for c in p)
        for p, exp in zip(parts, expected)
    )
