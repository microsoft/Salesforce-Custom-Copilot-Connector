from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote
import asyncio
import os

from connector.graph import GraphApiError, GraphClient
from connector.identity_sync import (
    AsyncSalesforceClient,
    ClientHelperForIdentitySync,
    EntityShareBase,
    EntityVisibility,
    GroupMember,
    RecordEnumerationDirection,
    SalesforceConstants,
    User,
    UserOrGroupType,
)
from connector.item_converter import SalesforceObjectHandler
from connector.salesforce import get_salesforce_access_token
from connector.settings import AppConfig


USER_ID_PREFIX = "005"


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
        acl_maps: dict[str, dict[str, list[dict[str, str]]]] = {}
        visibility_map = await self._helper.get_org_wide_defaults_map()

        object_order = self._sort_object_names(records_by_object_type.keys())
        for object_name in object_order:
            records = records_by_object_type.get(object_name, [])
            if not records:
                continue
            acl_maps[object_name] = await self._build_acl_map_for_object(
                object_name,
                records,
                visibility_map,
                acl_maps,
            )

        return acl_maps

    async def _build_acl_map_for_object(
        self,
        object_name: str,
        records: list[dict[str, Any]],
        visibility_map: dict[str, EntityVisibility],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
        visibility = visibility_map.get(object_name, EntityVisibility.PUBLIC_READ_ONLY)

        if self._is_public_visibility(visibility):
            return {record["Id"]: self._public_acl() for record in records if record.get("Id")}

        if self._is_controlled_by_parent(visibility):
            return await self._build_parent_controlled_acl_map(object_name, records, acl_maps)

        return await self._build_private_acl_map(object_name, records)

    async def _build_parent_controlled_acl_map(
        self,
        object_name: str,
        records: list[dict[str, Any]],
        acl_maps: dict[str, dict[str, list[dict[str, str]]]],
    ) -> dict[str, list[dict[str, str]]]:
        account_acl_map = acl_maps.get(SalesforceConstants.ACCOUNT, {})
        resolved: dict[str, list[dict[str, str]]] = {}
        unresolved_records: list[dict[str, Any]] = []

        for record in records:
            record_id = record.get("Id")
            account_id = record.get("AccountId")
            if record_id and account_id and account_id in account_acl_map:
                resolved[record_id] = account_acl_map[account_id]
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
            return {record["Id"]: self._public_acl() for record in records if record.get("Id")}

        record_ids = [str(record["Id"]) for record in records if record.get("Id")]
        if not record_ids:
            return {}

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

            record_user_ids[record_id] = user_ids
            record_group_ids[record_id] = group_ids
            union_user_ids.update(user_ids)
            union_group_ids.update(group_ids)

        authorized_users_by_object, _ = await self._helper.get_authorized_users_and_groups_from_salesforce(
            list(union_user_ids),
            list(union_group_ids),
            self._handlers[object_name],
            EntityVisibility.NONE,
            await self._get_frozen_users(),
        )
        users_by_id = authorized_users_by_object.get(object_name, {})

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
        for candidate in (user.FederationIdentifier, user.UserName, user.Email):
            resolved = self._resolve_principal_guid(candidate)
            if resolved:
                return resolved
        return None

    def _resolve_principal_guid(self, identifier: str | None) -> str | None:
        if not identifier:
            return None

        normalized = identifier.strip()
        if not normalized:
            return None

        cached = self._principal_id_cache.get(normalized)
        if cached is not None or normalized in self._principal_id_cache:
            return cached

        if self._looks_like_guid(normalized):
            self._principal_id_cache[normalized] = normalized
            return normalized

        if self._graph_client is None:
            self._principal_id_cache[normalized] = None
            return None

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
            "/users?$select=id&$top=1&$filter="
            f"userPrincipalName eq '{escaped_identifier}' or mail eq '{escaped_identifier}'"
        )
        try:
            payload = self._graph_client.get(filter_path)
            values = payload.get("value", []) if isinstance(payload, dict) else []
            if values and isinstance(values[0], dict) and values[0].get("id"):
                return str(values[0]["id"])
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

    @staticmethod
    def _sort_object_names(object_names: Any) -> list[str]:
        priority = {
            SalesforceConstants.ACCOUNT: 0,
            SalesforceConstants.CONTACT: 1,
            SalesforceConstants.OPPORTUNITY: 2,
            SalesforceConstants.LEAD: 3,
            SalesforceConstants.CASE: 4,
        }
        return sorted(object_names, key=lambda name: priority.get(name, 100))

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