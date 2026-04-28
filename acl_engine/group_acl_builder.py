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
    - ControlledByParent → "{Object}-GlobalUsers" + owner user ACE
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
    ) -> None:
        self._sf = sf_client
        self._query_client = IdentityQueryClient(sf_client, owd_field_map=owd_field_map)
        self._owd_overrides = owd_overrides or {}
        self._parent_map = parent_map or {}
        self._principal_mapper = principal_mapper
        # Caches populated on first use
        self._owd_map: dict[str, EntityVisibility] | None = None
        self._users_by_id: dict[str, SfUser] | None = None
        self._groups_by_id: dict[str, SfGroup] | None = None
        self._frozen_users: set[str] | None = None
        self._no_share_table_types: set[str] = set()

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

            logger.info("[GroupACL] %s: ControlledByParent → GlobalUsers + owner", object_type)
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

        # Direct user ACE (only if resolvable to AAD GUID)
        ace = None
        if user.federation_identifier:
            ace = _user_ace_aad(user.federation_identifier)
        else:
            ace = _user_ace_external(user)
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
        """Build ACLs for ControlledByParent objects: GlobalUsers + record owner."""
        if self._users_by_id is None:
            users = await self._query_client.get_users_with_roles(object_type)
            self._users_by_id = {u.id: u for u in users}

        acl_map: dict[str, list[dict[str, str]]] = {}

        for record in records:
            record_id = str(record.get("Id", ""))
            if not record_id:
                continue

            acls: list[dict[str, str]] = [
                _group_ace(SfGroupIdFormats.GLOBAL_USERS.format(object_type))
            ]

            owner_id = record.get("OwnerId", "")
            if owner_id:
                owner = (self._users_by_id or {}).get(owner_id)
                if owner:
                    # Mirror PrincipalMapper._resolve_principal:
                    # try FederationIdentifier → UserName (stripped) → Email in order
                    identifier = _best_user_identifier(owner)
                    if identifier:
                        source = (
                            "FederationIdentifier" if identifier == owner.federation_identifier
                            else "Email" if identifier == owner.email
                            else "UserName (stripped)"
                        )
                        # If a PrincipalMapper is available, resolve identifier → AAD GUID
                        if self._principal_mapper is not None:
                            resolved = self._principal_mapper._resolve_identifier(identifier)
                            if resolved:
                                logger.debug(
                                    "[GroupACL] Owner %s (%s) — resolved via %s '%s' → AAD GUID '%s'",
                                    owner_id, owner.name, source, identifier, resolved,
                                )
                                acls.append(_user_ace_aad(resolved))
                            else:
                                logger.warning(
                                    "[GroupACL] Owner %s (%s) — PrincipalMapper could not resolve '%s' to AAD GUID → skipping user ACE",
                                    owner_id, owner.name, identifier,
                                )
                        else:
                            logger.debug(
                                "[GroupACL] Owner %s (%s) — resolved via %s: '%s' → adding as user ACE (no PrincipalMapper)",
                                owner_id, owner.name, source, identifier,
                            )
                            acls.append(_user_ace_aad(identifier))
                    else:
                        logger.warning(
                            "[GroupACL] Owner %s (%s) — no identifier available "
                            "(FederationIdentifier/UserName/Email all missing) → applying deny-everyone ACL for record %s",
                            owner_id, owner.name, record_id,
                        )
                        acl_map[record_id] = _deny_everyone_acl()
                        continue
                else:
                    logger.warning(
                        "[GroupACL] Owner %s not found in user cache — no user ACE added for record %s",
                        owner_id, record_id,
                    )

            acl_map[record_id] = acls

        return acl_map

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_visibility(self, object_type: str) -> EntityVisibility:
        """Get the effective OWD visibility for an object type."""
        if self._owd_map and object_type in self._owd_map:
            return self._owd_map[object_type]
        return EntityVisibility.NONE  # Safe default


# ── Identifier resolution helper ────────────────────────────────────────────


def _best_user_identifier(user: "SfUser") -> str | None:
    """
    Return the best identifier to use as an AAD ACE value.

    Priority: FederationIdentifier → UserName (stripped of SF suffix) → Email
    Returns None if none are available.
    """
    import re

    def _strip_sf_suffix(username: str) -> str:
        """Strip the Salesforce-appended org suffix from a username."""
        # e.g. john@contoso.com.sandbox123 → john@contoso.com
        return re.sub(r'(\.[a-z0-9]+)+$', '', username, flags=re.IGNORECASE)

    for identifier in (
        user.federation_identifier,
        _strip_sf_suffix(user.user_name) if user.user_name else "",
        user.email,
    ):
        if identifier and identifier.strip():
            return identifier.strip()
    return None


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
