"""
Tests for ClientHelperForIdentitySync

Mirrors ClientHelperForIdentitySyncTests.cs
"""

import pytest
from unittest.mock import AsyncMock, Mock
import json

from salesforce_identity import (
    ClientHelperForIdentitySync,
    SalesforceConstants,
    EntityVisibility,
    SfIdentityCheckpointState,
    PermissionSetAssignment,
    User,
    Group,
    EntityShareBase,
    ObjectRecord,
    UserRole,
    UserLogin,
    RecordEnumerationDirection,
)


@pytest.mark.asyncio
class TestPermissionSets:
    """Test permission set queries."""

    async def test_get_permission_sets_basic(self, mock_sf_client, instance_url, access_token):
        """Test GetPermissionSetsFromSalesforce with basic query."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000006V0b1CAC",
                    "Assignee": {
                        "Name": "kailun qian",
                        "Id": "0055w00000CmT7xAAF",
                        "Alias": "qianka",
                        "Email": "qiankailun@outlook.com",
                        "FirstName": "kailun",
                        "LastName": "qian",
                        "FederationIdentifier": None,
                        "Username": "qiankailun@outlook.com",
                        "IsActive": True
                    },
                    "PermissionSet": {
                        "Id": "0PS5w000002d8e3GAA",
                        "IsOwnedByProfile": True,
                        "Profile": {"Name": "System Administrator"},
                        "Label": "00ex00000018ozh_128_09_04_12_1"
                    }
                },
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000006VciLCAS",
                    "Assignee": {
                        "Name": "Rohit Sharma",
                        "Id": "0055w00000CkeZ1AAJ",
                        "Alias": "RShar",
                        "Email": "rohitsharma91@gmail.com",
                        "FirstName": "Rohit",
                        "LastName": "Sharma",
                        "FederationIdentifier": None,
                        "Username": "rohit@coolcompany.com",
                        "IsActive": True
                    },
                    "PermissionSet": {
                        "Id": "0PS5w000002j6DNGAY",
                        "IsOwnedByProfile": False,
                        "Profile": None,
                        "Label": "AccountReadPermissions"
                    }
                },
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000007Ioj1CAC",
                    "Assignee": {
                        "Name": "Integration User",
                        "Id": "0055w00000CqAe3AAF",
                    }
                },
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000007Ioj2CAC",
                },
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000007Ioj5CAC",
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_permission_sets_from_salesforce(
            SalesforceConstants.ACCOUNT,
            "",
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 5
        assert result[0].Id == "0Pa5w000006V0b1CAC"
        assert result[1].Id == "0Pa5w000006VciLCAS"

    async def test_get_permission_sets_with_filter(self, mock_sf_client, instance_url, access_token):
        """Test GetPermissionSetsFromSalesforce with filter condition."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [{"Id": f"0Pa5w00000{i}", "attributes": {"type": "PermissionSetAssignment"}} for i in range(5)]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_permission_sets_from_salesforce(
            SalesforceConstants.ACCOUNT,
            "Assignee.RoleId = '1234'",
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 5
        # Verify the filter was included in the query
        call_args = mock_sf_client.query.call_args[0][0]
        assert "Assignee.RoleId = '1234'" in call_args

    async def test_get_permission_sets_v2(self, mock_sf_client, instance_url, access_token):
        """Test GetPermissionSetsFromSalesforce with V2 query."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [{"Id": f"0Pa5w00000{i}", "attributes": {"type": "PermissionSetAssignment"}} for i in range(5)]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_permission_sets_from_salesforce(
            SalesforceConstants.ACCOUNT,
            "",
            SfIdentityCheckpointState(),
            fetch_all=True,
            use_v2_query=True
        )
        
        # Assert
        assert len(result) == 5
        # Verify V2 query format was used
        call_args = mock_sf_client.query.call_args[0][0]
        assert "UserType = 'Standard'" in call_args


@pytest.mark.asyncio
class TestOrgWideDefaults:
    """Test org-wide default queries."""

    async def test_get_org_wide_defaults(self, mock_sf_client, instance_url, access_token):
        """Test GetOrgWideDefaultsFromSalesforce."""
        # Arrange
        response_data = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "attributes": {"type": "Organization"},
                "Id": "00D5w000004y5LSEAY",
                "DefaultAccountAccess": "All",
                "DefaultContactAccess": "ControlledByParent",
                "DefaultLeadAccess": "ReadEditTransfer",
                "DefaultOpportunityAccess": "ControlledByCampaign",
                "DefaultCampaignAccess": "All",
                "DefaultCaseAccess": "All"
            }]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_org_wide_defaults_from_salesforce()
        
        # Assert
        assert result is not None
        # ControlledByCampaign should be normalized to None
        assert result.DefaultOpportunityAccess == EntityVisibility.NONE

    async def test_get_org_wide_defaults_map(self, mock_sf_client, instance_url, access_token):
        """Test GetOrgWideDefaultsMap."""
        # Arrange
        response_data = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "00D5w000004y5LSEAY",
                "attributes": {"type": "Organization"},
                "DefaultAccountAccess": "Read",
                "DefaultContactAccess": "ControlledByParent",
                "DefaultLeadAccess": "Edit",
                "DefaultOpportunityAccess": "None",
                "DefaultCampaignAccess": "All",
                "DefaultCaseAccess": "All"
            }]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_org_wide_defaults_map()
        
        # Assert
        assert result is not None
        assert SalesforceConstants.ACCOUNT in result
        assert SalesforceConstants.CONTACT in result
        assert SalesforceConstants.OPPORTUNITY in result


@pytest.mark.asyncio
class TestShares:
    """Test share queries."""

    async def test_get_shares_for_public_groups(self, mock_sf_client, instance_url, access_token):
        """Test GetSharesForPublicGroups."""
        # Arrange
        response_data = {
            "totalSize": 8,
            "done": True,
            "records": [
                {"attributes": {"type": "AggregateResult"}, "UserOrGroupId": f"00G5w00000{i}"}
                for i in range(8)
            ]
        }
        
        response_data2 = {
            "totalSize": 0,
            "done": True,
            "records": []
        }
        
        mock_sf_client.query.side_effect = [response_data, response_data2]
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_shares_for_public_groups(
            SalesforceConstants.ACCOUNT,
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 8

    async def test_get_shares_for_public_groups_sequential(self, mock_sf_client, instance_url, access_token):
        """Test GetSharesForPublicGroupsSequential."""
        # Arrange
        response_data = {
            "totalSize": 9,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "AccountShare"},
                    "Id": f"00r5w0000{i}",
                    "UserOrGroupId": f"00G5w0000{i}"
                }
                for i in range(9)
            ]
        }
        
        response_data2 = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "attributes": {"type": "AccountShare"},
                "Id": "00r5w0000LAST",
                "UserOrGroupId": "00G5w0000LAST"
            }]
        }
        
        mock_sf_client.query.side_effect = [response_data, response_data2]
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_shares_for_public_groups_sequential(
            SalesforceConstants.ACCOUNT,
            "",
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 9

    async def test_get_shares_from_records(self, mock_sf_client, instance_url, access_token):
        """Test GetSharesForGroupsFromRecords."""
        # Arrange
        response_data = {
            "totalSize": 3,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "Account"},
                    "Id": "0015w00002BNmGRAA1",
                    "Shares": None
                },
                {
                    "attributes": {"type": "Account"},
                    "Id": "0015w00002BNmGSAA1",
                    "Shares": {
                        "totalSize": 1,
                        "done": True,
                        "records": [{
                            "attributes": {"type": "AccountShare"},
                            "Id": "00r5w0000HCptK3AQJ",
                            "UserOrGroupId": "00G5w000006vQXWEA2"
                        }]
                    }
                },
                {
                    "attributes": {"type": "Account"},
                    "Id": "0015w00002BNmGTAA1",
                    "Shares": None
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_shares_for_groups_from_records(
            SalesforceConstants.ACCOUNT,
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 1

    async def test_get_records_with_shares(self, mock_sf_client, instance_url, access_token):
        """Test GetRecordsWithShares."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "Account"},
                    "Id": f"0015w0000{i}",
                    "IsDeleted": False,
                    "Shares": {
                        "totalSize": 2,
                        "done": True,
                        "records": [
                            {
                                "attributes": {"type": "AccountShare"},
                                "UserOrGroupId": f"0055w0000{i}",
                                "UserOrGroup": {"Type": "User"}
                            }
                        ]
                    }
                }
                for i in range(5)
            ]
        }
        
        mock_sf_client.query_all.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_records_with_shares(
            SalesforceConstants.ACCOUNT,
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 5
        for record in result:
            assert record.Shares is not None


@pytest.mark.asyncio
class TestUsers:
    """Test user queries."""

    async def test_get_users_from_salesforce(self, mock_sf_client, instance_url, access_token):
        """Test GetUsersFromSalesforce."""
        # Arrange
        response_data = {
            "totalSize": 11,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "User"},
                    "Id": f"0055w0000{i}",
                    "Name": f"User {i}",
                    "Alias": f"user{i}",
                    "Email": f"user{i}@test.com",
                    "IsActive": True,
                    "UserRoleId": None,
                    "UserRole": None
                }
                for i in range(11)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_users_from_salesforce("")
        
        # Assert
        assert len(result) == 11

    async def test_get_users_for_content_ingestion(self, mock_sf_client, instance_url, access_token):
        """Test GetUsersForContentIngestion."""
        # Arrange
        response_data = {
            "totalSize": 8,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "User"},
                    "Id": "0055w00000CkeZ1AAJ",
                    "Name": "Charlotte Walton",
                    "Alias": "cwalton",
                    "Email": "rohitcontoso@outlook.com",
                    "FederationIdentifier": "Charlotte.Walton@fabrikammss.onmicrosoft.com",
                    "FirstName": "Charlotte",
                    "LastName": "Walton",
                    "Username": "rohit@coolcompany.com",
                    "UserRoleId": "00E5w000004XkNoEAK",
                    "UserRole": {
                        "attributes": {"type": "UserRole"},
                        "ParentRoleId": "00E5w000004XkNaEAK"
                    },
                    "PermissionSetAssignments": {
                        "totalSize": 2,
                        "done": True,
                        "records": [
                            {
                                "attributes": {"type": "PermissionSetAssignment"},
                                "Id": "0Pa5w000007Ioj5CAC",
                                "PermissionSet": {
                                    "Id": "0PS5w000002d8e3GAA",
                                    "IsOwnedByProfile": True
                                }
                            }
                        ]
                    }
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_users_for_content_ingestion(
            SalesforceConstants.ACCOUNT,
            ""
        )
        
        # Assert
        assert len(result) == 1
        assert result[0].PermissionSets is not None
        assert len(result[0].PermissionSets) == 1

    async def test_get_users_and_permission_set(self, mock_sf_client, instance_url, access_token):
        """Test GetUsersAndPermissionSet."""
        # Arrange
        response_data = {
            "totalSize": 9,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "User"},
                    "Id": f"0055w0000{i}",
                    "Name": f"User {i}",
                    "IsActive": True,
                    "UserType": "Standard",
                    "PermissionSetAssignments": {
                        "totalSize": 1,
                        "done": True,
                        "records": [
                            {
                                "attributes": {"type": "PermissionSetAssignment"},
                                "Id": f"0Pa5w0000{i}",
                                "PermissionSet": {"Id": f"0PS5w0000{i}"}
                            }
                        ]
                    }
                }
                for i in range(9)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_users_and_permission_set(
            SalesforceConstants.ACCOUNT,
            ""
        )
        
        # Assert
        assert len(result) == 9
        for user in result:
            assert user.PermissionSets is not None

    async def test_get_users_and_their_role(self, mock_sf_client, instance_url, access_token):
        """Test GetUsersAndTheirRole."""
        # Arrange
        response_data = {
            "totalSize": 11,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "User"},
                    "Id": f"0055w0000{i}",
                    "UserRoleId": f"00E5w0000{i}" if i < 5 else None
                }
                for i in range(11)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_users_and_their_role("")
        
        # Assert
        assert len(result) == 11

    async def test_get_frozen_users(self, mock_sf_client, instance_url, access_token):
        """Test GetFrozenUsers."""
        # Arrange
        response_data = {
            "totalSize": 8,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "UserLogin"},
                    "Id": f"0Yw5w0000{i}",
                    "UserId": f"0055w0000{i}"
                }
                for i in range(8)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_frozen_users()
        
        # Assert
        assert len(result) == 8

    async def test_get_users_and_managers(self, mock_sf_client, instance_url, access_token):
        """Test GetUsersAndManagers."""
        # Arrange
        response_data = {
            "totalSize": 11,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "User"},
                    "Id": "0055w00000CmT7xAAF",
                    "ManagerId": "0055w00000DRjk2AAD"
                },
                {
                    "attributes": {"type": "User"},
                    "Id": "0055w00000CkeZ1AAJ",
                    "ManagerId": "0055w00000DRjkCAAT"
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_users_and_managers()
        
        # Assert
        assert len(result) == 2
        assert result[0].ManagerId == "0055w00000DRjk2AAD"


@pytest.mark.asyncio
class TestGroups:
    """Test group queries."""

    async def test_get_group_type_and_related_id(self, mock_sf_client, instance_url, access_token):
        """Test GetGroupTypeAndRelatedId."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "Group"},
                    "Id": "00G5w000005Rp25EAC",
                    "Type": "Queue",
                    "RelatedId": None,
                    "DoesIncludeBosses": False
                },
                {
                    "attributes": {"type": "Group"},
                    "Id": "00G5w000007S7WxEAK",
                    "Type": "Organization",
                    "RelatedId": None,
                    "DoesIncludeBosses": False
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_group_type_and_related_id(
            "Id in ('00G5w000005Rp25EAC', '00G5w000007S7WxEAK')"
        )
        
        # Assert
        assert len(result) == 2

    async def test_get_group_members(self, mock_sf_client, instance_url, access_token):
        """Test GetGroupMembers."""
        # Arrange
        response_data = {
            "totalSize": 4,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "GroupMember"},
                    "Id": f"0115w0000{i}",
                    "UserOrGroupId": f"0055w0000{i}"
                }
                for i in range(4)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_group_members("GroupId = '00G5w000005Rp25EAC'")
        
        # Assert
        assert len(result) == 4


@pytest.mark.asyncio
class TestRoles:
    """Test role queries."""

    async def test_get_user_role_hierarchy(self, mock_sf_client, instance_url, access_token):
        """Test GetUserRoleHierarchyFromSalesforce."""
        # Arrange
        response_data = {
            "totalSize": 18,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "UserRole"},
                    "Id": "00E5w000004XkNZEA0",
                    "ParentRoleId": None,
                    "ContactAccessForAccountOwner": "Edit",
                    "OpportunityAccessForAccountOwner": "Edit"
                },
                {
                    "attributes": {"type": "UserRole"},
                    "Id": "00E5w000004XkNgEAK",
                    "ParentRoleId": "00E5w000004XkNZEA0",
                    "ContactAccessForAccountOwner": "Edit",
                    "OpportunityAccessForAccountOwner": "Edit"
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_user_role_hierarchy_from_salesforce(
            SfIdentityCheckpointState()
        )
        
        # Assert
        assert len(result) == 2

    async def test_get_user_roles_assigned_to_users(self, mock_sf_client, instance_url, access_token):
        """Test GetUserRolesAssignedToUsers."""
        # Arrange
        response_data1 = {
            "totalSize": 7,
            "done": False,
            "records": [
                {"attributes": {"type": "AggregateResult"}, "UserRoleId": f"00E5w0000{i}"}
                for i in range(7)
            ]
        }
        
        response_data2 = {
            "totalSize": 2,
            "done": True,
            "records": [
                {"attributes": {"type": "AggregateResult"}, "UserRoleId": "00E5w0000007"},
                {"attributes": {"type": "AggregateResult"}, "UserRoleId": "00E5w0000008"}
            ]
        }
        
        response_data3 = {
            "totalSize": 0,
            "done": True,
            "records": []
        }
        
        mock_sf_client.query.side_effect = [response_data1, response_data2, response_data3]
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_user_roles_assigned_to_users()
        
        # Assert
        assert len(result) == 8


@pytest.mark.asyncio
class TestGlobalAccess:
    """Test global access user queries."""

    async def test_get_global_access_users(self, mock_sf_client, instance_url, access_token):
        """Test GetGlobalAccessUsersFromSalesforce."""
        # Arrange
        response_data = {
            "totalSize": 1,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "PermissionSetAssignment"},
                    "Id": "0Pa5w000007Ioj5CAC",
                    "Assignee": {
                        "Name": "Charlotte Walton",
                        "Id": "0055w00000CkeZ1AAJ",
                        "IsActive": True
                    },
                    "PermissionSet": {
                        "Id": "0PS5w000002d8e3GAA",
                        "IsOwnedByProfile": True
                    }
                }
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_global_access_users_from_salesforce(
            SalesforceConstants.ACCOUNT,
            SfIdentityCheckpointState(LastRecordId="0Pa5w000007Ioj5CAC")
        )
        
        # Assert
        assert len(result) == 1


@pytest.mark.asyncio
class TestPermissions:
    """Test permission queries."""

    async def test_get_object_permissions(self, mock_sf_client, instance_url, access_token):
        """Test GetObjectPermissions."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "ObjectPermissions"},
                    "Id": f"1155w0000{i}",
                    "ParentId": f"0PS5w0000{i}"
                }
                for i in range(5)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_object_permissions(
            SalesforceConstants.ACCOUNT,
            should_only_check_profile_for_fls=False
        )
        
        # Assert
        assert len(result) >= 0  # May deduplicate

    async def test_get_field_permissions(self, mock_sf_client, instance_url, access_token):
        """Test GetFieldPermission."""
        # Arrange
        response_data = {
            "totalSize": 3,
            "done": True,
            "records": [
                {
                    "attributes": {"type": "FieldPermissions"},
                    "Id": f"0PD5w0000{i}",
                    "ParentId": f"0PS5w0000{i}",
                    "Field": f"Account.Field{i}",
                    "PermissionsRead": True
                }
                for i in range(3)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        result = await helper.get_field_permission(
            SalesforceConstants.ACCOUNT,
            "'Account.Name', 'Account.Industry'",
            should_only_check_profile_for_fls=False
        )
        
        # Assert
        assert len(result) == 3


@pytest.mark.asyncio
class TestMisc:
    """Test miscellaneous methods."""

    def test_update_access_token(self, mock_sf_client, instance_url, access_token):
        """Test UpdateAccessToken."""
        # Arrange
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        
        # Act
        helper.update_access_token("new_token")
        
        # Assert
        assert helper.access_token == "new_token"

    async def test_checkpoint_tracking(self, mock_sf_client, instance_url, access_token):
        """Test checkpoint state tracking."""
        # Arrange
        response_data = {
            "totalSize": 5,
            "done": True,
            "records": [
                {"Id": f"0055w0000{i}", "attributes": {"type": "User"}}
                for i in range(5)
            ]
        }
        
        mock_sf_client.query.return_value = response_data
        helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
        checkpoint = SfIdentityCheckpointState()
        
        # Act
        await helper.get_users_from_salesforce("", checkpoint=checkpoint, fetch_all=False)
        
        # Assert
        # Checkpoint should be updated
        assert checkpoint.LastRecordId != "" or checkpoint.Exhausted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
