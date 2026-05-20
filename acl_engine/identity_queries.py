"""
acl_engine/identity_queries.py
------------------------------
SOQL query methods used by the Identity Crawl and Group-based ACL builder.

Provides a ``IdentityQueryClient`` that wraps an existing ``SalesforceClient``
and adds high-level async methods for all SOQL queries defined in the
Identity Crawl specification (temp.md Sections 3.1–3.12).

Each method corresponds to one SOQL query pattern and returns parsed
``identity_models`` dataclass instances.
"""
from __future__ import annotations

import logging
from typing import Any

from acl_engine.salesforce_client import SalesforceClient
from acl_engine.identity_models import (
    EntityShare,
    EntityVisibility,
    SfGroup,
    SfUser,
    UserOrGroupType,
    UserRole,
    parse_visibility,
)

logger = logging.getLogger("salesforce_connector.acl_engine.identity")

# Salesforce limits the number of IDs in an IN clause
_MAX_IN_CLAUSE_IDS = 50

# ── EntityDefinition.InternalSharingModel → EntityVisibility mapping ─────────
# EntityDefinition returns different string literals than the Organization table.
# This dict normalises them to the EntityVisibility values the rest of the
# group-ACL engine already understands.
_ENTITY_DEF_TO_VISIBILITY: dict[str, EntityVisibility] = {
    "Private":                    EntityVisibility.NONE,
    "Read":                       EntityVisibility.READ,
    "ReadSelect":                 EntityVisibility.READ,
    "ReadWrite":                  EntityVisibility.EDIT,
    "ReadWriteTransfer":          EntityVisibility.READ_EDIT_TRANSFER,
    "FullAccess":                 EntityVisibility.READ_EDIT_TRANSFER,
    "ControlledByParent":         EntityVisibility.CONTROLLED_BY_PARENT,
    "ControlledByCampaign":       EntityVisibility.CONTROLLED_BY_CAMPAIGN,
    "ControlledByLeadOrContact":  EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
}


def _share_table_name(object_type: str) -> str:
    """Derive the share table API name for a given sObject type.

    Standard objects : Account   → AccountShare
    Custom objects   : Work_Order__c → Work_Order__Share
    """
    if object_type.endswith("__c"):
        return object_type[:-3] + "__Share"
    return object_type + "Share"


def _chunked(items: list, size: int) -> list[list]:
    """Split *items* into chunks of at most *size*."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _quote_ids(ids: list[str]) -> str:
    """Format a list of IDs for a SOQL IN clause."""
    return ", ".join(f"'{i}'" for i in ids)


class IdentityQueryClient:
    """
    High-level query methods for identity crawl and group-based ACL.

    Parameters
    ----------
    sf_client    : An authenticated ``SalesforceClient`` instance.
    owd_field_map : ``{object_name: owd_field}`` from config (e.g.
                    ``{"Account": "DefaultAccountAccess"}``).  When provided,
                    the OWD query is built dynamically from this map.  When
                    omitted, falls back to loading from ``config/schema.json``.
    batch_size   : Max IDs per SOQL IN clause (default 50, Salesforce limit).
    use_entity_definition_owd : When True, query ``EntityDefinition`` for OWD
                    values before falling back to the Organization table.
    object_names : All object names from schema.json (used for the
                    EntityDefinition query).  When omitted, loaded from config.
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        owd_field_map: dict[str, str] | None = None,
        batch_size: int = _MAX_IN_CLAUSE_IDS,
        use_entity_definition_owd: bool = False,
        object_names: list[str] | None = None,
    ) -> None:
        self._sf = sf_client
        self._batch_size = batch_size
        self._owd_field_map = owd_field_map if owd_field_map is not None else self._load_owd_field_map()
        self._no_share_table_types: set[str] = set()
        self._invalid_owd_fields: set[str] = set()
        # ── EntityDefinition flight ──────────────────────────────────────────
        self._use_entity_definition_owd = use_entity_definition_owd
        self._object_names: list[str] = (
            list(object_names) if object_names is not None
            else self._load_object_names()
        ) if use_entity_definition_owd else []
        # ── End EntityDefinition flight ──────────────────────────────────────

    @staticmethod
    def _load_owd_field_map() -> dict[str, str]:
        """Fallback: load owd_field_map from config/schema.json."""
        try:
            from salesforce.settings import build_owd_field_map
            return build_owd_field_map()
        except Exception:
            logger.warning("[IdentityQuery] Could not load owd_field_map from config")
            return {}

    @staticmethod
    def _load_object_names() -> list[str]:
        """Fallback: load all object names from config/schema.json."""
        try:
            from salesforce.settings import build_object_name_list
            return build_object_name_list()
        except Exception:
            logger.warning("[IdentityQuery] Could not load object names from config")
            return []

    # ── 3.1  Org-Wide Defaults ───────────────────────────────────────────────

    async def get_org_wide_defaults(self) -> dict[str, EntityVisibility]:
        """
        Query OWD settings for all configured objects.

        When ``use_entity_definition_owd`` is enabled:
          1. Queries ``EntityDefinition`` for all objects in schema.json.
          2. Maps ``InternalSharingModel`` to ``EntityVisibility``.
          3. Falls back to the Organization-table approach for objects not
             found in the EntityDefinition result set.

        When disabled: behaves exactly as before (Organization table query).

        Returns ``{object_name: EntityVisibility}``.
        """
        owd_map: dict[str, EntityVisibility] = {}

        # ── NEW PATH: EntityDefinition ────────────────────────────────────────
        entity_def_resolved: set[str] = set()
        if self._use_entity_definition_owd and self._object_names:
            entity_def_map = await self._fetch_entity_def_owd()
            owd_map.update(entity_def_map)
            entity_def_resolved = set(entity_def_map.keys())
            logger.info(
                "[IdentityQuery] EntityDefinition resolved %d object(s): %s",
                len(entity_def_resolved),
                {k: v.value for k, v in entity_def_map.items()},
            )
        # ── END NEW PATH ──────────────────────────────────────────────────────

        # ── OLD PATH (fallback): Organization table ───────────────────────────
        # Only query for objects NOT already resolved by EntityDefinition
        remaining_map = {
            obj: fld for obj, fld in self._owd_field_map.items()
            if obj not in entity_def_resolved
        }
        if remaining_map:
            org_owd = await self._fetch_org_table_owd(remaining_map)
            owd_map.update(org_owd)
        # ── END OLD PATH ──────────────────────────────────────────────────────

        logger.info("[IdentityQuery] OWD map: %s", {k: v.value for k, v in owd_map.items()})
        return owd_map

    async def _fetch_entity_def_owd(self) -> dict[str, EntityVisibility]:
        """
        Query ``EntityDefinition`` for all objects in schema.json and return
        ``{objectName: EntityVisibility}``.

        Fires:
            SELECT QualifiedApiName, InternalSharingModel
            FROM EntityDefinition
            WHERE QualifiedApiName IN ('Account', 'Contact', …)

        Unknown ``InternalSharingModel`` values default to ``NONE`` (Private).
        """
        quoted = ", ".join(f"'{name}'" for name in self._object_names)
        soql = (
            "SELECT QualifiedApiName, InternalSharingModel "
            "FROM EntityDefinition "
            f"WHERE QualifiedApiName IN ({quoted})"
        )
        logger.info("[IdentityQuery] Fetching OWD via EntityDefinition: %s", soql)

        try:
            result = await self._sf.query(soql, tooling=True)
            records = result.get("records", [])
        except Exception as exc:
            logger.warning(
                "[IdentityQuery] EntityDefinition query failed (%s); "
                "will fall back to Organization query",
                exc,
            )
            return {}

        owd_map: dict[str, EntityVisibility] = {}
        for row in records:
            api_name = row.get("QualifiedApiName")
            raw_model = row.get("InternalSharingModel")
            if not api_name:
                continue
            vis = _ENTITY_DEF_TO_VISIBILITY.get(
                raw_model or "", EntityVisibility.NONE
            )
            owd_map[api_name] = vis
            logger.info(
                "[IdentityQuery] EntityDefinition: %s → InternalSharingModel='%s' → OWD='%s'",
                api_name, raw_model, vis.value,
            )

        # Log objects that were queried but not returned by EntityDefinition
        missing = set(self._object_names) - set(owd_map.keys())
        if missing:
            logger.warning("[IdentityQuery] EntityDefinition returned no rows for: %s", missing)

        logger.info(
            "[IdentityQuery] EntityDefinition cache: %d/%d object(s)",
            len(owd_map), len(self._object_names),
        )
        return owd_map

    async def _fetch_org_table_owd(
        self, field_map: dict[str, str],
    ) -> dict[str, EntityVisibility]:
        """
        Query OWD from the Organization table for the given field map.

        This is the original Organization-table implementation, extracted into
        its own method so it can serve as a fallback when the EntityDefinition
        path is enabled.
        """
        if not field_map:
            return {}

        # Validate fields against Organization describe to avoid INVALID_FIELD
        valid_fields = await self._get_org_fields()
        filtered_map = {
            obj: fld for obj, fld in field_map.items()
            if fld in valid_fields
        }
        skipped = set(field_map) - set(filtered_map)
        if skipped:
            self._invalid_owd_fields = {field_map[obj] for obj in skipped}
            logger.warning(
                "[IdentityQuery] Skipping objects with invalid OWD fields on Organization: %s",
                {obj: field_map[obj] for obj in skipped},
            )

        if not filtered_map:
            logger.warning("[IdentityQuery] No valid OWD fields found; returning empty OWD")
            return {}

        # Build SELECT from validated fields: deduplicate field names
        owd_fields = list(dict.fromkeys(filtered_map.values()))
        soql = f"SELECT {', '.join(owd_fields)} FROM Organization"

        logger.info("[IdentityQuery] Fetching OWD with: %s", soql)

        result = await self._sf.query(soql)
        records = result.get("records", [])
        if not records:
            logger.warning("[IdentityQuery] No Organization record returned")
            return {}

        record = records[0]
        owd_map: dict[str, EntityVisibility] = {}
        for obj_name, owd_field in filtered_map.items():
            raw = record.get(owd_field, "None")
            owd_map[obj_name] = parse_visibility(raw)

        return owd_map

    async def _get_org_fields(self) -> set[str]:
        """Return the set of field names available on the Organization entity."""
        try:
            desc = await self._sf.describe_sobject("Organization")
            return {f["name"] for f in desc.get("fields", [])}
        except Exception as exc:
            logger.warning("[IdentityQuery] Organization describe failed: %s", exc)
            # Fall back to all configured fields (query may fail for bad ones)
            return set(self._owd_field_map.values())

    # ── 3.2  Authorized Users (all with Read permission) ─────────────────────

    async def get_authorized_users(self, object_name: str) -> list[SfUser]:
        """
        Query all active users who have Read permission on *object_name*
        via PermissionSetAssignment.  Used for PUBLIC OWD objects.
        """
        soql = (
            "SELECT Id, Assignee.Name, Assignee.Id, Assignee.Alias, "
            "Assignee.Email, Assignee.FirstName, Assignee.LastName, "
            "Assignee.FederationIdentifier, Assignee.UserName, "
            "Assignee.IsActive, Assignee.UserRoleId, "
            "PermissionSet.Id, PermissionSet.IsOwnedByProfile, "
            "PermissionSet.Profile.Name, PermissionSet.Label "
            "FROM PermissionSetAssignment "
            "WHERE PermissionSetId IN ("
            "  SELECT ParentId FROM ObjectPermissions "
            f"  WHERE SObjectType = '{object_name}' AND PermissionsRead = true"
            ") "
            "AND Assignee.IsActive = True "
            "AND Assignee.UserType = 'Standard' "
            "AND (NOT Assignee.Name LIKE '%User%') "
            "ORDER BY Id ASC"
        )
        logger.info("[IdentityQuery] get_users_with_roles — hitting Salesforce User endpoint for '%s'", object_name)
        logger.info("[IdentityQuery] SOQL: %s", soql)
        records = await self._sf.query_all(soql)
        logger.info("[IdentityQuery] Fetched %d user record(s) for '%s'", len(records), object_name)
        users = self._parse_psa_users(records)
        missing = [u for u in users if not u.federation_identifier]
        if missing:
            logger.warning(
                "[IdentityQuery] %d of %d user(s) for '%s' have no FederationIdentifier",
                len(missing), len(users), object_name,
            )
            for u in missing:
                logger.debug(
                    "[IdentityQuery] User %s (%s) — FederationIdentifier: MISSING",
                    u.id, u.name,
                )
        return users

    # ── 3.3  Global Access Users (ViewAll/ModifyAll) ─────────────────────────

    async def get_global_access_users(self, object_name: str) -> list[SfUser]:
        """
        Query users with ViewAll or ModifyAll on *object_name*.
        Used for the GlobalUsers group in PRIVATE OWD.
        """
        soql = (
            "SELECT Id, Assignee.Name, Assignee.Id, Assignee.Alias, "
            "Assignee.Email, Assignee.FirstName, Assignee.LastName, "
            "Assignee.FederationIdentifier, Assignee.UserName, "
            "Assignee.IsActive, Assignee.UserRoleId, "
            "PermissionSet.Id, PermissionSet.IsOwnedByProfile, "
            "PermissionSet.Profile.Name, PermissionSet.Label "
            "FROM PermissionSetAssignment "
            "WHERE PermissionSetId IN ("
            "  SELECT ParentId FROM ObjectPermissions "
            f"  WHERE SObjectType = '{object_name}' AND PermissionsViewAllRecords = true"
            ") "
            "AND Assignee.IsActive = True "
            "AND (NOT Assignee.Name LIKE '%User%') "
            "ORDER BY Id ASC"
        )
        records = await self._sf.query_all(soql)
        return self._parse_psa_users(records)

    # ── 3.6  Group Shares Only (identity sync optimisation) ──────────────────

    async def get_group_share_ids(self, object_name: str) -> list[str]:
        """
        Query distinct UserOrGroupId values from the share table
        where the share is to a Group (Queue type).
        """
        share_table = _share_table_name(object_name)
        soql = (
            f"SELECT UserOrGroupId "
            f"FROM {share_table} "
            f"GROUP BY UserOrGroupId "
            f"ORDER BY UserOrGroupId ASC"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            exc_str = str(exc)
            if "INVALID_TYPE" in exc_str or "is not supported" in exc_str:
                logger.info(
                    "[IdentityQuery] Share table %s does not exist — %s uses non-standard sharing",
                    share_table, object_name,
                )
                self._no_share_table_types.add(object_name)
            else:
                logger.warning("[IdentityQuery] Could not query %s group shares: %s", share_table, exc)
            return []
        return [r["UserOrGroupId"] for r in records if r.get("UserOrGroupId")]

    def has_share_table(self, object_name: str) -> bool:
        """Return False if a prior query proved the share table doesn't exist."""
        return object_name not in self._no_share_table_types

    def has_owd_field(self, object_name: str) -> bool:
        """Return True if the object has a valid OWD field on Organization.

        Objects like User, Product2, Pricebook2 have no OWD field — their
        access is controlled by Profile/Permission Set, not per-record sharing.
        """
        return object_name in self._owd_field_map and self._owd_field_map[object_name] not in self._invalid_owd_fields

    # ── 3.7  Group Details ───────────────────────────────────────────────────

    async def get_groups_by_ids(self, group_ids: list[str]) -> list[SfGroup]:
        """
        Fetch Group records (type, RelatedId, members) for a list of group IDs.
        Batches queries to respect the IN clause limit.
        """
        if not group_ids:
            return []

        all_groups: list[SfGroup] = []
        for chunk in _chunked(group_ids, self._batch_size):
            quoted = _quote_ids(chunk)
            soql = (
                "SELECT Id, Type, RelatedId, DoesIncludeBosses, "
                "(SELECT UserOrGroupId FROM GroupMembers) "
                f"FROM Group WHERE Id IN ({quoted}) "
                "ORDER BY Id ASC"
            )
            records = await self._sf.query_all(soql)
            for r in records:
                members_data = r.get("GroupMembers") or {}
                member_records = members_data.get("records", []) if isinstance(members_data, dict) else []
                member_ids = [m["UserOrGroupId"] for m in member_records if m.get("UserOrGroupId")]

                raw_type = r.get("Type", "")
                try:
                    group_type = UserOrGroupType(raw_type)
                except ValueError:
                    group_type = UserOrGroupType.REGULAR

                all_groups.append(SfGroup(
                    id=r.get("Id", ""),
                    type=group_type,
                    related_id=r.get("RelatedId") or "",
                    does_include_bosses=bool(r.get("DoesIncludeBosses")),
                    group_members=member_ids,
                ))
        return all_groups

    # ── 3.8  Role Hierarchy ─────────────────────────────────────────────────

    async def get_role_hierarchy(self) -> dict[str, str]:
        """
        Query the full UserRole hierarchy.

        Returns ``{role_id: parent_role_id}`` for all roles.
        Roles with no parent have an empty string value.
        """
        soql = "SELECT Id, ParentRoleId FROM UserRole ORDER BY Id ASC"
        records = await self._sf.query_all(soql)
        return {r["Id"]: (r.get("ParentRoleId") or "") for r in records if r.get("Id")}

    # ── 3.9  Roles Assigned to Active Users ─────────────────────────────────

    async def get_roles_assigned_to_users(self) -> set[str]:
        """
        Query distinct UserRoleId values for active standard users.
        """
        soql = (
            "SELECT UserRoleId FROM User "
            "WHERE UserRoleId != null AND IsActive = True "
            "AND UserType = 'Standard' "
            "AND (NOT Name LIKE '%User%') "
            "GROUP BY UserRoleId "
            "ORDER BY UserRoleId ASC"
        )
        records = await self._sf.query_all(soql)
        return {r["UserRoleId"] for r in records if r.get("UserRoleId")}

    # ── 3.10  Users with roles and permission sets ──────────────────────────

    async def get_users_with_roles(self, object_name: str) -> list[SfUser]:
        """
        Query all active users with their role and parent role info,
        plus permission set assignments for *object_name*.
        """
        soql = (
            "SELECT Id, Name, Alias, Email, FederationIdentifier, "
            "FirstName, LastName, UserName, UserRoleId, "
            "UserRole.ParentRoleId, ManagerId, "
            "(SELECT PermissionSet.Id, PermissionSet.IsOwnedByProfile, "
            "PermissionSet.Profile.Name, PermissionSet.Label "
            "FROM PermissionSetAssignments "
            "WHERE PermissionSetId IN ("
            "  SELECT ParentId FROM ObjectPermissions "
            f"  WHERE SObjectType = '{object_name}' AND PermissionsRead = true"
            ")) "
            "FROM User "
            "WHERE IsActive = True AND (NOT Name LIKE '%User%') "
            "ORDER BY Id ASC"
        )
        logger.info("[IdentityQuery] get_users_with_roles — querying Salesforce users for '%s'", object_name)
        records = await self._sf.query_all(soql)
        users = self._parse_full_users(records)
        missing = [u for u in users if not u.federation_identifier]
        if missing:
            logger.warning(
                "[IdentityQuery] %d/%d user(s) missing FederationIdentifier for '%s': %s",
                len(missing), len(users), object_name,
                ", ".join(f"{u.name} ({u.id})" for u in missing),
            )
        return users

    # ── 3.11  Frozen Users ──────────────────────────────────────────────────

    async def get_frozen_user_ids(self) -> set[str]:
        """Query IDs of users who are currently frozen."""
        soql = "SELECT Id, UserId FROM UserLogin WHERE IsFrozen = True ORDER BY Id ASC"
        records = await self._sf.query_all(soql)
        return {r["UserId"] for r in records if r.get("UserId")}

    # ── 3.12  Manager Relationships ─────────────────────────────────────────

    async def get_manager_map(self) -> dict[str, str]:
        """
        Query all user → manager relationships.

        Returns ``{user_id: manager_id}``.
        """
        soql = "SELECT Id, ManagerId FROM User ORDER BY Id ASC"
        records = await self._sf.query_all(soql)
        return {r["Id"]: r["ManagerId"] for r in records if r.get("Id") and r.get("ManagerId")}

    # ── Territory queries ───────────────────────────────────────────────────

    async def get_territory_user_ids(self, territory_id: str) -> list[str]:
        """
        Query users assigned to a specific Territory2 via
        UserTerritory2Association.
        """
        soql = (
            "SELECT UserId FROM UserTerritory2Association "
            f"WHERE Territory2Id = '{territory_id}'"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError:
            logger.warning("[IdentityQuery] Could not query territory users for %s", territory_id)
            return []
        return [r["UserId"] for r in records if r.get("UserId")]

    async def get_territory_users(self, territory_id: str) -> list[SfUser]:
        """Fetch users assigned to a territory as SfUser instances."""
        user_ids = await self.get_territory_user_ids(territory_id)
        if not user_ids:
            return []
        return await self.get_users_by_ids(user_ids)

    async def get_child_territory_ids(self, territory_id: str) -> list[str]:
        """Query direct child Territory2 IDs under a parent territory."""
        soql = (
            "SELECT Id FROM Territory2 "
            f"WHERE ParentTerritory2Id = '{territory_id}' "
            "ORDER BY Id ASC"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError:
            logger.warning("[IdentityQuery] Could not query child territories for %s", territory_id)
            return []
        return [r["Id"] for r in records if r.get("Id")]

    async def get_all_descendant_territory_ids(self, territory_id: str) -> set[str]:
        """
        Collect all descendant Territory2 IDs via iterative BFS.
        """
        descendants: set[str] = set()
        frontier = [territory_id]
        while frontier:
            current = frontier.pop(0)
            children = await self.get_child_territory_ids(current)
            for child_id in children:
                if child_id not in descendants:
                    descendants.add(child_id)
                    frontier.append(child_id)
        return descendants

    # ── Helper: get users by role ────────────────────────────────────────────

    async def get_users_for_role(self, role_id: str) -> list[SfUser]:
        """Query active users assigned to a specific role."""
        soql = (
            "SELECT Id, Name, Email, FederationIdentifier, UserName "
            "FROM User "
            f"WHERE UserRoleId = '{role_id}' AND IsActive = True "
            "AND (NOT Name LIKE '%User%') "
            "ORDER BY Id ASC"
        )
        records = await self._sf.query_all(soql)
        return [
            SfUser(
                id=r.get("Id", ""),
                name=r.get("Name") or "",
                email=r.get("Email") or "",
                federation_identifier=r.get("FederationIdentifier") or "",
                user_name=r.get("UserName") or "",
            )
            for r in records
            if r.get("Id")
        ]

    # ── Helper: get group members as users ───────────────────────────────────

    async def get_group_member_users(self, group_id: str) -> list[SfUser]:
        """Fetch user members of a specific group."""
        soql = (
            "SELECT UserOrGroupId FROM GroupMember "
            f"WHERE GroupId = '{group_id}' "
            "ORDER BY Id ASC"
        )
        records = await self._sf.query_all(soql)
        user_ids = [r["UserOrGroupId"] for r in records if r.get("UserOrGroupId")]
        if not user_ids:
            return []
        return await self.get_users_by_ids(user_ids)

    # ── Helper: get user by ID ──────────────────────────────────────────────

    async def get_user_by_id(self, user_id: str) -> SfUser | None:
        """Fetch a single user by Salesforce ID."""
        soql = (
            "SELECT Id, Name, Email, FederationIdentifier, UserName, ManagerId "
            f"FROM User WHERE Id = '{user_id}' AND IsActive = True"
        )
        records = await self._sf.query_all(soql)
        if not records:
            return None
        r = records[0]
        return SfUser(
            id=r.get("Id", ""),
            name=r.get("Name") or "",
            email=r.get("Email") or "",
            federation_identifier=r.get("FederationIdentifier") or "",
            user_name=r.get("UserName") or "",
            manager_id=r.get("ManagerId") or "",
        )

    # ── Helper: get users by IDs ────────────────────────────────────────────

    async def get_users_by_ids(self, user_ids: list[str]) -> list[SfUser]:
        """Fetch users by a list of Salesforce IDs."""
        if not user_ids:
            return []

        all_users: list[SfUser] = []
        for chunk in _chunked(user_ids, self._batch_size):
            quoted = _quote_ids(chunk)
            soql = (
                "SELECT Id, Name, Email, FederationIdentifier, UserName "
                f"FROM User WHERE Id IN ({quoted}) AND IsActive = True "
                "ORDER BY Id ASC"
            )
            records = await self._sf.query_all(soql)
            for r in records:
                if r.get("Id"):
                    all_users.append(SfUser(
                        id=r["Id"],
                        name=r.get("Name") or "",
                        email=r.get("Email") or "",
                        federation_identifier=r.get("FederationIdentifier") or "",
                        user_name=r.get("UserName") or "",
                    ))
        return all_users

    # ── Helper: get manager + subordinates ──────────────────────────────────

    async def get_manager_and_subordinates(self, manager_id: str) -> list[SfUser]:
        """Fetch a manager and all direct reports."""
        soql = (
            "SELECT Id, Name, Email, FederationIdentifier, UserName "
            f"FROM User WHERE (Id = '{manager_id}' OR ManagerId = '{manager_id}') "
            "AND IsActive = True "
            "ORDER BY Id ASC"
        )
        records = await self._sf.query_all(soql)
        return [
            SfUser(
                id=r.get("Id", ""),
                name=r.get("Name") or "",
                email=r.get("Email") or "",
                federation_identifier=r.get("FederationIdentifier") or "",
                user_name=r.get("UserName") or "",
            )
            for r in records
            if r.get("Id")
        ]

    # ── Parsing helpers ─────────────────────────────────────────────────────

    def _parse_psa_users(self, psa_records: list[dict[str, Any]]) -> list[SfUser]:
        """Parse PermissionSetAssignment query results into SfUser instances (deduped)."""
        seen: dict[str, SfUser] = {}
        for r in psa_records:
            assignee = r.get("Assignee") or {}
            user_id = assignee.get("Id")
            if not user_id or user_id in seen:
                continue

            perm_set = r.get("PermissionSet") or {}
            seen[user_id] = SfUser(
                id=user_id,
                name=assignee.get("Name") or "",
                alias=assignee.get("Alias") or "",
                email=assignee.get("Email") or "",
                first_name=assignee.get("FirstName") or "",
                last_name=assignee.get("LastName") or "",
                federation_identifier=assignee.get("FederationIdentifier") or "",
                user_name=assignee.get("UserName") or "",
                user_role_id=assignee.get("UserRoleId") or "",
                is_active=assignee.get("IsActive", True),
                permission_sets=[{
                    "Id": perm_set.get("Id") or "",
                    "Label": perm_set.get("Label") or "",
                    "IsOwnedByProfile": perm_set.get("IsOwnedByProfile", False),
                }],
            )
        return list(seen.values())

    def _parse_full_users(self, user_records: list[dict[str, Any]]) -> list[SfUser]:
        """Parse User query results (with nested role and permission set data)."""
        users: list[SfUser] = []
        for r in user_records:
            user_role = r.get("UserRole") or {}
            psa_data = r.get("PermissionSetAssignments") or {}
            psa_records = psa_data.get("records", []) if isinstance(psa_data, dict) else []
            permission_sets = [
                {
                    "Id": (p.get("PermissionSet") or {}).get("Id") or "",
                    "Label": (p.get("PermissionSet") or {}).get("Label") or "",
                    "IsOwnedByProfile": (p.get("PermissionSet") or {}).get("IsOwnedByProfile", False),
                }
                for p in psa_records
            ]

            users.append(SfUser(
                id=r.get("Id", ""),
                name=r.get("Name") or "",
                alias=r.get("Alias") or "",
                email=r.get("Email") or "",
                first_name=r.get("FirstName") or "",
                last_name=r.get("LastName") or "",
                federation_identifier=r.get("FederationIdentifier") or "",
                user_name=r.get("UserName") or "",
                user_role_id=r.get("UserRoleId") or "",
                parent_role_id=user_role.get("ParentRoleId") or "",
                manager_id=r.get("ManagerId") or "",
                permission_sets=permission_sets,
            ))
        return users
