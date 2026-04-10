from __future__ import annotations

from dataclasses import dataclass, field as dc_field, make_dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlencode
import asyncio
import logging
import os

from Salesforce.settings import build_owd_field_map, load_schema_config
from Item.item_converter import load_converter_config


logger = logging.getLogger("salesforce_connector")


class EntityVisibility(str, Enum):
    NONE = "None"
    PUBLIC_READ_ONLY = "Read"
    PUBLIC_READ_WRITE = "Edit"
    PUBLIC_READ_WRITE_TRANSFER = "ReadEditTransfer"
    ALL = "All"
    CONTROLLED_BY_PARENT = "ControlledByParent"
    CONTROLLED_BY_CAMPAIGN = "ControlledByCampaign"
    CONTROLLED_BY_LEAD_OR_CONTACT = "ControlledByLeadOrContact"


class UserOrGroupType(str, Enum):
    USER = "User"
    QUEUE = "Queue"
    ROLE = "Role"
    ROLE_AND_SUBORDINATES = "RoleAndSubordinates"
    ROLE_AND_SUBORDINATES_INTERNAL = "RoleAndSubordinatesInternal"
    ORGANIZATION = "Organization"
    MANAGER = "Manager"
    MANAGER_AND_SUBORDINATES_INTERNAL = "ManagerAndSubordinatesInternal"
    PUBLIC_GROUP = "Group"


class RecordEnumerationDirection(str, Enum):
    ASCENDING = "Ascending"
    DESCENDING = "Descending"


@dataclass
class IdentityResponseBase:
    Id: str
    attributes: Optional[dict[str, Any]] = None


@dataclass
class UserOrGroup:
    Type: str
    attributes: Optional[dict[str, Any]] = None


@dataclass
class PermissionSetAssignment(IdentityResponseBase):
    Id: str = ""
    AssigneeId: Optional[str] = None
    PermissionSetId: Optional[str] = None
    IsActive: Optional[bool] = None


@dataclass
class UserRole:
    Id: str = ""
    Name: Optional[str] = None
    ParentRoleId: Optional[str] = None
    DeveloperName: Optional[str] = None
    ContactAccessForAccountOwner: Optional[str] = None
    OpportunityAccessForAccountOwner: Optional[str] = None


@dataclass
class User(IdentityResponseBase):
    Name: Optional[str] = None
    Alias: Optional[str] = None
    Email: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    FederationIdentifier: Optional[str] = None
    UserName: Optional[str] = None
    IsActive: Optional[bool] = None
    UserType: Optional[str] = None
    IsFrozen: bool = False
    UserRoleId: Optional[str] = None
    ManagerId: Optional[str] = None
    UserRole: Optional[UserRole] = None
    PermissionSets: Optional[list[PermissionSetAssignment]] = None
    PermissionSetAssignments: Optional[dict[str, Any]] = None


@dataclass
class EntityShareBase(IdentityResponseBase):
    Id: str = ""
    UserOrGroupId: Optional[str] = None
    RowCause: Optional[str] = None
    UserOrGroup: Optional[UserOrGroup] = None


@dataclass
class Group(IdentityResponseBase):
    Name: Optional[str] = None
    Type: Optional[str] = None
    RelatedId: Optional[str] = None
    DeveloperName: Optional[str] = None
    DoesIncludeBosses: Optional[bool] = None
    GroupMembers: Optional[dict[str, Any]] = None


@dataclass
class GroupMember(IdentityResponseBase):
    GroupId: Optional[str] = None
    UserOrGroupId: Optional[str] = None


@dataclass
class ObjectRecord(IdentityResponseBase):
    IsDeleted: bool = False
    Shares: Optional[dict[str, Any]] = None


@dataclass
class UserLogin(IdentityResponseBase):
    UserId: Optional[str] = None
    IsFrozen: bool = False


@dataclass
class ObjectTerritory2Association(IdentityResponseBase):
    """Maps Salesforce records (Account / Opportunity) to Territory2 assignments."""

    ObjectId: Optional[str] = None
    Territory2Id: Optional[str] = None
    AssociationCause: Optional[str] = None
    SobjectType: Optional[str] = None


@dataclass
class UserTerritory2Association(IdentityResponseBase):
    """Maps users to Territory2 assignments."""

    UserId: Optional[str] = None
    Territory2Id: Optional[str] = None
    RoleInTerritory2: Optional[str] = None


@dataclass
class Territory2(IdentityResponseBase):
    """Territory hierarchy node – holds the parent territory reference."""

    ParentTerritory2Id: Optional[str] = None


@dataclass
class Organization(IdentityResponseBase):
    Id: str = ""
    DefaultAccountAccess: EntityVisibility = EntityVisibility.NONE
    DefaultContactAccess: EntityVisibility = EntityVisibility.NONE
    DefaultOpportunityAccess: EntityVisibility = EntityVisibility.NONE
    DefaultLeadAccess: EntityVisibility = EntityVisibility.NONE
    DefaultCaseAccess: EntityVisibility = EntityVisibility.NONE
    DefaultCampaignAccess: EntityVisibility = EntityVisibility.NONE


@dataclass
class SfIdentityCheckpointState:
    LastRecordId: str = ""
    NextUrl: str = ""
    Exhausted: bool = False


T = TypeVar("T", bound=IdentityResponseBase)


class SalesforceIdentitySOQLResponseProcessor:
    def get(self, response: dict[str, Any], model_class: type[T]) -> list[T]:
        records = response.get("records", [])
        if not records:
            return []

        results: list[T] = []
        for record in records:
            try:
                parsed = self._parse_record(record, model_class)
                if parsed is not None:
                    results.append(parsed)
            except Exception as error:
                logger.warning("Failed to parse %s record: %s", model_class.__name__, error, exc_info=True)
        return results

    def _parse_record(self, record: dict[str, Any], model_class: type[T]) -> T | None:
        if not record:
            return None

        clean_record = {key: value for key, value in record.items() if key != "attributes"}

        if model_class is User:
            return self._parse_user(clean_record)  # type: ignore[return-value]
        if model_class is EntityShareBase:
            return self._parse_entity_share(clean_record)  # type: ignore[return-value]
        if model_class is ObjectRecord:
            return self._parse_object_record(clean_record)  # type: ignore[return-value]
        if model_class is Organization:
            return self._parse_organization(clean_record)  # type: ignore[return-value]

        return model_class(**self._filter_fields(clean_record, model_class))

    def _parse_user(self, record: dict[str, Any]) -> User:
        user_data = self._filter_fields(record, User)
        if "Username" in record and "UserName" not in user_data:
            user_data["UserName"] = record["Username"]

        if "UserRole" in record and isinstance(record["UserRole"], dict):
            user_data["UserRole"] = UserRole(**self._filter_fields(record["UserRole"], UserRole))

        if "PermissionSetAssignments" in record:
            user_data["PermissionSetAssignments"] = record["PermissionSetAssignments"]

        return User(**user_data)

    def _parse_entity_share(self, record: dict[str, Any]) -> EntityShareBase:
        share_data = self._filter_fields(record, EntityShareBase)
        share_data.setdefault("Id", "")
        if "UserOrGroup" in record and isinstance(record["UserOrGroup"], dict):
            share_data["UserOrGroup"] = UserOrGroup(
                Type=record["UserOrGroup"].get("Type", ""),
                attributes=record["UserOrGroup"].get("attributes"),
            )
        return EntityShareBase(**share_data)

    def _parse_object_record(self, record: dict[str, Any]) -> ObjectRecord:
        object_data = self._filter_fields(record, ObjectRecord)
        if "Shares" in record:
            object_data["Shares"] = record["Shares"]
        return ObjectRecord(**object_data)

    def _parse_organization(self, record: dict[str, Any]) -> Organization:
        org_data = self._filter_fields(record, Organization)
        for key in (
            "DefaultAccountAccess",
            "DefaultContactAccess",
            "DefaultOpportunityAccess",
            "DefaultLeadAccess",
            "DefaultCaseAccess",
            "DefaultCampaignAccess",
        ):
            value = org_data.get(key)
            if not value:
                continue
            try:
                org_data[key] = EntityVisibility(value)
            except ValueError:
                logger.warning("Unknown entity visibility %s for %s", value, key)
        return Organization(**org_data)

    @staticmethod
    def _filter_fields(record: dict[str, Any], model_class: type[Any]) -> dict[str, Any]:
        if not hasattr(model_class, "__dataclass_fields__"):
            return record
        valid_fields = set(model_class.__dataclass_fields__.keys())
        return {key: value for key, value in record.items() if key in valid_fields}


class IdentitySyncQueries:
    _owd_fields: list[str] = list(dict.fromkeys(
        list(build_owd_field_map().values())
        + ["DefaultCampaignAccess"]
    ))
    OrgWideDefaultQuery = f"SELECT {', '.join(_owd_fields)} from Organization"
    AllSharesFromRecords = (
        "SELECT Id, IsDeleted, (SELECT Id, UserOrGroupId, UserOrGroup.Type from Shares) "
        "from {0}{1} ORDER BY Id {2}"
    )
    GroupMembersQueryFormat = "SELECT Id, GroupId, UserOrGroupId from GroupMember{0} ORDER BY Id asc"
    GroupTypeAndRelatedIdQuery = (
        "SELECT Id, Name, Type, RelatedId, DoesIncludeBosses, "
        "(SELECT UserOrGroupId from GroupMembers Limit 1) from Group{0} ORDER BY Id asc"
    )
    UsersQueryForContentIngestionFormat = (
        "SELECT Id, Name, Alias, Email, FederationIdentifier, FirstName, LastName, "
        "UserName, UserRoleId, UserRole.ParentRoleId, "
        "(SELECT PermissionSet.Id, PermissionSet.IsOwnedByProfile, PermissionSet.Profile.Name, PermissionSet.Label "
        "FROM PermissionSetAssignments "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true)) "
        "from User WHERE IsActive = True AND (NOT Name Like '%User%'){1} ORDER BY Id asc"
    )
    UsersQueryFormat = (
        "SELECT Id, Name, Alias, Email, FederationIdentifier, FirstName, LastName, "
        "UserName, UserRoleId, UserRole.ParentRoleId, IsActive, ManagerId "
        "FROM User WHERE (NOT Name Like '%User%'){0} ORDER BY Id asc{1}"
    )
    UserLoginQuery = "SELECT Id, UserId FROM UserLogin Where IsFrozen = True{0} ORDER BY Id asc"
    UserRoleQuery = (
        "SELECT Id, ParentRoleId, ContactAccessForAccountOwner, OpportunityAccessForAccountOwner "
        "FROM UserRole{0} ORDER BY Id asc"
    )
    UserAndMangerQuery = "SELECT Id, ManagerId from User{0} ORDER BY Id asc"

    # ── Territory-based ACL queries ──────────────────────────────────────────
    # Step 1 – fetch Territory2Ids for a given Account / Opportunity record
    ObjectTerritory2AssociationQueryFormat = (
        "SELECT Id, ObjectId, Territory2Id, AssociationCause, SobjectType "
        "FROM ObjectTerritory2Association WHERE {0}"
    )
    # Step 2 / 4 – fetch UserIds for a set of Territory2Ids
    UserTerritory2AssociationQueryFormat = (
        "SELECT Id, UserId, Territory2Id, RoleInTerritory2 "
        "FROM UserTerritory2Association WHERE Territory2Id IN ({0})"
    )
    # Step 3 – fetch the parent territory for a given Territory2 node
    Territory2ParentQueryFormat = (
        "SELECT Id, ParentTerritory2Id "
        "FROM Territory2 WHERE Id = '{0}'"
    )


class SalesforceConstants:
    _schema = load_schema_config()
    # Per-object config keyed by objectName, loaded from schema.json
    OBJECTS: dict[str, dict] = {obj["objectName"]: obj for obj in _schema.get("objectList", [])}
    # Object names in the order defined in schema.json
    ORDERED_OBJECT_NAMES: list[str] = [obj["objectName"] for obj in _schema.get("objectList", [])]
    # Maximum number of IDs to include in a nested SOQL IN clause
    MAX_FILTER_IDS_IN_NESTED_QUERY: int = int(os.getenv("SALESFORCE_BATCH_SIZE", "100"))


class AsyncSalesforceClient:
    def __init__(self, instance_url: str, api_version: str):
        self.instance_url = instance_url.rstrip("/")
        self.api_version = api_version

    async def query(self, soql: str, access_token: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._execute_query, soql, access_token, False)

    async def query_all(self, soql: str, access_token: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._execute_query, soql, access_token, True)

    def _execute_query(self, soql: str, access_token: str, use_query_all: bool) -> dict[str, Any]:
        import requests  # Import here to avoid requiring it when just using enums
        
        endpoint = "queryAll" if use_query_all else "query"
        query_url = (
            f"{self.instance_url}/services/data/{self.api_version}/{endpoint}?"
            f"{urlencode({'q': soql})}"
        )

        response = requests.get(
            query_url,
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {access_token}",
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()


class ClientHelperForIdentitySync:
    def __init__(self, salesforce_client: AsyncSalesforceClient, instance_url: str, access_token: str):
        self.salesforce_client = salesforce_client
        self.instance_url = instance_url
        self.access_token = access_token
        self.response_processor = SalesforceIdentitySOQLResponseProcessor()

    async def get_org_wide_defaults_from_salesforce(self) -> Organization:
        response = await self._execute_query(IdentitySyncQueries.OrgWideDefaultQuery)
        organizations = self.response_processor.get(response, Organization)
        if not organizations:
            raise ValueError("No organization record found")

        organization = organizations[0]
        for key in ("DefaultAccountAccess", "DefaultOpportunityAccess"):
            value = getattr(organization, key)
            if value in (
                EntityVisibility.CONTROLLED_BY_CAMPAIGN,
                EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
            ):
                setattr(organization, key, EntityVisibility.NONE)
        return organization

    async def get_org_wide_defaults_map(self) -> dict[str, EntityVisibility]:
        organization = await self.get_org_wide_defaults_from_salesforce()
        return {
            obj_name: getattr(organization, obj_cfg["owdField"], EntityVisibility.PUBLIC_READ_WRITE)
            for obj_name, obj_cfg in SalesforceConstants.OBJECTS.items()
            if "owdField" in obj_cfg
        }

    async def get_records_with_shares(
        self,
        object_name: str,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = False,
        filter_condition: str = "",
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[ObjectRecord]:
        order = "asc" if direction == RecordEnumerationDirection.ASCENDING else "desc"
        if filter_condition:
            soql = IdentitySyncQueries.AllSharesFromRecords.format(
                object_name,
                f" WHERE {filter_condition}{{0}}",
                order,
            )
        else:
            soql = IdentitySyncQueries.AllSharesFromRecords.format(object_name, "{0}", order)

        records = await self._get_records_using_last_id(
            soql,
            fetch_all,
            bool(filter_condition),
            checkpoint,
            ObjectRecord,
            use_query_all=True,
            direction=direction,
        )
        return [
            record
            for record in records
            if record.Shares and record.Shares.get("records") and not record.IsDeleted
        ]

    async def get_group_members(
        self,
        filter_condition: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[GroupMember]:
        soql = IdentitySyncQueries.GroupMembersQueryFormat
        if filter_condition:
            soql = IdentitySyncQueries.GroupMembersQueryFormat.format(f" WHERE {filter_condition}{{0}}")
        else:
            soql = IdentitySyncQueries.GroupMembersQueryFormat.format("{0}")

        return await self._get_records_using_last_id(
            soql,
            fetch_all,
            bool(filter_condition),
            checkpoint,
            GroupMember,
        )

    async def get_group_type_and_related_id(
        self,
        filter_condition: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[Group]:
        soql = IdentitySyncQueries.GroupTypeAndRelatedIdQuery
        if filter_condition:
            soql = IdentitySyncQueries.GroupTypeAndRelatedIdQuery.format(
                f" WHERE {filter_condition}{{0}}"
            )
        else:
            soql = IdentitySyncQueries.GroupTypeAndRelatedIdQuery.format("{0}")

        return await self._get_records_using_last_id(
            soql,
            fetch_all,
            bool(filter_condition),
            checkpoint,
            Group,
        )

    async def get_users_for_content_ingestion(
        self,
        object_name: str,
        filter_conditions: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        soql = IdentitySyncQueries.UsersQueryForContentIngestionFormat.format(object_name, "{0}")
        if filter_conditions:
            soql = IdentitySyncQueries.UsersQueryForContentIngestionFormat.format(
                object_name,
                f" AND {filter_conditions}{{0}}",
            )

        users = await self._get_records_using_last_id(
            soql,
            fetch_all,
            True,
            checkpoint,
            User,
        )

        for user in users:
            if user.PermissionSetAssignments and user.PermissionSetAssignments.get("records"):
                user.PermissionSets = self.response_processor.get(
                    user.PermissionSetAssignments,
                    PermissionSetAssignment,
                )
        return users

    async def get_authorized_users_and_groups_from_salesforce(
        self,
        user_ids: list[str],
        group_ids: list[str],
        salesforce_object_handler: Any,
        entity_visibility: EntityVisibility,
        frozen_users: set[str],
    ) -> tuple[dict[str, dict[str, User]], dict[str, Group]]:
        authorized_users_for_sf_objects = {salesforce_object_handler.object_name: {}}
        for child in getattr(salesforce_object_handler, "child_handlers", []):
            authorized_users_for_sf_objects[child.object_name] = {}

        distinct_users = list(set(user_ids))
        batch_size = SalesforceConstants.MAX_FILTER_IDS_IN_NESTED_QUERY
        user_batches = [
            distinct_users[index : index + batch_size]
            for index in range(0, len(distinct_users), batch_size)
        ]

        for user_batch in user_batches:
            if not user_batch:
                continue
            quoted_user_ids = ", ".join("'" + user_id + "'" for user_id in user_batch)
            filter_str = f"Id in ({quoted_user_ids})"
            current_batch_users = await self.get_users_for_content_ingestion(
                salesforce_object_handler.object_name,
                filter_str,
            )

            authorized_users = authorized_users_for_sf_objects[salesforce_object_handler.object_name]
            for user in current_batch_users:
                user.IsFrozen = user.Id in frozen_users
                authorized_users[user.Id] = user

            for child in getattr(salesforce_object_handler, "child_handlers", []):
                child_users = await self.get_users_for_content_ingestion(child.object_name, filter_str)
                child_authorized = authorized_users_for_sf_objects[child.object_name]
                for user in child_users:
                    user.IsFrozen = user.Id in frozen_users
                    child_authorized[user.Id] = user

        sf_groups: dict[str, Group] = {}
        if entity_visibility == EntityVisibility.NONE:
            distinct_groups = list(set(group_ids))
            group_batches = [
                distinct_groups[index : index + batch_size]
                for index in range(0, len(distinct_groups), batch_size)
            ]
            all_groups: list[Group] = []
            for group_batch in group_batches:
                if not group_batch:
                    continue
                quoted_group_ids = ", ".join("'" + group_id + "'" for group_id in group_batch)
                filter_str = f"Id in ({quoted_group_ids})"
                all_groups.extend(await self.get_group_type_and_related_id(filter_str))
            sf_groups = {group.Id: group for group in all_groups}

        return authorized_users_for_sf_objects, sf_groups

    async def get_users_from_salesforce(
        self,
        filter_conditions: str = "",
        limit: int = 0,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        limit_clause = f" Limit {limit}" if limit > 0 else ""
        filter_clause = f" AND {filter_conditions}{{0}}" if filter_conditions else "{0}"
        soql = IdentitySyncQueries.UsersQueryFormat.format(filter_clause, limit_clause)
        return await self._get_records_using_last_id(
            soql,
            fetch_all,
            True,
            checkpoint,
            User,
        )

    async def get_frozen_users(self) -> list[UserLogin]:
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserLoginQuery,
            True,
            True,
            None,
            UserLogin,
        )

    async def get_user_role_hierarchy_from_salesforce(
        self,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[UserRole]:
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserRoleQuery,
            fetch_all,
            False,
            checkpoint,
            UserRole,
        )

    async def get_users_and_managers(self) -> list[User]:
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserAndMangerQuery,
            True,
            False,
            None,
            User,
        )

    # ── Territory-based ACL helpers ──────────────────────────────────────────

    async def get_territory_ids_for_record(self, object_id: str) -> list[str]:
        """Step 1: Fetch Territory2Ids assigned to a given Account / Opportunity record.

        Queries: ObjectTerritory2Association WHERE ObjectId = '<object_id>'
        """
        soql = IdentitySyncQueries.ObjectTerritory2AssociationQueryFormat.format(
            f"ObjectId = '{object_id}'"
        )
        response = await self._execute_query(soql)
        associations = self.response_processor.get(response, ObjectTerritory2Association)
        return [a.Territory2Id for a in associations if a.Territory2Id]

    async def get_users_for_territories(self, territory_ids: list[str]) -> list[str]:
        """Steps 2 / 4: Fetch UserIds for a list of Territory2Ids.

        Queries: UserTerritory2Association WHERE Territory2Id IN (<territory_ids>)
        """
        if not territory_ids:
            return []
        quoted = ", ".join(f"'{t}'" for t in sorted(set(territory_ids)))
        soql = IdentitySyncQueries.UserTerritory2AssociationQueryFormat.format(quoted)
        response = await self._execute_query(soql)
        associations = self.response_processor.get(response, UserTerritory2Association)
        return [a.UserId for a in associations if a.UserId]

    async def get_parent_territory_id(self, territory_id: str) -> str | None:
        """Step 3: Fetch the ParentTerritory2Id for a given Territory2 node.

        Returns None when there is no parent (i.e. the territory is a root node).
        Queries: Territory2 WHERE Id = '<territory_id>'
        """
        soql = IdentitySyncQueries.Territory2ParentQueryFormat.format(territory_id)
        response = await self._execute_query(soql)
        territories = self.response_processor.get(response, Territory2)
        if territories and territories[0].ParentTerritory2Id:
            return territories[0].ParentTerritory2Id
        return None

    async def _get_records_using_last_id(
        self,
        soql_format: str,
        fetch_all: bool,
        contains_filter_conditions: bool,
        checkpoint: Optional[SfIdentityCheckpointState],
        model_class: type[T],
        use_query_all: bool = False,
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[T]:
        def processor(current_set, records, last_id, results):
            if last_id and current_set and last_id == current_set[0].Id:
                current_set.pop(0)
            results.extend(current_set)
            if (not records.get("done", True) or records.get("nextRecordsUrl")) and current_set:
                return current_set[-1].Id
            return ""

        return await self._get_records_using_custom_last_id(
            soql_format,
            fetch_all,
            contains_filter_conditions,
            processor,
            "Id",
            checkpoint,
            model_class,
            use_query_all,
            direction,
        )

    async def _get_records_using_custom_last_id(
        self,
        soql_format: str,
        fetch_all: bool,
        contains_filter_conditions: bool,
        current_set_processor: Callable[..., str],
        last_id_field_name: str,
        checkpoint: Optional[SfIdentityCheckpointState],
        model_class: type[T],
        use_query_all: bool = False,
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[T]:
        last_id = checkpoint.LastRecordId if checkpoint else ""
        if checkpoint:
            checkpoint.NextUrl = ""

        results: list[T] = []
        comparison = ">" if direction == RecordEnumerationDirection.ASCENDING else "<"

        while True:
            if last_id:
                where_clause = (
                    f"{' AND ' if contains_filter_conditions else ' WHERE '}"
                    f"{last_id_field_name} {comparison} '{last_id}'"
                )
            else:
                where_clause = ""

            soql = soql_format.format(where_clause)
            response = await (self._execute_query_all(soql) if use_query_all else self._execute_query(soql))
            if not response or not response.get("records"):
                break

            current_set = self.response_processor.get(response, model_class)
            if current_set:
                new_last_id = current_set_processor(current_set, response, last_id, results)
                last_id = "" if new_last_id == last_id else new_last_id
            else:
                last_id = ""

            if not fetch_all or not last_id:
                break

        if checkpoint:
            checkpoint.LastRecordId = last_id
            checkpoint.Exhausted = not last_id

        return results

    async def _execute_query(self, soql: str) -> dict[str, Any]:
        return await self.salesforce_client.query(soql, self.access_token)

    async def _execute_query_all(self, soql: str) -> dict[str, Any]:
        return await self.salesforce_client.query_all(soql, self.access_token)