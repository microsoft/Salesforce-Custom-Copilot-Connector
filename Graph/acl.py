from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote
import asyncio
import logging
import os

from Graph.graph import GraphApiError, GraphClient
from Salesforce.identity_sync import (
    AsyncSalesforceClient,
    ClientHelperForIdentitySync,
    EntityShareBase,
    EntityVisibility,
    GroupMember,
    RecordEnumerationDirection,
    User,
    UserOrGroupType,
)
from Item.item_converter import SalesforceObjectHandler
from Salesforce.salesforce import get_salesforce_access_token
from Salesforce.settings import AppConfig


USER_ID_PREFIX = "005"
logger = logging.getLogger("salesforce_connector")


class AclResolver:
    def __init__(
        self,
        config: AppConfig,
        handlers: dict[str, SalesforceObjectHandler],
        graph_client: GraphClient | None = None,
    ):
        self._config = config
        self._handlers = handlers
        self._graph_client = graph_client
        self._tenant_id = os.getenv("AZURE_TENANT_ID") or "everyone"
        self._helper = ClientHelperForIdentitySync(
            AsyncSalesforceClient(
                config.connector.salesforce.instance_url,
                config.connector.salesforce.api_version,
            ),
            config.connector.salesforce.instance_url,
            get_salesforce_access_token(config),
        )
        self._group_cache: dict[str, tuple[set[str], bool]] = {}
        self._principal_id_cache: dict[str, str | None] = {}
        self._role_children_cache: dict[str, set[str]] | None = None
        self._users_and_managers: list[User] | None = None
        self._frozen_users: set[str] | None = None
        # territory ACL caches
        self._territory_parent_cache: dict[str, str | None] = {}
        self._territory_users_cache: dict[str, set[str]] = {}

    def resolve(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        if not records_by_object_type:
            return {}
        return asyncio.run(self._resolve_async(records_by_object_type))

    async def _resolve_async(
        self,
        records_by_object_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        logger.info("\n" + "=" * 80)
        logger.info("ACL RESOLUTION START")
        logger.info("=" * 80)
        
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
            
            logger.info("\n" + "-" * 80)
            logger.info("Processing object: %s (%d records)", object_name, len(records))
            logger.info("-" * 80)
            
            acl_maps[object_name] = await self._build_acl_map_for_object(
                object_name,
                records,
                visibility_map,
                acl_maps,
            )

        logger.info("\n" + "=" * 80)
        logger.info("ACL RESOLUTION COMPLETE")
        logger.info("=" * 80 + "\n")
        
        return acl_maps

    async def _build_acl_map_for_object(
        self,
        object_name: str,
        records: list[dict[str, Any]],
        visibility_map: dict[str, EntityVisibility],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
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
                logger.info(
                    "  [Territory ACL] Resolving territory-based access for %s record %s",
                    object_name,
                    record_id,
                )
                territory_user_ids = await self._get_territory_user_ids(record_id)
                if territory_user_ids:
                    logger.info(
                        "  [Territory ACL] Adding %d territory user(s) to ACL for record %s",
                        len(territory_user_ids),
                        record_id,
                    )
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
        user_ids: set[str] = set()
        includes_everyone = False
        
        for group_id in group_ids:
            group_users, group_everyone = await self._resolve_group(group_id)
            user_ids.update(group_users)
            includes_everyone = includes_everyone or group_everyone
        return user_ids, includes_everyone

    async def _resolve_group(self, group_id: str) -> tuple[set[str], bool]:
        if group_id in self._group_cache:
            return self._group_cache[group_id]

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
        role_ids = {role_id}
        if include_descendants:
            role_ids.update(await self._get_descendant_role_ids(role_id))

        users = await self._helper.get_users_from_salesforce(
            self._build_in_filter("UserRoleId", sorted(role_ids)),
            fetch_all=True,
        )
        return {user.Id for user in users if user.Id}

    async def _resolve_manager_users(self, manager_id: str, include_descendants: bool) -> set[str]:
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
        """Get all parent role IDs (upward in hierarchy) for implicit sharing"""
        if self._role_children_cache is None:
            await self._get_descendant_role_ids("")  # Initialize cache
        
        # Build parent map from children cache
        parent_map: dict[str, str] = {}
        roles = await self._helper.get_user_role_hierarchy_from_salesforce(fetch_all=True)
        for role in roles:
            if role.Id and role.ParentRoleId:
                parent_map[role.Id] = role.ParentRoleId
        
        # Traverse upward to collect all parent roles
        parents: set[str] = set()
        current = role_id
        while current in parent_map:
            parent = parent_map[current]
            parents.add(parent)
            current = parent
        
        return parents

    async def _get_parent_role_users(self, role_id: str) -> set[str]:
        """Get all users in parent roles for implicit upward sharing"""
        parent_role_ids = await self._get_parent_role_ids(role_id)
        
        if not parent_role_ids:
            return set()
        
        users = await self._helper.get_users_from_salesforce(
            self._build_in_filter("UserRoleId", sorted(parent_role_ids)),
            fetch_all=True,
        )
        user_ids = {user.Id for user in users if user.Id}
        return user_ids

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
            logger.info(
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
            logger.info("      Querying UserTerritory2Association for territory %s", territory_id)
            user_ids = await self._helper.get_users_for_territories([territory_id])
            self._territory_users_cache[territory_id] = set(user_ids)
            logger.info("      → %d user(s) found for territory %s", len(user_ids), territory_id)
        else:
            logger.info(
                "      Territory %s users served from cache (%d user(s))",
                territory_id,
                len(self._territory_users_cache[territory_id]),
            )
        return self._territory_users_cache[territory_id]

    async def _get_territory_user_ids(self, record_id: str) -> set[str]:
        """Resolve the full set of territory-based Salesforce UserIds for one record.

        ACL resolution flow (per the spec):
          Step 1 – ObjectTerritory2Association   → direct Territory2Ids for this record
          Step 2 – Territory2.ParentTerritory2Id → recurse upward until root
          Step 3 – UserTerritory2Association     → UserIds for every territory collected
          Step 5 – return the union (callers merge with owner / share-based sets)
        """
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
        logger.info("    ── Territory ACL: Steps 3-4 – UserTerritory2Association ──────────")
        all_user_ids: set[str] = set()
        for t_id in sorted(all_territory_ids):
            users = await self._get_territory_users_cached(t_id)
            logger.info(
                "    Step 3/4: Territory %s → %d user(s): %s",
                t_id,
                len(users),
                sorted(users) if users else "(none)",
            )
            all_user_ids.update(users)

        logger.info("    ── Territory ACL: Step 5 – Final Effective ACL ──────────────────")
        logger.info(
            "    Step 5: Record %s → territory ACL union: %d unique user(s): %s",
            record_id,
            len(all_user_ids),
            sorted(all_user_ids) if all_user_ids else "(none)",
        )
        return all_user_ids

    @staticmethod
    def _get_owner_role_id(record: dict[str, Any]) -> str | None:
        owner = record.get("Owner")
        if isinstance(owner, dict):
            user_role = owner.get("UserRole")
            if isinstance(user_role, dict):
                return user_role.get("Id")
        return None

    async def _get_users_and_managers(self) -> list[User]:
        if self._users_and_managers is None:
            self._users_and_managers = await self._helper.get_users_and_managers()
        return self._users_and_managers

    async def _get_frozen_users(self) -> set[str]:
        if self._frozen_users is None:
            self._frozen_users = {
                user_login.UserId
                for user_login in await self._helper.get_frozen_users()
                if user_login.UserId
            }
        return self._frozen_users

    def _build_user_acls(
        self,
        users_by_id: dict[str, User],
        user_ids: set[str],
    ) -> list[dict[str, str]]:
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
        for candidate in (user.FederationIdentifier, user.UserName, user.Email):
            if candidate:
                return candidate.strip()
        return None

    def _resolve_user_guid(self, user: User) -> str | None:
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
        return [
            {
                "accessType": "grant",
                "type": "everyone",
                "value": self._tenant_id,
            }
        ]

    def _deny_all_acl(self) -> list[dict[str, str]]:
        return [
            {
                "accessType": "deny",
                "type": "everyone",
                "value": self._tenant_id,
            }
        ]

    def _sort_object_names(self, object_names: Any) -> list[str]:
        ordered_names = [str(name) for name in object_names]
        object_name_set = set(ordered_names)
        dependency_depths: dict[str, int] = {}
        visiting: set[str] = set()

        def _dependency_depth(name: str) -> int:
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
        quoted_values = ", ".join(f"'{value}'" for value in sorted(set(values)) if value)
        return f"{field_name} in ({quoted_values})" if quoted_values else ""

    @staticmethod
    def _is_public_visibility(visibility: EntityVisibility | str) -> bool:
        value = visibility.value if isinstance(visibility, EntityVisibility) else str(visibility)
        return value in {
            EntityVisibility.PUBLIC_READ_ONLY.value,
            EntityVisibility.PUBLIC_READ_WRITE.value,
            EntityVisibility.PUBLIC_READ_WRITE_TRANSFER.value,
            EntityVisibility.ALL.value,
        }

    @staticmethod
    def _is_controlled_by_parent(visibility: EntityVisibility | str) -> bool:
        value = visibility.value if isinstance(visibility, EntityVisibility) else str(visibility)
        return value == EntityVisibility.CONTROLLED_BY_PARENT.value