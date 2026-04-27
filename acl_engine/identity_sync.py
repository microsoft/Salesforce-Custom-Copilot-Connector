"""
acl_engine/identity_sync.py
----------------------------
Identity Crawl handler — creates and populates external groups in Microsoft
Graph that mirror Salesforce's sharing model.

The identity crawl runs as a separate scenario from content ingestion.  It
creates the external groups that content item ACLs reference (via
``group_acl_builder.py``).

Architecture
------------
``IdentitySyncHandler``
    Top-level orchestrator.  Call ``run_full_crawl()`` or
    ``run_incremental_crawl()`` (both produce the same output since the
    connector always emits complete membership).

``IdentityCrawlEnumerator``
    List phase — queries OWD and emits one top-level group per SF object.

``IdentityGatherer``
    Gather phase — populates each group with users and child groups based
    on the object's OWD and share records.

User Removal
------------
Both full and incremental crawls emit the **complete current membership**
every time.  The framework detects stale edges (members present in the
previous crawl but absent in the current one) and removes them
automatically.  This module **never** explicitly removes users or groups.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from acl_engine.group_id_formats import SfGroupIdFormats
from acl_engine.identity_models import (
    EntityVisibility,
    GroupIdentityType,
    SfGroup,
    SfUser,
    UserOrGroupType,
    is_controlled_by_parent,
    is_public_visibility,
    parse_visibility,
)
from acl_engine.identity_queries import IdentityQueryClient
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine.identity")


# ── Result containers ─────────────────────────────────────────────────────────

@dataclass
class GroupMembership:
    """Represents an external group and its membership."""
    group_id: str
    display_name: str = ""
    users: list[SfUser] = field(default_factory=list)
    child_groups: list[ChildGroupRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChildGroupRef:
    """Reference to a child group that should be nested inside a parent group."""
    group_id: str
    group_type: GroupIdentityType
    needs_gather: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopLevelGroupInfo:
    """Metadata for a top-level group emitted during the list phase."""
    group_id: str
    object_name: str
    owd: EntityVisibility
    display_name: str = ""


@dataclass
class IdentityCrawlResult:
    """Complete result of an identity crawl run."""
    top_level_groups: list[TopLevelGroupInfo] = field(default_factory=list)
    gathered_groups: list[GroupMembership] = field(default_factory=list)
    total_users_emitted: int = 0
    total_groups_emitted: int = 0


# ── Main handler ──────────────────────────────────────────────────────────────

class IdentitySyncHandler:
    """
    Top-level handler for identity crawl operations.

    Parameters
    ----------
    sf_client       : Authenticated ``SalesforceClient``.
    object_names    : List of SF object names to crawl (e.g. ["Account", "Lead"]).
    parent_map      : ``{object_name: (parent_field, parent_object)}`` from config.
    owd_overrides   : Optional OWD overrides.
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        object_names: list[str],
        parent_map: dict[str, tuple[str, str]] | None = None,
        owd_overrides: dict[str, str] | None = None,
        owd_field_map: dict[str, str] | None = None,
    ) -> None:
        self._sf = sf_client
        self._object_names = object_names
        self._parent_map = parent_map or {}
        self._owd_overrides = owd_overrides or {}
        self._query_client = IdentityQueryClient(sf_client, owd_field_map=owd_field_map)

    # ── Public API ────────────────────────────────────────────────────────────

    def run_full_crawl(self) -> IdentityCrawlResult:
        """
        Execute a full identity crawl (synchronous wrapper).

        Returns an ``IdentityCrawlResult`` containing all top-level groups
        and their fully populated memberships.
        """
        return asyncio.run(self.run_full_crawl_async())

    async def run_full_crawl_async(self) -> IdentityCrawlResult:
        """Async implementation of the full identity crawl."""
        result = IdentityCrawlResult()

        # Phase 1: List — emit top-level groups
        logger.info("[IdentitySync] Phase 1: Enumerating top-level groups")
        enumerator = IdentityCrawlEnumerator(
            self._query_client,
            self._object_names,
            self._parent_map,
            self._owd_overrides,
        )
        result.top_level_groups = await enumerator.enumerate()

        logger.info(
            "[IdentitySync] Emitted %d top-level group(s)",
            len(result.top_level_groups),
        )

        # Phase 2: Gather — populate each group
        logger.info("[IdentitySync] Phase 2: Gathering group memberships")
        gatherer = IdentityGatherer(self._query_client)

        for top_group in result.top_level_groups:
            logger.info(
                "[IdentitySync] Gathering %s (OWD=%s)",
                top_group.group_id,
                top_group.owd.value,
            )
            membership = await gatherer.build_top_level_group(
                top_group.object_name,
                top_group.owd,
            )
            result.gathered_groups.append(membership)
            result.total_users_emitted += len(membership.users)
            result.total_groups_emitted += 1

            # Gather child groups
            for child_ref in membership.child_groups:
                if child_ref.needs_gather:
                    child_membership = await gatherer.gather_child_group(
                        top_group.object_name,
                        child_ref,
                    )
                    result.gathered_groups.append(child_membership)
                    result.total_users_emitted += len(child_membership.users)
                    result.total_groups_emitted += 1

        logger.info(
            "[IdentitySync] Crawl complete: %d groups, %d user memberships",
            result.total_groups_emitted,
            result.total_users_emitted,
        )
        return result

    def run_incremental_crawl(self) -> IdentityCrawlResult:
        """
        Execute an incremental identity crawl.

        Per the Salesforce connector pattern, incremental crawl emits
        the SAME output as a full crawl.  The framework handles stale
        edge detection automatically.
        """
        return self.run_full_crawl()


# ── List phase ────────────────────────────────────────────────────────────────

class IdentityCrawlEnumerator:
    """
    Emits one ``TopLevelGroupInfo`` per configured SF object.

    Queries OWD from Salesforce and determines the effective visibility
    for each object (handling ControlledByParent resolution).
    """

    def __init__(
        self,
        query_client: IdentityQueryClient,
        object_names: list[str],
        parent_map: dict[str, tuple[str, str]],
        owd_overrides: dict[str, str],
    ) -> None:
        self._query = query_client
        self._object_names = object_names
        self._parent_map = parent_map
        self._owd_overrides = owd_overrides

    async def enumerate(self) -> list[TopLevelGroupInfo]:
        """Query OWD and emit top-level groups for all configured objects."""
        owd_map = await self._query.get_org_wide_defaults()

        # Apply overrides
        for obj_name, raw_owd in self._owd_overrides.items():
            owd_map[obj_name] = parse_visibility(raw_owd)

        groups: list[TopLevelGroupInfo] = []
        for object_name in self._object_names:
            visibility = owd_map.get(object_name, EntityVisibility.NONE)

            # Handle ControlledByParent: check if parent is public
            if is_controlled_by_parent(visibility):
                parent_info = self._parent_map.get(object_name)
                if parent_info:
                    parent_obj = parent_info[1]
                    parent_vis = owd_map.get(parent_obj, EntityVisibility.NONE)
                    if is_public_visibility(parent_vis):
                        visibility = parent_vis

            group_id = SfGroupIdFormats.TOP_LEVEL.format(object_name)
            groups.append(TopLevelGroupInfo(
                group_id=group_id,
                object_name=object_name,
                owd=visibility,
                display_name=object_name,
            ))
            logger.info(
                "[IdentityEnum] %s → %s (OWD=%s)",
                object_name,
                group_id,
                visibility.value,
            )

        return groups


# ── Gather phase ──────────────────────────────────────────────────────────────

class IdentityGatherer:
    """
    Core logic for populating identity groups with users and child groups.

    Handles both top-level groups (PUBLIC vs PRIVATE OWD) and child groups
    (roles, public groups, managers, all-internal-users, global-access-users).
    """

    def __init__(self, query_client: IdentityQueryClient) -> None:
        self._query = query_client
        # Caches populated during crawl
        self._role_hierarchy: dict[str, str] | None = None
        self._roles_assigned: set[str] | None = None

    async def build_top_level_group(
        self,
        object_name: str,
        visibility: EntityVisibility,
    ) -> GroupMembership:
        """
        Build the top-level group for an object.

        For PUBLIC OWD: populates with all authorized users.
        For PRIVATE OWD: creates child groups from share data.
        """
        group_id = SfGroupIdFormats.TOP_LEVEL.format(object_name)
        membership = GroupMembership(
            group_id=group_id,
            display_name=object_name,
            metadata={
                "ObjectName": object_name,
                "OrgWideDefault": visibility.value,
            },
        )

        if is_public_visibility(visibility):
            # PUBLIC OWD: content items use grant-everyone ACL, so no
            # external group membership is needed.  Skip the expensive
            # authorized-users query entirely.
            logger.info(
                "[IdentityGather] %s PUBLIC: skipped (grant-everyone used for content ACLs)",
                object_name,
            )
        else:
            # PRIVATE OWD: build child group structure
            await self._build_private_children(membership, object_name)

        return membership

    async def gather_child_group(
        self,
        object_name: str,
        child_ref: ChildGroupRef,
    ) -> GroupMembership:
        """Populate a child group with its members."""
        membership = GroupMembership(
            group_id=child_ref.group_id,
            metadata=child_ref.metadata,
        )
        group_type = child_ref.group_type

        if group_type == GroupIdentityType.GLOBAL_ACCESS_USERS:
            membership.users = await self._query.get_global_access_users(object_name)

        elif group_type in (GroupIdentityType.ROLE_WITH_PARENT, GroupIdentityType.ROLE_WITHOUT_PARENT):
            role_id = child_ref.metadata.get("RoleId", "")
            parent_role_id = child_ref.metadata.get("ParentRoleId", "")

            membership.users = await self._query.get_users_for_role(role_id)

            # Nest parent role group (edge only, not re-gathered)
            if parent_role_id:
                membership.child_groups.append(ChildGroupRef(
                    group_id=SfGroupIdFormats.ROLE.format(object_name, parent_role_id),
                    group_type=GroupIdentityType.ROLE_WITH_PARENT,
                    needs_gather=False,
                ))

        elif group_type in (GroupIdentityType.ROLE_AND_SUB_WITH_PARENT, GroupIdentityType.ROLE_AND_SUB_WITHOUT_PARENT):
            role_id = child_ref.metadata.get("RoleId", "")
            parent_role_id = child_ref.metadata.get("ParentRoleId", "")
            child_roles = child_ref.metadata.get("ChildRoles", [])

            membership.users = await self._query.get_users_for_role(role_id)

            # Nest child role sub-groups
            for child_role_id in child_roles:
                membership.child_groups.append(ChildGroupRef(
                    group_id=SfGroupIdFormats.ROLE_AND_SUBORDINATES_NO_PARENTS.format(object_name, child_role_id),
                    group_type=GroupIdentityType.ROLE_AND_SUB_WITHOUT_PARENT,
                    needs_gather=False,
                ))

            # Nest parent role
            if parent_role_id:
                membership.child_groups.append(ChildGroupRef(
                    group_id=SfGroupIdFormats.ROLE.format(object_name, parent_role_id),
                    group_type=GroupIdentityType.ROLE_WITH_PARENT,
                    needs_gather=False,
                ))

        elif group_type == GroupIdentityType.ALL_INTERNAL_USERS:
            membership.users = await self._query.get_authorized_users(object_name)

        elif group_type == GroupIdentityType.PUBLIC_GROUP:
            pg_id = child_ref.metadata.get("PublicGroupId", "")
            membership.users = await self._query.get_group_member_users(pg_id)

        elif group_type == GroupIdentityType.MANAGER:
            related_id = child_ref.metadata.get("RelatedId", "")
            user = await self._query.get_user_by_id(related_id)
            if user:
                membership.users = [user]

        elif group_type == GroupIdentityType.MANAGER_AND_SUBORDINATES:
            related_id = child_ref.metadata.get("RelatedId", "")
            membership.users = await self._query.get_manager_and_subordinates(related_id)

        elif group_type == GroupIdentityType.TERRITORY:
            related_id = child_ref.metadata.get("RelatedId", "")
            membership.users = await self._query.get_territory_users(related_id)

        elif group_type == GroupIdentityType.TERRITORY_AND_SUBORDINATES:
            related_id = child_ref.metadata.get("RelatedId", "")
            # Users in this territory
            users = await self._query.get_territory_users(related_id)
            # Users in all descendant territories
            descendant_ids = await self._query.get_all_descendant_territory_ids(related_id)
            for desc_id in descendant_ids:
                users.extend(await self._query.get_territory_users(desc_id))
            # Deduplicate by user ID
            seen: set[str] = set()
            deduped: list[SfUser] = []
            for u in users:
                if u.id not in seen:
                    seen.add(u.id)
                    deduped.append(u)
            membership.users = deduped

        logger.info(
            "[IdentityGather] Child %s: %d user(s), %d nested group(s)",
            child_ref.group_id,
            len(membership.users),
            len(membership.child_groups),
        )
        return membership

    # ── Private OWD child group construction ──────────────────────────────────

    async def _build_private_children(
        self,
        membership: GroupMembership,
        object_name: str,
    ) -> None:
        """Create all child groups for a PRIVATE OWD object."""
        # 1. GlobalUsers child group
        membership.child_groups.append(ChildGroupRef(
            group_id=SfGroupIdFormats.GLOBAL_USERS.format(object_name),
            group_type=GroupIdentityType.GLOBAL_ACCESS_USERS,
            needs_gather=True,
            metadata={"ChildGroupType": GroupIdentityType.GLOBAL_ACCESS_USERS.value},
        ))

        # 2. Load role hierarchy
        if self._role_hierarchy is None:
            self._role_hierarchy = await self._query.get_role_hierarchy()
        if self._roles_assigned is None:
            self._roles_assigned = await self._query.get_roles_assigned_to_users()

        # 3. Query group shares for this object
        group_share_ids = await self._query.get_group_share_ids(object_name)

        # 4. Load group details
        sf_groups: list[SfGroup] = []
        if group_share_ids:
            sf_groups = await self._query.get_groups_by_ids(group_share_ids)

        created_groups: set[str] = set()

        # 5. Process group shares
        for sf_group in sf_groups:
            self._create_child_group_for_sf_group(
                membership, object_name, sf_group, created_groups
            )

        logger.info(
            "[IdentityGather] %s PRIVATE: %d child group(s)",
            object_name,
            len(membership.child_groups),
        )

    def _create_child_group_for_sf_group(
        self,
        membership: GroupMembership,
        object_name: str,
        sf_group: SfGroup,
        created: set[str],
    ) -> None:
        """Create the appropriate child group(s) for a Salesforce Group record."""
        gtype = sf_group.type
        hierarchy = self._role_hierarchy or {}

        if gtype in (UserOrGroupType.ROLE, UserOrGroupType.ROLE_AND_SUBORDINATES, UserOrGroupType.ROLE_AND_SUBORDINATES_INTERNAL):
            self._create_role_groups_with_hierarchy(
                membership, object_name, sf_group.related_id, created
            )

        elif gtype == UserOrGroupType.ORGANIZATION:
            gid = SfGroupIdFormats.ALL_INTERNAL_USERS.format(object_name)
            if gid not in created:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.ALL_INTERNAL_USERS,
                    needs_gather=True,
                    metadata={"ChildGroupType": GroupIdentityType.ALL_INTERNAL_USERS.value},
                ))

        elif gtype == UserOrGroupType.MANAGER:
            gid = SfGroupIdFormats.MANAGER.format(object_name, sf_group.related_id)
            if gid not in created:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.MANAGER,
                    needs_gather=True,
                    metadata={
                        "ChildGroupType": GroupIdentityType.MANAGER.value,
                        "RelatedId": sf_group.related_id,
                    },
                ))

        elif gtype == UserOrGroupType.MANAGER_AND_SUBORDINATES_INTERNAL:
            gid = SfGroupIdFormats.MANAGER_AND_SUBORDINATES.format(object_name, sf_group.related_id)
            if gid not in created:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.MANAGER_AND_SUBORDINATES,
                    needs_gather=True,
                    metadata={
                        "ChildGroupType": GroupIdentityType.MANAGER_AND_SUBORDINATES.value,
                        "RelatedId": sf_group.related_id,
                    },
                ))

        elif gtype == UserOrGroupType.TERRITORY:
            gid = SfGroupIdFormats.TERRITORY.format(object_name, sf_group.related_id)
            if gid not in created:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.TERRITORY,
                    needs_gather=True,
                    metadata={
                        "ChildGroupType": GroupIdentityType.TERRITORY.value,
                        "RelatedId": sf_group.related_id,
                    },
                ))

        elif gtype in (UserOrGroupType.TERRITORY_AND_SUBORDINATES, UserOrGroupType.TERRITORY_AND_SUBORDINATES_INTERNAL):
            gid = SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format(object_name, sf_group.related_id)
            if gid not in created:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.TERRITORY_AND_SUBORDINATES,
                    needs_gather=True,
                    metadata={
                        "ChildGroupType": GroupIdentityType.TERRITORY_AND_SUBORDINATES.value,
                        "RelatedId": sf_group.related_id,
                    },
                ))

        else:
            # Regular / Queue → Public Group
            gid = SfGroupIdFormats.PUBLIC_GROUP.format(object_name, sf_group.id)
            if gid not in created and sf_group.group_members:
                created.add(gid)
                membership.child_groups.append(ChildGroupRef(
                    group_id=gid,
                    group_type=GroupIdentityType.PUBLIC_GROUP,
                    needs_gather=True,
                    metadata={
                        "ChildGroupType": GroupIdentityType.PUBLIC_GROUP.value,
                        "PublicGroupId": sf_group.id,
                    },
                ))

    def _create_role_groups_with_hierarchy(
        self,
        membership: GroupMembership,
        object_name: str,
        role_id: str,
        created: set[str],
    ) -> None:
        """Create Role group and walk up the hierarchy."""
        hierarchy = self._role_hierarchy or {}
        current_role = role_id

        while current_role and current_role not in created:
            created.add(current_role)
            parent_role = hierarchy.get(current_role, "")
            child_roles = [r for r, p in hierarchy.items() if p == current_role]

            has_parent = bool(parent_role)
            group_type = (
                GroupIdentityType.ROLE_WITH_PARENT
                if has_parent
                else GroupIdentityType.ROLE_WITHOUT_PARENT
            )

            group_id = SfGroupIdFormats.ROLE.format(object_name, current_role)
            membership.child_groups.append(ChildGroupRef(
                group_id=group_id,
                group_type=group_type,
                needs_gather=True,
                metadata={
                    "ChildGroupType": group_type.value,
                    "RoleId": current_role,
                    "ParentRoleId": parent_role,
                    "ChildRoles": child_roles,
                },
            ))

            current_role = parent_role
