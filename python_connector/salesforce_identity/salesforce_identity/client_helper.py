"""
Client Helper for Identity Sync

Mirrors ClientHelperForIdentitySync.cs - handles identity-related data fetching
from Salesforce including permissions, users, roles, and groups.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Callable, Optional

from .models import (
    EntityShareBase,
    EntityVisibility,
    FieldPermissions,
    Group,
    GroupMember,
    ObjectPermissions,
    ObjectRecord,
    Organization,
    PermissionSetAssignment,
    RecordEnumerationDirection,
    SfIdentityCheckpointState,
    User,
    UserLogin,
    UserRole,
    IdentityResponseBase,
)
from .queries import IdentitySyncQueries
from .response_processor import SalesforceIdentitySOQLResponseProcessor

logger = logging.getLogger(__name__)


# Constants (mirroring SalesforceConstants from C#)
class SalesforceConstants:
    """Salesforce-specific constants."""

    SF_QUERY_BATCH_SIZE = 2000
    MAX_FILTER_IDS_IN_NESTED_QUERY = 100
    PARALLEL_FOR_EACH_BATCH_COUNT = 5
    NUMBER_OF_PREVIOUS_METHODS_IN_CALL_STACK = 3

    # Object names
    ACCOUNT = "Account"
    CONTACT = "Contact"
    OPPORTUNITY = "Opportunity"
    LEAD = "Lead"
    CASE = "Case"


class ClientHelperForIdentitySync:
    """
    Helper for Salesforce client to get identity-related data.

    Mirrors the C# ClientHelperForIdentitySync class.
    """

    def __init__(
        self,
        salesforce_client: Any,
        instance_url: str,
        access_token: str,
    ):
        """
        Initialize the identity sync helper.

        Args:
            salesforce_client: Salesforce client instance (with query methods)
            instance_url: Salesforce instance URL
            access_token: OAuth access token
        """
        self.salesforce_client = salesforce_client
        self.instance_url = instance_url
        self.access_token = access_token
        self.response_processor = SalesforceIdentitySOQLResponseProcessor()

    async def get_permission_sets_from_salesforce(
        self,
        object_name: str,
        filter_conditions: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
        use_v2_query: bool = False,
    ) -> list[PermissionSetAssignment]:
        """
        Get permission sets from Salesforce.

        Args:
            object_name: Current object name
            filter_conditions: Additional WHERE clause conditions
            checkpoint: Checkpoint state for resumption
            fetch_all: Whether to fetch all records
            use_v2_query: Whether to use V2 query format

        Returns:
            List of permission set assignments
        """
        logger.info(f"GetPermissionSetsFromSalesforce for object: {object_name}")

        soql_format = (
            IdentitySyncQueries.PermissionSetsQueryFormatV2
            if use_v2_query
            else IdentitySyncQueries.PermissionSetsQueryFormat
        )

        if filter_conditions:
            soql = soql_format.format(object_name, f" AND {filter_conditions}{{0}}")
        else:
            soql = soql_format.format(object_name, "{0}")

        return await self._get_records_using_last_id(
            soql, fetch_all, True, checkpoint, PermissionSetAssignment
        )

    async def get_users_for_content_ingestion(
        self,
        object_name: str,
        filter_conditions: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        """
        Get users and their permission sets for content ingestion.

        Args:
            object_name: Current object name
            filter_conditions: Additional WHERE clause conditions
            checkpoint: Checkpoint state
            fetch_all: Whether to fetch all records

        Returns:
            List of users with permission sets
        """
        soql = IdentitySyncQueries.UsersQueryForContentIngestionFormat.format(
            object_name, "{0}"
        )
        if filter_conditions:
            soql = IdentitySyncQueries.UsersQueryForContentIngestionFormat.format(
                object_name, f" AND {filter_conditions}{{0}}"
            )

        users = await self._get_records_using_last_id(
            soql, fetch_all, True, checkpoint, User
        )

        # Process nested PermissionSetAssignments
        for user in users:
            if (
                user.PermissionSetAssignments
                and user.PermissionSetAssignments.get("records")
            ):
                user.PermissionSets = self.response_processor.get(
                    user.PermissionSetAssignments, PermissionSetAssignment
                )

        return users

    async def get_users_and_permission_set(
        self,
        object_name: str,
        filter_conditions: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        """
        Get users and their permission sets.

        Args:
            object_name: Current object name
            filter_conditions: Additional WHERE clause conditions
            checkpoint: Checkpoint state
            fetch_all: Whether to fetch all records

        Returns:
            List of users with permission sets
        """
        soql = IdentitySyncQueries.UsersAndPermissionSetFormat.format(
            object_name, "{0}"
        )
        if filter_conditions:
            soql = IdentitySyncQueries.UsersAndPermissionSetFormat.format(
                object_name, f" AND {filter_conditions}{{0}}"
            )

        users = await self._get_records_using_last_id(
            soql, fetch_all, True, checkpoint, User
        )

        # Process nested PermissionSetAssignments
        for user in users:
            if (
                user.PermissionSetAssignments
                and user.PermissionSetAssignments.get("records")
            ):
                user.PermissionSets = self.response_processor.get(
                    user.PermissionSetAssignments, PermissionSetAssignment
                )

        logger.info(
            f"GetUsersAndPermissionSet for {object_name} returning {len(users)} users"
        )
        return users

    async def get_users_and_their_role(
        self,
        filter_conditions: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        """Get users and their roles."""
        soql = IdentitySyncQueries.UserAndRoleFormat
        if filter_conditions:
            soql = IdentitySyncQueries.UserAndRoleFormat.format(
                f" AND {filter_conditions}{{0}}"
            )

        return await self._get_records_using_last_id(
            soql, fetch_all, bool(filter_conditions), checkpoint, User
        )

    async def get_global_access_users_from_salesforce(
        self,
        object_name: str,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
    ) -> list[PermissionSetAssignment]:
        """Get global access users (ModifyAll permission)."""
        logger.info(f"GetGlobalAccessUsersFromSalesforce for object: {object_name}")

        soql = IdentitySyncQueries.GlobalAccessUsersQueryFormat.format(
            object_name, "{0}"
        )

        return await self._get_records_using_last_id(
            soql, False, True, checkpoint, PermissionSetAssignment
        )

    async def get_org_wide_defaults_from_salesforce(self) -> Organization:
        """Get org-wide default settings."""
        logger.info("GetOrgWideDefaultsFromSalesforce")

        response = await self._execute_query(IdentitySyncQueries.OrgWideDefaultQuery)
        orgs = self.response_processor.get(response, Organization)

        if not orgs:
            raise ValueError("No organization record found")

        org = orgs[0]

        # Normalize ControlledByCampaign/ControlledByLeadOrContact to None
        if org.DefaultAccountAccess in (
            EntityVisibility.CONTROLLED_BY_CAMPAIGN,
            EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
        ):
            org.DefaultAccountAccess = EntityVisibility.NONE

        if org.DefaultOpportunityAccess in (
            EntityVisibility.CONTROLLED_BY_CAMPAIGN,
            EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
        ):
            org.DefaultOpportunityAccess = EntityVisibility.NONE

        return org

    async def get_org_wide_defaults_map(self) -> dict[str, EntityVisibility]:
        """Get org-wide defaults as a map."""
        org = await self.get_org_wide_defaults_from_salesforce()

        return {
            SalesforceConstants.ACCOUNT: org.DefaultAccountAccess,
            SalesforceConstants.CONTACT: org.DefaultContactAccess,
            SalesforceConstants.OPPORTUNITY: org.DefaultOpportunityAccess,
            SalesforceConstants.LEAD: org.DefaultLeadAccess,
            SalesforceConstants.CASE: org.DefaultCaseAccess,
        }

    async def get_shares_for_public_groups_sequential(
        self,
        object_name: str,
        filter_condition: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[EntityShareBase]:
        """Get entity shares for public groups (sequential)."""
        logger.info(f"GetSharesForPublicGroupsSequential for {object_name}")

        soql_format = IdentitySyncQueries.SharesQueryForGroupsSequentialFormat.format(
            object_name,
            "{0}" if not filter_condition else f" AND {filter_condition}{{0}}",
            SalesforceConstants.SF_QUERY_BATCH_SIZE * 5,
        )

        def processor(current_set, records, last_id, results):
            # Bug in Salesforce API: first element may still be the one with last_id
            if last_id and current_set and last_id == current_set[0].UserOrGroupId:
                current_set.pop(0)

            results.extend(current_set)
            return current_set[-1].UserOrGroupId if current_set else ""

        return await self._get_records_using_custom_last_id(
            soql_format,
            fetch_all,
            True,
            processor,
            "UserOrGroupId",
            checkpoint,
            EntityShareBase,
        )

    async def get_shares_for_groups_from_records(
        self,
        object_name: str,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[EntityShareBase]:
        """Get entity shares from record-level query."""
        logger.info(f"GetSharesForGroupsFromRecords for {object_name}")

        soql = IdentitySyncQueries.SharesFromRecords.format(object_name, "{0}")

        records = await self._get_records_using_last_id(
            soql, fetch_all, False, checkpoint, ObjectRecord
        )

        # Extract shares from records
        all_shares = []
        for record in records:
            if record.Shares and record.Shares.get("records"):
                shares = self.response_processor.get(record.Shares, EntityShareBase)
                all_shares.extend(shares)

        # Return distinct shares
        unique_shares = list({share.Id: share for share in all_shares}.values())
        logger.info(
            f"GetSharesForGroupsFromRecords for {object_name} got {len(all_shares)} "
            f"shares, returning {len(unique_shares)} distinct"
        )

        return unique_shares

    async def get_records_with_shares(
        self,
        object_name: str,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = False,
        filter_condition: str = "",
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[ObjectRecord]:
        """Get all records with their shares."""
        logger.info(f"GetRecordsWithShares for {object_name}")

        order = "asc" if direction == RecordEnumerationDirection.ASCENDING else "desc"

        if filter_condition:
            soql = IdentitySyncQueries.AllSharesFromRecords.format(
                object_name, f" WHERE {filter_condition}{{0}}", order
            )
        else:
            soql = IdentitySyncQueries.AllSharesFromRecords.format(
                object_name, "{0}", order
            )

        records = await self._get_records_using_last_id(
            soql,
            fetch_all,
            bool(filter_condition),
            checkpoint,
            ObjectRecord,
            use_query_all=True,
            direction=direction,
        )

        # Filter to records with shares
        valid_records = [
            r
            for r in records
            if r.Shares
            and r.Shares.get("records")
            and not r.IsDeleted
        ]

        logger.info(
            f"GetRecordsWithShares for {object_name} got {len(records)} records, "
            f"returning {len(valid_records)} with shares"
        )

        return valid_records

    async def get_shares_for_public_groups(
        self,
        object_name: str,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[EntityShareBase]:
        """Get entity shares for public groups."""
        logger.info(f"GetSharesForPublicGroups for {object_name}")

        soql_format = IdentitySyncQueries.SharesQueryForGroupsFormat.format(
            object_name, "{0}", SalesforceConstants.SF_QUERY_BATCH_SIZE
        )

        def processor(current_set, records, last_id, results):
            if last_id and current_set and last_id == current_set[0].UserOrGroupId:
                current_set.pop(0)

            results.extend(current_set)
            return current_set[-1].UserOrGroupId if current_set else ""

        return await self._get_records_using_custom_last_id(
            soql_format,
            fetch_all,
            True,
            processor,
            "UserOrGroupId",
            checkpoint,
            EntityShareBase,
        )

    async def get_group_type_and_related_id(
        self,
        filter_condition: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[Group]:
        """Get group type and related ID."""
        soql = IdentitySyncQueries.GroupTypeAndRelatedIdQuery
        if filter_condition:
            soql = IdentitySyncQueries.GroupTypeAndRelatedIdQuery.format(
                f" WHERE {filter_condition}{{0}}"
            )

        return await self._get_records_using_last_id(
            soql, fetch_all, bool(filter_condition), checkpoint, Group
        )

    async def get_group_members(
        self,
        filter_condition: str = "",
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[GroupMember]:
        """Get group members."""
        soql = IdentitySyncQueries.GroupMembersQueryFormat
        if filter_condition:
            soql = IdentitySyncQueries.GroupMembersQueryFormat.format(
                f" WHERE {filter_condition}{{0}}"
            )

        return await self._get_records_using_last_id(
            soql, fetch_all, bool(filter_condition), checkpoint, GroupMember
        )

    async def get_authorized_users_and_groups_from_salesforce(
        self,
        user_ids: list[str],
        group_ids: list[str],
        salesforce_object_handler: Any,  # SalesforceObjectHandler
        entity_visibility: EntityVisibility,
        frozen_users: set[str],
    ) -> tuple[dict[str, dict[str, User]], dict[str, Group]]:
        """
        Get authorized users and groups from Salesforce.

        Returns:
            Tuple of (authorized_users_for_sf_objects, sf_groups)
        """
        # Initialize result dictionaries
        authorized_users_for_sf_objects = {
            salesforce_object_handler.object_name: {}
        }
        for child in getattr(salesforce_object_handler, "child_handlers", []):
            authorized_users_for_sf_objects[child.object_name] = {}

        # Batch user IDs
        distinct_users = list(set(user_ids))
        batch_size = SalesforceConstants.MAX_FILTER_IDS_IN_NESTED_QUERY
        user_batches = [
            distinct_users[i : i + batch_size]
            for i in range(0, len(distinct_users), batch_size)
        ]

        # Fetch users in batches
        for user_batch in user_batches:
            filter_str = f"Id in ({', '.join(f\"'{uid}'\" for uid in user_batch)})"

            # Parent object users
            current_batch_users = await self.get_users_for_content_ingestion(
                salesforce_object_handler.object_name, filter_str
            )

            authorized_users = authorized_users_for_sf_objects[
                salesforce_object_handler.object_name
            ]
            for user in current_batch_users:
                user.IsFrozen = user.Id in frozen_users
                authorized_users[user.Id] = user

            # Child object users
            for child in getattr(salesforce_object_handler, "child_handlers", []):
                child_users = await self.get_users_for_content_ingestion(
                    child.object_name, filter_str
                )

                child_authorized = authorized_users_for_sf_objects[child.object_name]
                for user in child_users:
                    user.IsFrozen = user.Id in frozen_users
                    child_authorized[user.Id] = user

        # Fetch groups if private visibility
        sf_groups = {}
        if entity_visibility == EntityVisibility.NONE:
            distinct_groups = list(set(group_ids))
            group_batches = [
                distinct_groups[i : i + batch_size]
                for i in range(0, len(distinct_groups), batch_size)
            ]

            # Fetch groups in parallel batches
            all_groups = []
            for group_batch in group_batches:
                filter_str = f"Id in ({', '.join(f\"'{gid}'\" for gid in group_batch)})"
                groups = await self.get_group_type_and_related_id(filter_str)
                all_groups.extend(groups)

            sf_groups = {g.Id: g for g in all_groups}

        logger.info(
            f"GetAuthorizedUsersAndGroups for {salesforce_object_handler.object_name}: "
            f"{len(authorized_users_for_sf_objects)} object types"
        )

        return authorized_users_for_sf_objects, sf_groups

    async def get_user_role_hierarchy_from_salesforce(
        self,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[UserRole]:
        """Get user role hierarchy."""
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserRoleQuery, fetch_all, False, checkpoint, UserRole
        )

    async def get_user_roles_assigned_to_users(
        self, should_use_v2: bool = False
    ) -> list[str]:
        """Get user roles that are assigned to users."""
        soql_format = (
            IdentitySyncQueries.UserRolesAssignedToUsersQueryV2.format(
                "{0}", SalesforceConstants.SF_QUERY_BATCH_SIZE
            )
            if should_use_v2
            else IdentitySyncQueries.UserRolesAssignedToUsersQuery.format(
                "{0}", SalesforceConstants.SF_QUERY_BATCH_SIZE
            )
        )

        def processor(current_set, records, last_id, results):
            if last_id and current_set and last_id == current_set[0].UserRoleId:
                current_set.pop(0)

            results.extend(current_set)
            return current_set[-1].UserRoleId if current_set else ""

        users = await self._get_records_using_custom_last_id(
            soql_format, True, True, processor, "UserRoleId", None, User
        )

        return [u.UserRoleId for u in users if u.UserRoleId]

    async def get_users_from_salesforce(
        self,
        filter_conditions: str = "",
        limit: int = 0,
        checkpoint: Optional[SfIdentityCheckpointState] = None,
        fetch_all: bool = True,
    ) -> list[User]:
        """Get users from Salesforce."""
        limit_clause = f" Limit {limit}" if limit > 0 else ""
        filter_clause = f" AND {filter_conditions}{{0}}" if filter_conditions else "{0}"

        soql = IdentitySyncQueries.UsersQueryFormat.format(filter_clause, limit_clause)

        return await self._get_records_using_last_id(
            soql, fetch_all, True, checkpoint, User
        )

    async def get_frozen_users(self) -> list[UserLogin]:
        """Get frozen users."""
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserLoginQuery, True, True, None, UserLogin
        )

    async def get_users_and_managers(self) -> list[User]:
        """Get users and their managers."""
        return await self._get_records_using_last_id(
            IdentitySyncQueries.UserAndMangerQuery, True, False, None, User
        )

    async def get_object_permissions(
        self, object_name: str, should_only_check_profile_for_fls: bool = False
    ) -> list[ObjectPermissions]:
        """Get object permissions."""
        if should_only_check_profile_for_fls:
            logger.info("Checking object permissions for only profiles")

        query_format = (
            IdentitySyncQueries.ObjectPermissionsOnlyProfilesQueryFormat
            if should_only_check_profile_for_fls
            else IdentitySyncQueries.ObjectPermissionsQueryFormat
        )

        soql_format = query_format.format(
            object_name, "{0}", SalesforceConstants.SF_QUERY_BATCH_SIZE
        )

        def processor(current_set, records, last_id, results):
            if last_id and current_set and last_id == current_set[0].ParentId:
                current_set.pop(0)

            results.extend(current_set)
            return current_set[-1].ParentId if current_set else ""

        return await self._get_records_using_custom_last_id(
            soql_format, True, True, processor, "ParentId", None, ObjectPermissions
        )

    async def get_field_permission(
        self,
        object_name: str,
        fields: str,
        should_only_check_profile_for_fls: bool = False,
    ) -> list[FieldPermissions]:
        """Get field permissions."""
        if should_only_check_profile_for_fls:
            logger.info("Checking field permissions for only profiles")

        query_format = (
            IdentitySyncQueries.FieldPermissionsOnlyProfilesQueryFormat
            if should_only_check_profile_for_fls
            else IdentitySyncQueries.FieldPermissionsQueryFormat
        )

        soql = query_format.format(object_name, fields, "{0}")

        return await self._get_records_using_last_id(
            soql, True, True, None, FieldPermissions
        )

    def update_access_token(self, access_token: str) -> None:
        """Update the access token."""
        logger.info("Updating access token")
        self.access_token = access_token

    # -------------------------------------------------------------------------
    # Private helper methods
    # -------------------------------------------------------------------------

    async def _get_records_using_last_id(
        self,
        soql_format: str,
        fetch_all: bool,
        contains_filter_conditions: bool,
        checkpoint: Optional[SfIdentityCheckpointState],
        model_class: type[IdentityResponseBase],
        use_query_all: bool = False,
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[IdentityResponseBase]:
        """Get records using last ID pagination."""

        def processor(current_set, records, last_id, results):
            if last_id and current_set and last_id == current_set[0].Id:
                current_set.pop(0)

            results.extend(current_set)

            # Continue if not done or has next URL
            if (
                not records.get("done", True)
                or records.get("nextRecordsUrl")
            ) and current_set:
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
        current_set_processor: Callable,
        last_id_field_name: str,
        checkpoint: Optional[SfIdentityCheckpointState],
        model_class: type[IdentityResponseBase],
        use_query_all: bool = False,
        direction: RecordEnumerationDirection = RecordEnumerationDirection.ASCENDING,
    ) -> list[IdentityResponseBase]:
        """Get records using custom last ID field pagination."""
        last_id = checkpoint.LastRecordId if checkpoint else ""
        if checkpoint:
            checkpoint.NextUrl = ""

        results = []
        comparison_op = ">" if direction == RecordEnumerationDirection.ASCENDING else "<"

        while True:
            # Build WHERE clause for last_id
            if last_id:
                where_clause = (
                    f"{' AND ' if contains_filter_conditions else ' WHERE '}"
                    f"{last_id_field_name} {comparison_op} '{last_id}'"
                )
            else:
                where_clause = ""

            soql = soql_format.format(where_clause)
            logger.info(f"Executing SOQL: {soql}")

            # Execute query
            if use_query_all:
                response = await self._execute_query_all(soql)
            else:
                response = await self._execute_query(soql)

            if not response or not response.get("records"):
                logger.error("Empty response received during identity sync query")
                break

            logger.info(
                f"Got {response.get('totalSize', 0)} total, "
                f"{len(response['records'])} records, "
                f"done={response.get('done', False)}"
            )

            # Parse records
            current_set = self.response_processor.get(response, model_class)

            if current_set:
                new_last_id = current_set_processor(
                    current_set, response, last_id, results
                )

                if new_last_id and new_last_id == last_id:
                    logger.error(
                        f"New last_id {new_last_id} equals current last_id, stopping"
                    )
                    last_id = ""
                else:
                    last_id = new_last_id
            else:
                last_id = ""

            if not fetch_all or not last_id:
                break

        if checkpoint:
            checkpoint.LastRecordId = last_id
            checkpoint.Exhausted = not last_id

        return results

    async def _execute_query(self, soql: str) -> dict:
        """Execute a SOQL query."""
        # This would call the actual Salesforce client
        # For now, this is a placeholder that needs to be implemented
        # based on your Salesforce client interface
        return await self.salesforce_client.query(soql, self.access_token)

    async def _execute_query_all(self, soql: str) -> dict:
        """Execute a SOQL query using queryAll (includes deleted records)."""
        return await self.salesforce_client.query_all(soql, self.access_token)
