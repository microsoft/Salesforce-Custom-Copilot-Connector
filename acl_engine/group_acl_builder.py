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
    ) -> None:
        self._sf = sf_client
        self._query_client = IdentityQueryClient(sf_client, owd_field_map=owd_field_map)
        self._owd_overrides = owd_overrides or {}
        self._parent_map = parent_map or {}
        # Caches populated on first use
        self._owd_map: dict[str, EntityVisibility] | None = None
        self._users_by_id: dict[str, SfUser] | None = None
        self._groups_by_id: dict[str, SfGroup] | None = None
        self._frozen_users: set[str] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

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
        logger.info("[GroupACL] Starting group-based ACL resolution")

        # Load OWD map once
        if self._owd_map is None:
            self._owd_map = await self._query_client.get_org_wide_defaults()
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
        """All records get a single group ACE referencing the TopLevel group."""
        group_id = SfGroupIdFormats.TOP_LEVEL.format(object_type)
        acl = [_group_ace(group_id)]
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
        # Ensure we have user and group data loaded
        if self._users_by_id is None:
            users = await self._query_client.get_users_with_roles(object_type)
            self._users_by_id = {u.id: u for u in users}

        if self._frozen_users is None:
            self._frozen_users = await self._query_client.get_frozen_user_ids()

        acl_map: dict[str, list[dict[str, str]]] = {}

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

            for share in share_records:
                uog_id = share.get("UserOrGroupId", "")
                uog_type = (share.get("UserOrGroup") or {}).get("Type", "")

                if uog_type == "User":
                    self._process_user_share(
                        acls, uog_id, object_type, processed_roles
                    )
                elif uog_type == "Queue":
                    await self._process_group_share(
                        acls, uog_id, object_type, processed_roles
                    )

            acl_map[record_id] = acls

        return acl_map

    def _process_user_share(
        self,
        acls: list[dict[str, str]],
        user_id: str,
        object_type: str,
        processed_roles: set[str],
    ) -> None:
        """Add ACEs for a direct user share."""
        user = (self._users_by_id or {}).get(user_id)
        frozen = self._frozen_users or set()

        if not user or not user.permission_sets or user.id in frozen:
            return

        # Direct user ACE
        if user.federation_identifier:
            acls.append(_user_ace_aad(user.federation_identifier))
        else:
            acls.append(_user_ace_external(user))

        # Parent role group ACE (hierarchy-based implicit sharing)
        if user.parent_role_id and user.parent_role_id not in processed_roles:
            processed_roles.add(user.parent_role_id)
            acls.append(_group_ace(
                SfGroupIdFormats.ROLE.format(object_type, user.parent_role_id)
            ))

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
                    if owner.federation_identifier:
                        acls.append(_user_ace_aad(owner.federation_identifier))
                    else:
                        acls.append(_user_ace_external(owner))

            acl_map[record_id] = acls

        return acl_map

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_visibility(self, object_type: str) -> EntityVisibility:
        """Get the effective OWD visibility for an object type."""
        if self._owd_map and object_type in self._owd_map:
            return self._owd_map[object_type]
        return EntityVisibility.NONE  # Safe default


# ── Module-level ACE factories ────────────────────────────────────────────────


def _group_ace(group_id: str) -> dict[str, str]:
    """Create a Group ACE referencing an external group."""
    return {
        "accessType": "grant",
        "type": "externalGroup",
        "value": group_id,
    }


def _user_ace_aad(federation_identifier: str) -> dict[str, str]:
    """Create a User ACE using AAD mail mapping."""
    return {
        "accessType": "grant",
        "type": "user",
        "value": federation_identifier,
    }


def _user_ace_external(user: SfUser) -> dict[str, str]:
    """Create a User ACE with external identity source properties."""
    return {
        "accessType": "grant",
        "type": "user",
        "value": user.id,
    }
