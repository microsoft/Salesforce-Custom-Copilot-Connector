"""
Legacy ACL resolver for Salesforce → Microsoft Graph external items.

This module implements the *legacy* access-control-list resolution pipeline.
For each Salesforce record it determines which Azure AD principals (users)
should be granted access in the Microsoft Graph external connection, based on
Salesforce's sharing model:

* **Organisation-Wide Defaults (OWD)** — when an object's OWD is ``Public``,
  all tenant users are granted access.  When it is ``Private`` or
  ``ControlledByParent``, the resolver drills into record-level sharing.
* **Record ownership** — the record owner always receives access.
* **Role hierarchy** — users in roles above the owner's role inherit access.
* **Sharing rules & manual shares** — ``EntityShare`` records are queried to
  discover additional grantees (users and groups).
* **Group expansion** — public groups and queues are recursively expanded to
  their member users.
* **Territory management** — if territories are in use, territory membership
  is resolved and merged into the ACL.
* **Parent-chain inheritance** — objects with ``ControlledByParent`` OWD
  inherit their parent record's ACL (up to ``ACL_MAX_PARENT_DEPTH``).

The newer ``acl_engine`` package is a modular rewrite of this logic.  Set
``USE_NEW_ACL_ENGINE=true`` in the environment to use it instead.

Classes
-------
AclResolver
    Instantiated per ingestion run.  Call :meth:`resolve` with a dict of
    ``{object_type: [records]}`` to receive a nested dict of
    ``{object_type: {record_id: [acl_entry]}}`` ready for the Graph PUT payload.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote
import asyncio
import logging

from graph.client import GraphApiError, GraphClient
from salesforce.sharing_model import (
    AsyncSalesforceClient,
    ClientHelperForIdentitySync,
    EntityShareBase,
    EntityVisibility,
    GroupMember,
    RecordEnumerationDirection,
    User,
    UserOrGroupType,
)
from item.converter import SalesforceObjectHandler
from salesforce.api_client import get_salesforce_access_token
from salesforce.settings import AppConfig


USER_ID_PREFIX = "005"
logger = logging.getLogger("salesforce_connector")


class AclResolver:
    def __init__(
        self,
        config: AppConfig,
        handlers: dict[str, SalesforceObjectHandler],
        graph_client: GraphClient | None = None,
    ):
        """Initialize the ACL resolver with config, object handlers, and optional Graph client for GUID lookups."""
        self._config = config
        self._handlers = handlers
        self._graph_client = graph_client
        self._tenant_id = config.tenant_id
        self._helper = ClientHelperForIdentitySync(
            AsyncSalesforceClient(
                config.connector.salesforce.instance_url,
                config.connector.salesforce.api_version,
            ),
            config.connector.salesforce.instance_url,
            get_salesforce_access_token(config),
            batch_size=config.tuning.salesforce_batch_size,
        )
        self._group_cache: dict[str, tuple[set[str], bool]] = {}
        self._principal_id_cache: dict[str, str | None] = {}
        self._role_children_cache: dict[str, set[str]] | None = None
        self._users_and_managers: list[User] | None = None
        self._frozen_users: set[str] | None = None
        # territory ACL caches
        self._territory_parent_cache: dict[str, str | None] = {}
        self._territory_users_cache: dict[str, set[str]] = {}
        # bulk pre-warm caches (populated by prewarm_caches)
        self._object_territory_assoc: dict[str, list[str]] | None = None
        self._all_territory_parents: dict[str, str | None] | None = None
        self._all_territory_users: dict[str, set[str]] | None = None
        self._all_groups_by_id: dict[str, Any] | None = None
        self._all_group_members_by_group: dict[str, list[str]] | None = None
        self._role_parent_map: dict[str, str] | None = None
        self._users_by_role: dict[str, set[str]] | None = None
        self._prewarmed: bool = False

    def resolve(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Resolve ACLs for all records. Returns ``{object_type: {record_id: [acl_entry]}}``."""
        if not records_by_object_type:
            return {}
        return asyncio.run(self._resolve_async(records_by_object_type))

    async def prewarm_caches(self) -> None:
        """Bulk-fetch all reference data from Salesforce in a few SOQL calls.

        After this method completes the ACL resolver can resolve territories,
        groups, role hierarchies, and user-to-role mappings entirely in-memory
        without any per-record SOQL queries.

        Typical Salesforce orgs:
        - Territory2:                      < 500 rows
        - ObjectTerritory2Association:     < 50 000 rows
        - UserTerritory2Association:       < 10 000 rows
        - Group + GroupMember:             < 5 000 rows
        - UserRole:                        < 1 000 rows
        - Users (active, with roles):      < 50 000 rows

        All of these fit comfortably in RAM and are fetched in seconds.
        """
        if self._prewarmed:
            return

        logger.info("=" * 70)
        logger.info("BULK PRE-WARM: Fetching all ACL reference data from Salesforce")
        logger.info("=" * 70)

        import time as _time
        t0 = _time.monotonic()

        # 1. Role hierarchy  ────────────────────────────────────────────────
        roles = await self._helper.get_user_role_hierarchy_from_salesforce(fetch_all=True)
        children_by_parent: dict[str, set[str]] = defaultdict(set)
        parent_map: dict[str, str] = {}
        for role in roles:
            if role.ParentRoleId and role.Id:
                children_by_parent[role.ParentRoleId].add(role.Id)
                parent_map[role.Id] = role.ParentRoleId
        self._role_children_cache = dict(children_by_parent)
        self._role_parent_map = parent_map
        logger.info("  Roles: %d records, %d parent links", len(roles), len(parent_map))

        # 2. Users with role info (for role→user mapping) ──────────────────
        all_users = await self._helper.get_users_from_salesforce(fetch_all=True)
        users_by_role: dict[str, set[str]] = defaultdict(set)
        for user in all_users:
            if user.UserRoleId and user.Id:
                users_by_role[user.UserRoleId].add(user.Id)
        self._users_by_role = dict(users_by_role)
        self._users_and_managers = all_users
        logger.info("  Users: %d active, %d with roles", len(all_users), sum(len(v) for v in users_by_role.values()))

        # 3. Frozen users ──────────────────────────────────────────────────
        frozen_logins = await self._helper.get_frozen_users()
        self._frozen_users = {ul.UserId for ul in frozen_logins if ul.UserId}
        logger.info("  Frozen users: %d", len(self._frozen_users))

        # 4. Territory data (all 3 tables in parallel) ─────────────────────
        #    Territory Management 2.0 objects may not exist in every org —
        #    treat any error as "no territory data" rather than crashing.
        try:
            terr_parents_task = self._helper.bulk_fetch_all_territories()
            obj_terr_task = self._helper.bulk_fetch_object_territory_associations()
            user_terr_task = self._helper.bulk_fetch_user_territory_associations()
            (
                self._all_territory_parents,
                self._object_territory_assoc,
                self._all_territory_users,
            ) = await asyncio.gather(terr_parents_task, obj_terr_task, user_terr_task)
        except Exception as exc:
            logger.warning("  Territory bulk fetch failed (Territory Management may not be enabled): %s", exc)
            self._all_territory_parents = {}
            self._object_territory_assoc = {}
            self._all_territory_users = {}

        # Also fill the per-territory caches used by the fallback code paths
        self._territory_parent_cache = dict(self._all_territory_parents)
        self._territory_users_cache = dict(self._all_territory_users)
        logger.info(
            "  Territories: %d nodes, %d object associations, %d user associations",
            len(self._all_territory_parents),
            sum(len(v) for v in self._object_territory_assoc.values()),
            sum(len(v) for v in self._all_territory_users.values()),
        )

        # 5. Groups + group members (in parallel) ─────────────────────────
        groups_task = self._helper.bulk_fetch_all_groups()
        members_task = self._helper.bulk_fetch_all_group_members()
        all_groups, self._all_group_members_by_group = await asyncio.gather(groups_task, members_task)
        self._all_groups_by_id = {g.Id: g for g in all_groups if g.Id}
        logger.info(
            "  Groups: %d, GroupMembers: %d",
            len(self._all_groups_by_id),
            sum(len(v) for v in self._all_group_members_by_group.values()),
        )

        elapsed = _time.monotonic() - t0
        self._prewarmed = True
        logger.info("BULK PRE-WARM COMPLETE in %.1fs", elapsed)
        logger.info("=" * 70)

    async def _resolve_async(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Async implementation that resolves ACLs per object type using OWD visibility."""
        logger.info("ACL RESOLUTION START")

        # Bulk pre-warm all reference data on first invocation
        await self.prewarm_caches()
        
        acl_maps: dict[str, dict[str, list[dict[str, str]]]] = {}
        visibility_map = await self._helper.get_org_wide_defaults_map()
        
        logger.info("\nOrg-Wide Defaults (OWD) Visibility Map:")
        for obj_name, visibility in visibility_map.items():
            logger.info("  %s: %s", obj_name, visibility)

        object_order = self._sort_object_names(records_by_object_type.keys())
        logger.info("\nObject processing order: %s", object_order)
        
        for object_name in object_order:
            records = records_by_object_type.get(object_name, [])
            if not records:
                continue
            
            logger.info("Processing object: %s (%d records)", object_name, len(records))
            
            acl_maps[object_name] = await self._build_acl_map_for_object(
                object_name,
                records,
                visibility_map,
                acl_maps,
            )

        logger.info("ACL RESOLUTION COMPLETE")
        
        return acl_maps

    async def _build_acl_map_for_object(
        self,
        object_name: str,
        records: list[dict[str, Any]],
        visibility_map: dict[str, EntityVisibility],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
        """Build the ACL map for one object type based on its OWD visibility setting."""
        visibility = visibility_map.get(object_name, EntityVisibility.PUBLIC_READ_ONLY)

        logger.info("Visibility for %s: %s", object_name, visibility)

        if self._is_public_visibility(visibility):
            logger.info("  → Public visibility - granting everyone access")
            return {record["Id"]: self._public_acl() for record in records if record.get("Id")}

        if self._is_controlled_by_parent(visibility):
            logger.info("  → Controlled by parent - using parent ACLs")
            return await self._build_parent_controlled_acl_map(object_name, records, acl_maps)

        logger.info("  → Private visibility - building custom ACLs")
        return await self._build_private_acl_map(object_name, records)

    async def _build_parent_controlled_acl_map(
        self,
        object_name: str,
        records: list[dict[str, Any]],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
        """Build ACLs for objects with ControlledByParent visibility by inheriting from parent records."""
        handler = self._handlers.get(object_name)
        parent_object_name = handler.parent_object_name if handler else None
        if not parent_object_name or handler is None:
            return await self._build_private_acl_map(object_name, records)

        parent_acl_map = acl_maps.get(parent_object_name, {})
        resolved: dict[str, list[dict[str, str]]] = {}
        unresolved_records: list[dict[str, Any]] = []

        for record in records:
            record_id = record.get("Id")
            parent_record_id = handler.get_parent_record_id(record)
            if record_id and parent_record_id and parent_record_id in parent_acl_map:
                resolved[record_id] = parent_acl_map[parent_record_id]
            else:
                unresolved_records.append(record)

        if unresolved_records:
            resolved.update(await self._build_private_acl_map(object_name, unresolved_records))

        return resolved

    async def _build_private_acl_map(
        self,
        object_name: str,
        records: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, str]]]:
        """Build ACLs for private-visibility objects using ownership, shares, roles, and territories."""
        if object_name not in self._handlers:
            logger.info("    No handler for %s - defaulting to public ACL", object_name)
            return {record["Id"]: self._public_acl() for record in records if record.get("Id")}

        record_ids = [str(record["Id"]) for record in records if record.get("Id")]
        if not record_ids:
            return {}

        logger.info("    Building private ACLs for %d %s records", len(record_ids), object_name)

        shares_by_record = await self._get_shares_by_record(object_name, record_ids)

        record_user_ids: dict[str, set[str]] = {}
        record_group_ids: dict[str, set[str]] = {}
        public_records: set[str] = set()
        union_user_ids: set[str] = set()
        union_group_ids: set[str] = set()

        for record in records:
            record_id = str(record["Id"])
            user_ids: set[str] = set()
            group_ids: set[str] = set()

            owner_id = record.get("OwnerId")
            if owner_id:
                user_ids.add(str(owner_id))
                
                # Add role hierarchy access: grant access to users in parent roles of the owner
                # This implements "Grant Access Using Hierarchies" for implicit upward sharing
                owner_role_id = self._get_owner_role_id(record)
                if owner_role_id:
                    parent_role_users = await self._get_parent_role_users(owner_role_id)
                    user_ids.update(parent_role_users)

            for share in shares_by_record.get(record_id, []):
                share_id = share.UserOrGroupId
                if not share_id:
                    continue
                if share.UserOrGroup and share.UserOrGroup.Type == UserOrGroupType.USER.value:
                    user_ids.add(share_id)
                else:
                    group_ids.add(share_id)

            expanded_users, includes_everyone = await self._expand_groups(group_ids)
            user_ids.update(expanded_users)

            if includes_everyone:
                public_records.add(record_id)

            # Territory-based ACL: Account and Opportunity can be governed by
            # Territory2 assignments.  Include all users from directly assigned
            # territories as well as every ancestor territory in the hierarchy.
            if object_name in ("Account", "Opportunity"):
                territory_user_ids = await self._get_territory_user_ids(record_id)
                user_ids.update(territory_user_ids)

            record_user_ids[record_id] = user_ids
            record_group_ids[record_id] = group_ids
            union_user_ids.update(user_ids)
            union_group_ids.update(group_ids)

        logger.info("    Authorizing %d users and %d groups", len(union_user_ids), len(union_group_ids))

        try:
            authorized_users_by_object, _ = await self._helper.get_authorized_users_and_groups_from_salesforce(
                list(union_user_ids),
                list(union_group_ids),
                self._handlers[object_name],
                EntityVisibility.NONE,
                await self._get_frozen_users(),
            )
        except Exception as auth_error:
            logger.error("    ❌ Failed to authorize users: %s", auth_error)
            raise
        
        users_by_id = authorized_users_by_object.get(object_name, {})
        logger.info("    Retrieved %d authorized users from Salesforce", len(users_by_id))

        # Bulk-resolve Salesforce users to AAD GUIDs via Graph $batch (20 per call).
        # This replaces hundreds of sequential GET /users/{id} calls with a few batch calls.
        self._bulk_warm_principal_cache(users_by_id)

        acl_map: dict[str, list[dict[str, str]]] = {}
        for record_id in record_ids:
            if record_id in public_records:
                acl_map[record_id] = self._public_acl()
                continue

            acl_entries = self._build_user_acls(users_by_id, record_user_ids.get(record_id, set()))
            acl_map[record_id] = acl_entries or self._deny_all_acl()

        return acl_map

    async def _get_shares_by_record(
        self,
        object_name: str,
        record_ids: list[str],
    ) -> dict[str, list[EntityShareBase]]:
        """Fetch sharing records from Salesforce, grouped by record ID."""
        filter_condition = self._build_in_filter("Id", record_ids)
        records_with_shares = await self._helper.get_records_with_shares(
            object_name,
            fetch_all=True,
            filter_condition=filter_condition,
            direction=RecordEnumerationDirection.ASCENDING,
        )

        shares_by_record: dict[str, list[EntityShareBase]] = {}
        for record in records_with_shares:
            share_records = record.Shares or {}
            shares_by_record[record.Id] = self._helper.response_processor.get(share_records, EntityShareBase)
        return shares_by_record

    async def _expand_groups(self, group_ids: set[str]) -> tuple[set[str], bool]:
        """Expand a set of group IDs into individual user IDs, detecting 'everyone' groups."""
        user_ids: set[str] = set()
        includes_everyone = False
        
        for group_id in group_ids:
            group_users, group_everyone = await self._resolve_group(group_id)
            user_ids.update(group_users)
            includes_everyone = includes_everyone or group_everyone
        return user_ids, includes_everyone

    async def _resolve_group(self, group_id: str) -> tuple[set[str], bool]:
        """Recursively resolve a Salesforce group to its member user IDs with caching.

        When pre-warmed data is available, uses in-memory lookups instead of
        per-group SOQL queries.
        """
        if group_id in self._group_cache:
            return self._group_cache[group_id]

        # ── Fast path: use pre-warmed data ────────────────────────────────
        if self._all_groups_by_id is not None:
            group = self._all_groups_by_id.get(group_id)
            if not group:
                self._group_cache[group_id] = (set(), False)
                return self._group_cache[group_id]

            group_type = (group.Type or "").strip()

            if group_type == UserOrGroupType.ORGANIZATION.value:
                self._group_cache[group_id] = (set(), True)
                return self._group_cache[group_id]

            if group_type == UserOrGroupType.ROLE.value and group.RelatedId:
                result = (self._get_role_users_from_cache(group.RelatedId, include_descendants=False), False)
                self._group_cache[group_id] = result
                return result

            if group_type in {
                UserOrGroupType.ROLE_AND_SUBORDINATES.value,
                UserOrGroupType.ROLE_AND_SUBORDINATES_INTERNAL.value,
            } and group.RelatedId:
                result = (self._get_role_users_from_cache(group.RelatedId, include_descendants=True), False)
                self._group_cache[group_id] = result
                return result

            if group_type == UserOrGroupType.MANAGER.value and group.RelatedId:
                result = (await self._resolve_manager_users(group.RelatedId, include_descendants=False), False)
                self._group_cache[group_id] = result
                return result

            if group_type == UserOrGroupType.MANAGER_AND_SUBORDINATES_INTERNAL.value and group.RelatedId:
                result = (await self._resolve_manager_users(group.RelatedId, include_descendants=True), False)
                self._group_cache[group_id] = result
                return result

            # Regular group: expand members from pre-warmed data
            member_ids = self._all_group_members_by_group.get(group_id, []) if self._all_group_members_by_group else []
            user_ids: set[str] = set()
            includes_everyone = False
            for member_id in member_ids:
                if not member_id or member_id == group_id:
                    continue
                if member_id.startswith(USER_ID_PREFIX):
                    user_ids.add(member_id)
                    continue
                nested_users, nested_everyone = await self._resolve_group(member_id)
                user_ids.update(nested_users)
                includes_everyone = includes_everyone or nested_everyone

            self._group_cache[group_id] = (user_ids, includes_everyone)
            return self._group_cache[group_id]

        # ── Fallback: per-group SOQL queries ──────────────────────────────
        groups = await self._helper.get_group_type_and_related_id(self._build_in_filter("Id", [group_id]))
        if not groups:
            self._group_cache[group_id] = (set(), False)
            return self._group_cache[group_id]

        group = groups[0]
        group_type = (group.Type or "").strip()

        if group_type == UserOrGroupType.ORGANIZATION.value:
            self._group_cache[group_id] = (set(), True)
            return self._group_cache[group_id]

        if group_type == UserOrGroupType.ROLE.value and group.RelatedId:
            result = (await self._resolve_role_users(group.RelatedId, include_descendants=False), False)
            self._group_cache[group_id] = result
            return result

        if group_type in {
            UserOrGroupType.ROLE_AND_SUBORDINATES.value,
            UserOrGroupType.ROLE_AND_SUBORDINATES_INTERNAL.value,
        } and group.RelatedId:
            result = (await self._resolve_role_users(group.RelatedId, include_descendants=True), False)
            self._group_cache[group_id] = result
            return result

        if group_type == UserOrGroupType.MANAGER.value and group.RelatedId:
            result = (await self._resolve_manager_users(group.RelatedId, include_descendants=False), False)
            self._group_cache[group_id] = result
            return result

        if group_type == UserOrGroupType.MANAGER_AND_SUBORDINATES_INTERNAL.value and group.RelatedId:
            result = (await self._resolve_manager_users(group.RelatedId, include_descendants=True), False)
            self._group_cache[group_id] = result
            return result

        members = await self._helper.get_group_members(self._build_in_filter("GroupId", [group_id]))
        user_ids: set[str] = set()
        includes_everyone = False

        for member in members:
            member_id = member.UserOrGroupId
            if not member_id or member_id == group_id:
                continue
            if member_id.startswith(USER_ID_PREFIX):
                user_ids.add(member_id)
                continue
            nested_users, nested_everyone = await self._resolve_group(member_id)
            user_ids.update(nested_users)
            includes_everyone = includes_everyone or nested_everyone

        self._group_cache[group_id] = (user_ids, includes_everyone)
        return self._group_cache[group_id]

    async def _resolve_role_users(self, role_id: str, include_descendants: bool) -> set[str]:
        """Return user IDs assigned to a role, optionally including descendant roles.

        Uses pre-warmed ``_users_by_role`` cache when available (zero SOQL).
        """
        if self._users_by_role is not None:
            return self._get_role_users_from_cache(role_id, include_descendants)

        role_ids = {role_id}
        if include_descendants:
            role_ids.update(await self._get_descendant_role_ids(role_id))

        users = await self._helper.get_users_from_salesforce(
            self._build_in_filter("UserRoleId", sorted(role_ids)),
            fetch_all=True,
        )
        return {user.Id for user in users if user.Id}

    def _get_role_users_from_cache(self, role_id: str, include_descendants: bool) -> set[str]:
        """Pure in-memory role→users lookup using pre-warmed caches."""
        role_ids = {role_id}
        if include_descendants and self._role_children_cache:
            frontier = list(self._role_children_cache.get(role_id, set()))
            while frontier:
                current = frontier.pop()
                if current in role_ids:
                    continue
                role_ids.add(current)
                frontier.extend(self._role_children_cache.get(current, set()))

        result: set[str] = set()
        if self._users_by_role:
            for rid in role_ids:
                result.update(self._users_by_role.get(rid, set()))
        return result

    async def _resolve_manager_users(self, manager_id: str, include_descendants: bool) -> set[str]:
        """Return user IDs in a manager's reporting chain, optionally including all subordinates."""
        users = await self._get_users_and_managers()
        reports_by_manager: dict[str, set[str]] = defaultdict(set)
        for user in users:
            if user.ManagerId and user.Id:
                reports_by_manager[user.ManagerId].add(user.Id)

        resolved = {manager_id}
        frontier = list(reports_by_manager.get(manager_id, set()))
        while frontier:
            current = frontier.pop()
            if current in resolved:
                continue
            resolved.add(current)
            if include_descendants:
                frontier.extend(reports_by_manager.get(current, set()))
        return resolved

    async def _get_descendant_role_ids(self, role_id: str) -> set[str]:
        """Return all descendant role IDs below the given role in the hierarchy."""
        if self._role_children_cache is None:
            roles = await self._helper.get_user_role_hierarchy_from_salesforce(fetch_all=True)
            children_by_parent: dict[str, set[str]] = defaultdict(set)
            for role in roles:
                if role.ParentRoleId and role.Id:
                    children_by_parent[role.ParentRoleId].add(role.Id)
            self._role_children_cache = dict(children_by_parent)

        descendants: set[str] = set()
        frontier = list(self._role_children_cache.get(role_id, set()))
        while frontier:
            current = frontier.pop()
            if current in descendants:
                continue
            descendants.add(current)
            frontier.extend(self._role_children_cache.get(current, set()))
        return descendants

    async def _get_parent_role_ids(self, role_id: str) -> set[str]:
        """Get all parent role IDs (upward in hierarchy) for implicit sharing.

        Uses pre-warmed ``_role_parent_map`` when available (zero SOQL).
        """
        parent_map = self._role_parent_map
        if parent_map is None:
            # Fallback: build from SOQL
            if self._role_children_cache is None:
                await self._get_descendant_role_ids("")  # Initialize cache
            roles = await self._helper.get_user_role_hierarchy_from_salesforce(fetch_all=True)
            parent_map = {}
            for role in roles:
                if role.Id and role.ParentRoleId:
                    parent_map[role.Id] = role.ParentRoleId
            self._role_parent_map = parent_map

        # Traverse upward to collect all parent roles
        parents: set[str] = set()
        current = role_id
        while current in parent_map:
            parent = parent_map[current]
            if parent in parents:
                break  # cycle guard
            parents.add(parent)
            current = parent

        return parents

    async def _get_parent_role_users(self, role_id: str) -> set[str]:
        """Get all users in parent roles for implicit upward sharing.

        Uses pre-warmed data when available (zero SOQL).
        """
        parent_role_ids = await self._get_parent_role_ids(role_id)

        if not parent_role_ids:
            return set()

        # Fast path: in-memory lookup
        if self._users_by_role is not None:
            result: set[str] = set()
            for rid in parent_role_ids:
                result.update(self._users_by_role.get(rid, set()))
            return result

        # Fallback: SOQL
        users = await self._helper.get_users_from_salesforce(
            self._build_in_filter("UserRoleId", sorted(parent_role_ids)),
            fetch_all=True,
        )
        return {user.Id for user in users if user.Id}

    # ── Territory-based ACL helpers ───────────────────────────────────────────

    async def _get_all_parent_territory_ids(self, territory_id: str) -> set[str]:
        """Walk the Territory2 hierarchy upward, collecting every ancestor ID.

        The loop terminates when there is no ParentTerritory2Id (root territory)
        or when a cycle is detected (safety guard).
        Results are cached in _territory_parent_cache to avoid redundant queries.
        """
        parent_ids: set[str] = set()
        visited: set[str] = {territory_id}
        current_id: str | None = territory_id

        while current_id:
            cached = current_id in self._territory_parent_cache
            if not cached:
                self._territory_parent_cache[current_id] = (
                    await self._helper.get_parent_territory_id(current_id)
                )
            parent_id = self._territory_parent_cache[current_id]
            cache_note = "(cached)" if cached else "(fetched)"
            logger.debug(
                "      Territory hierarchy: %s → parent: %s %s",
                current_id,
                parent_id or "(none – root)",
                cache_note,
            )
            if not parent_id or parent_id in visited:
                break
            parent_ids.add(parent_id)
            visited.add(parent_id)
            current_id = parent_id

        return parent_ids

    async def _get_territory_users_cached(self, territory_id: str) -> set[str]:
        """Return the set of Salesforce UserIds assigned to *one* territory.

        Results are cached in _territory_users_cache so repeated lookups for the
        same territory (across multiple records) hit memory instead of Salesforce.
        """
        if territory_id not in self._territory_users_cache:
            logger.debug("      Querying UserTerritory2Association for territory %s", territory_id)
            user_ids = await self._helper.get_users_for_territories([territory_id])
            self._territory_users_cache[territory_id] = set(user_ids)
            logger.debug("      → %d user(s) found for territory %s", len(user_ids), territory_id)
        else:
            logger.debug(
                "      Territory %s users served from cache (%d user(s))",
                territory_id,
                len(self._territory_users_cache[territory_id]),
            )
        return self._territory_users_cache[territory_id]

    async def _get_territory_user_ids(self, record_id: str) -> set[str]:
        """Resolve the full set of territory-based Salesforce UserIds for one record.

        When bulk pre-warmed data is available (the common path for full syncs),
        this method performs **zero SOQL queries** — all lookups are pure
        in-memory dict operations.
        """
        # ── Fast path: use pre-warmed data ────────────────────────────────
        if self._object_territory_assoc is not None:
            direct_territories = self._object_territory_assoc.get(record_id, [])
            if not direct_territories:
                return set()

            # Collect direct + all ancestor territories via in-memory parent walk
            all_territory_ids: set[str] = set(direct_territories)
            for t_id in direct_territories:
                current: str | None = t_id
                while current and self._all_territory_parents is not None:
                    parent = self._all_territory_parents.get(current)
                    if not parent or parent in all_territory_ids:
                        break
                    all_territory_ids.add(parent)
                    current = parent

            # Collect users from all territories
            all_user_ids: set[str] = set()
            if self._all_territory_users is not None:
                for t_id in all_territory_ids:
                    users = self._all_territory_users.get(t_id, set())
                    all_user_ids.update(users)

            logger.debug(
                "  [Territory ACL] Record %s: %d territories → %d users (pre-warmed)",
                record_id, len(all_territory_ids), len(all_user_ids),
            )
            return all_user_ids

        # ── Fallback: per-record SOQL queries (only if prewarm failed) ────
        logger.info("    ── Territory ACL: Step 1 – ObjectTerritory2Association ──────────")
        territory_ids = await self._helper.get_territory_ids_for_record(record_id)
        if not territory_ids:
            logger.info("    Step 1: No Territory2 assignments found for record %s", record_id)
            return set()

        logger.info(
            "    Step 1: Record %s → %d direct territory ID(s): %s",
            record_id,
            len(territory_ids),
            territory_ids,
        )

        # Step 2+3: Walk the Territory2 parent hierarchy upward for each direct territory
        logger.info("    ── Territory ACL: Step 2 – Territory2 parent hierarchy walk ─────")
        all_territory_ids: set[str] = set(territory_ids)
        for t_id in territory_ids:
            ancestor_ids = await self._get_all_parent_territory_ids(t_id)
            if ancestor_ids:
                logger.info(
                    "    Step 2: Territory %s → %d ancestor(s): %s",
                    t_id,
                    len(ancestor_ids),
                    sorted(ancestor_ids),
                )
            else:
                logger.info("    Step 2: Territory %s has no parent (root territory)", t_id)
            all_territory_ids.update(ancestor_ids)

        logger.info(
            "    Step 2: Total territories to check (direct + ancestors): %d → %s",
            len(all_territory_ids),
            sorted(all_territory_ids),
        )

        # Steps 4+5: Fetch users per territory (with per-territory caching)
        logger.debug("    ── Territory ACL: Steps 3-4 – UserTerritory2Association ──────────")
        all_user_ids: set[str] = set()
        for t_id in sorted(all_territory_ids):
            users = await self._get_territory_users_cached(t_id)
            logger.debug(
                "    Step 3/4: Territory %s → %d user(s)",
                t_id,
                len(users),
            )
            all_user_ids.update(users)

        logger.debug(
            "    [Territory ACL] Record %s → %d unique territory user(s)",
            record_id,
            len(all_user_ids),
        )
        return all_user_ids

    @staticmethod
    def _get_owner_role_id(record: dict[str, Any]) -> str | None:
        """Extract the owner's UserRole ID from a Salesforce record, if present."""
        owner = record.get("Owner")
        if isinstance(owner, dict):
            user_role = owner.get("UserRole")
            if isinstance(user_role, dict):
                return user_role.get("Id")
        return None

    async def _get_users_and_managers(self) -> list[User]:
        """Fetch and cache all Salesforce users with their manager relationships."""
        if self._users_and_managers is None:
            self._users_and_managers = await self._helper.get_users_and_managers()
        return self._users_and_managers

    async def _get_frozen_users(self) -> set[str]:
        """Fetch and cache the set of frozen Salesforce user IDs."""
        if self._frozen_users is None:
            self._frozen_users = {
                user_login.UserId
                for user_login in await self._helper.get_frozen_users()
                if user_login.UserId
            }
        return self._frozen_users

    def _bulk_warm_principal_cache(self, users_by_id: dict[str, User]) -> None:
        """Pre-resolve user identifiers to AAD GUIDs via Graph $batch.

        Collects every un-cached identifier (FederationIdentifier, UserName,
        Email) across *users_by_id*, then resolves them in batches of 20 using
        ``POST /$batch`` with ``GET /users/{id}?$select=id`` requests.

        Only **successes** are cached so that individual fallback (filter query)
        still runs for 404s — some identifiers only resolve via the filter path.
        """
        if not self._graph_client:
            return

        to_resolve: list[str] = []
        for user in users_by_id.values():
            if not user or user.IsFrozen:
                continue
            for raw in (user.FederationIdentifier, user.UserName, user.Email):
                if not raw:
                    continue
                ident = raw.strip()
                if ident and ident not in self._principal_id_cache and not self._looks_like_guid(ident):
                    to_resolve.append(ident)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for ident in to_resolve:
            if ident not in seen:
                seen.add(ident)
                unique.append(ident)

        if not unique:
            return

        logger.info("    Bulk Graph lookup: %d unique identifiers", len(unique))
        resolved = 0

        from graph.client import GRAPH_BATCH_MAX_SIZE
        for i in range(0, len(unique), GRAPH_BATCH_MAX_SIZE):
            batch = unique[i : i + GRAPH_BATCH_MAX_SIZE]
            requests_payload = [
                {
                    "id": str(idx),
                    "method": "GET",
                    "url": f"/users/{quote(ident, safe='')}?$select=id",
                }
                for idx, ident in enumerate(batch)
            ]
            try:
                responses = self._graph_client.batch_requests(requests_payload)
                for resp in responses:
                    idx = int(resp.get("id", -1))
                    if not (0 <= idx < len(batch)):
                        continue
                    status = resp.get("status", 0)
                    body = resp.get("body", {})
                    if 200 <= status < 300 and isinstance(body, dict) and body.get("id"):
                        self._principal_id_cache[batch[idx]] = str(body["id"])
                        resolved += 1
                    # Don't cache failures — let individual lookup try the filter path
            except Exception as exc:
                logger.debug("    Bulk Graph batch failed: %s — falling back to individual lookups", exc)

        logger.info("    Bulk Graph lookup: resolved %d / %d identifiers", resolved, len(unique))

    def _build_user_acls(
        self,
        users_by_id: dict[str, User],
        user_ids: set[str],
    ) -> list[dict[str, str]]:
        """Convert a set of Salesforce user IDs into deduplicated Graph ACL grant entries."""
        acl_entries: list[dict[str, str]] = []
        seen_values: set[str] = set()

        for user_id in sorted(user_ids):
            user = users_by_id.get(user_id)
            
            if not user or user.IsFrozen:
                continue
            
            principal = self._resolve_user_guid(user)
            
            if not principal:
                logger.warning("    ✗ User %s (%s): No M365 account found", user_id, user.Email or user.UserName or "no-identifier")
                continue
            
            normalized = principal.lower()
            if normalized in seen_values:
                continue
            seen_values.add(normalized)
            acl_entries.append(
                {
                    "accessType": "grant",
                    "type": "user",
                    "value": principal,
                }
            )
        
        return acl_entries

    @staticmethod
    def _get_user_principal(user: User) -> str | None:
        """Return the first non-empty identifier from FederationIdentifier, UserName, or Email."""
        for candidate in (user.FederationIdentifier, user.UserName, user.Email):
            if candidate:
                return candidate.strip()
        return None

    def _resolve_user_guid(self, user: User) -> str | None:
        """Resolve a Salesforce user to a Microsoft Graph user GUID, trying all identity fields."""
        resolved = self._resolve_principal_guid(user.FederationIdentifier)
        if resolved:
            return resolved

        resolved = self._resolve_principal_guid(user.UserName)
        if resolved:
            return resolved

        resolved = self._resolve_principal_guid(user.Email)
        if resolved:
            return resolved

        return None

    def _resolve_principal_guid(self, identifier: str | None) -> str | None:
        """Resolve an identifier (UPN, email, or GUID) to a Microsoft Graph user ID with caching."""
        if not identifier:
            return None

        normalized = identifier.strip()
        if not normalized:
            return None

        # Check cache
        cached = self._principal_id_cache.get(normalized)
        if cached is not None or normalized in self._principal_id_cache:
            return cached

        # Check if already a GUID
        if self._looks_like_guid(normalized):
            self._principal_id_cache[normalized] = normalized
            return normalized

        # Need Graph client to look up
        if self._graph_client is None:
            self._principal_id_cache[normalized] = None
            return None

        # Lookup via Graph API
        graph_id = self._lookup_graph_user_id(normalized)
        self._principal_id_cache[normalized] = graph_id
        return graph_id

    def _lookup_graph_user_id(self, identifier: str) -> str | None:
        """Look up a user's Graph ID by direct path or filter query on UPN/mail."""
        direct_path = f"/users/{quote(identifier, safe='')}?$select=id"
        try:
            payload = self._graph_client.get(direct_path)
            if isinstance(payload, dict) and payload.get("id"):
                return str(payload["id"])
        except GraphApiError as error:
            if error.status_code not in (400, 403, 404):
                raise
        except Exception:
            return None

        escaped_identifier = identifier.replace("'", "''")
        filter_path = (
            f"/users?$select=id&$top=1&$filter="
            f"userPrincipalName eq '{escaped_identifier}' or mail eq '{escaped_identifier}'"
        )
        try:
            payload = self._graph_client.get(filter_path)
            values = payload.get("value", []) if isinstance(payload, dict) else []
            if values and isinstance(values[0], dict) and values[0].get("id"):
                return str(values[0]["id"])
            else:
                return None
        except GraphApiError as error:
            if error.status_code not in (400, 403, 404):
                raise
        except Exception:
            return None

        return None

    @staticmethod
    def _looks_like_guid(value: str) -> bool:
        """Return True if the value matches the 8-4-4-4-12 hex GUID format."""
        cleaned = value.strip()
        parts = cleaned.split("-")
        if len(parts) != 5:
            return False
        expected_lengths = (8, 4, 4, 4, 12)
        for part, expected_length in zip(parts, expected_lengths, strict=True):
            if len(part) != expected_length:
                return False
            if not all(char in "0123456789abcdefABCDEF" for char in part):
                return False
        return True

    def _public_acl(self) -> list[dict[str, str]]:
        """Return an ACL list granting access to everyone in the tenant."""
        return [
            {
                "accessType": "grant",
                "type": "everyone",
                "value": self._tenant_id,
            }
        ]

    def _deny_all_acl(self) -> list[dict[str, str]]:
        """Return an ACL list denying access to everyone (used when no users are resolved)."""
        return [
            {
                "accessType": "deny",
                "type": "everyone",
                "value": self._tenant_id,
            }
        ]

    def _sort_object_names(self, object_names: Any) -> list[str]:
        """Sort object names so parent objects are processed before their dependents."""
        ordered_names = [str(name) for name in object_names]
        object_name_set = set(ordered_names)
        dependency_depths: dict[str, int] = {}
        visiting: set[str] = set()

        def _dependency_depth(name: str) -> int:
            """Compute the parent-chain depth for topological sorting."""
            if name in dependency_depths:
                return dependency_depths[name]

            if name in visiting:
                return 0

            visiting.add(name)
            handler = self._handlers.get(name)
            parent_object_name = getattr(handler, "parent_object_name", None) if handler else None
            depth = 0
            if parent_object_name and parent_object_name in object_name_set:
                depth = _dependency_depth(parent_object_name) + 1
            visiting.remove(name)
            dependency_depths[name] = depth
            return depth

        return sorted(
            ordered_names,
            key=lambda name: (_dependency_depth(name), name),
        )

    @staticmethod
    def _build_in_filter(field_name: str, values: list[str]) -> str:
        """Build a SOQL IN filter clause from a list of values."""
        quoted_values = ", ".join(f"'{value}'" for value in sorted(set(values)) if value)
        return f"{field_name} in ({quoted_values})" if quoted_values else ""

    @staticmethod
    def _is_public_visibility(visibility: EntityVisibility | str) -> bool:
        """Return True if the OWD visibility grants public read access."""
        value = visibility.value if isinstance(visibility, EntityVisibility) else str(visibility)
        return value in {
            EntityVisibility.PUBLIC_READ_ONLY.value,
            EntityVisibility.PUBLIC_READ_WRITE.value,
            EntityVisibility.PUBLIC_READ_WRITE_TRANSFER.value,
            EntityVisibility.ALL.value,
        }

    @staticmethod
    def _is_controlled_by_parent(visibility: EntityVisibility | str) -> bool:
        """Return True if the OWD visibility is ControlledByParent."""
        value = visibility.value if isinstance(visibility, EntityVisibility) else str(visibility)
        return value == EntityVisibility.CONTROLLED_BY_PARENT.value