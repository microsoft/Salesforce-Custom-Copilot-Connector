"""
Identity Sync SOQL Queries

Mirrors IdentitySyncQueries.cs from C# implementation.
Contains all SOQL query templates for identity and permission operations.
"""


class IdentitySyncQueries:
    """SOQL query templates for identity sync operations."""

    # Permission Sets Query
    PermissionSetsQueryFormat = (
        "SELECT Id, Assignee.Name, Assignee.Id, Assignee.Alias, Assignee.Email, "
        "Assignee.FirstName, Assignee.LastName, Assignee.FederationIdentifier, "
        "Assignee.UserName, Assignee.IsActive, PermissionSet.Id, "
        "PermissionSet.IsOwnedByProfile, PermissionSet.Profile.Name, PermissionSet.Label "
        "FROM PermissionSetAssignment "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true) "
        "AND (NOT Assignee.Name Like '%User%'){1} ORDER BY Id asc"
    )

    # Permission Sets Query V2
    PermissionSetsQueryFormatV2 = (
        "SELECT Id, Assignee.Name, Assignee.Id, Assignee.Alias, Assignee.Email, "
        "Assignee.FirstName, Assignee.LastName, Assignee.FederationIdentifier, "
        "Assignee.UserName, Assignee.IsActive, PermissionSet.Id, "
        "PermissionSet.IsOwnedByProfile, PermissionSet.Profile.Name, PermissionSet.Label "
        "FROM PermissionSetAssignment "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true) "
        "AND Assignee.IsActive = True AND Assignee.UserType = 'Standard' "
        "AND (NOT Assignee.Name Like '%User%'){1} ORDER BY Id asc"
    )

    # Global Access Users Query
    GlobalAccessUsersQueryFormat = (
        "SELECT Id, Assignee.Name, Assignee.Id, Assignee.Alias, Assignee.Email, "
        "Assignee.FirstName, Assignee.LastName, Assignee.FederationIdentifier, "
        "Assignee.UserName, Assignee.IsActive, PermissionSet.Id, "
        "PermissionSet.IsOwnedByProfile, PermissionSet.Profile.Name, PermissionSet.Label "
        "FROM PermissionSetAssignment "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsViewAllRecords = true) "
        "AND Assignee.IsActive = True AND (NOT Assignee.Name Like '%User%'){1} ORDER BY Id asc"
    )

    # Org Wide Defaults Query
    OrgWideDefaultQuery = (
        "SELECT DefaultAccountAccess, DefaultContactAccess, DefaultOpportunityAccess, "
        "DefaultLeadAccess, DefaultCampaignAccess, DefaultCaseAccess from Organization"
    )

    # Shares Query for Groups Format
    SharesQueryForGroupsFormat = (
        "SELECT UserOrGroupId from {0}Share Where UserOrGroup.Type = 'Queue'{1} "
        "GROUP BY UserOrGroupId ORDER BY UserOrGroupId asc Limit {2}"
    )

    # Shares Query for Groups Sequential Format
    SharesQueryForGroupsSequentialFormat = (
        "SELECT UserOrGroupId from {0}Share Where UserOrGroup.Type = 'Queue'{1} "
        "ORDER BY UserOrGroupId asc Limit {2}"
    )

    # Shares from Records
    SharesFromRecords = (
        "SELECT Id, (SELECT UserOrGroupId from Shares WHERE UserOrGroup.Type = 'Queue') "
        "from {0}{1} ORDER BY Id asc"
    )

    # All Shares from Records
    AllSharesFromRecords = (
        "SELECT Id, IsDeleted, (SELECT UserOrGroupId, UserOrGroup.Type from Shares) "
        "from {0}{1} ORDER BY Id {2}"
    )

    # Groups Query Format (with nested members)
    GroupsQueryFormat = (
        "SELECT Id, DoesIncludeBosses, Type, RelatedId, "
        "(SELECT UserOrGroupId from GroupMembers) from Group{0} ORDER BY Id asc"
    )

    # Group Members Query Format
    GroupMembersQueryFormat = (
        "SELECT Id, UserOrGroupId from GroupMember{0} ORDER BY Id asc"
    )

    # Group Type and Related ID Query
    GroupTypeAndRelatedIdQuery = (
        "SELECT Id, Type, RelatedId, DoesIncludeBosses, "
        "(SELECT UserOrGroupId from GroupMembers Limit 1) from Group{0} ORDER BY Id asc"
    )

    # Shares Query for Content Sync
    SharesQueryForContentSync = (
        "SELECT UserOrGroupId, UserOrGroup.Type from Shares"
    )

    # User Role Query
    UserRoleQuery = (
        "SELECT Id, ParentRoleId, ContactAccessForAccountOwner, OpportunityAccessForAccountOwner "
        "FROM UserRole{0} ORDER BY Id asc"
    )

    # User Roles Assigned to Users Query
    UserRolesAssignedToUsersQuery = (
        "SELECT UserRoleId from User WHERE UserRoleId != null AND IsActive = True "
        "AND (NOT Name Like '%User%'){0} GROUP BY UserRoleId ORDER BY UserRoleId asc Limit {1}"
    )

    # User Roles Assigned to Users Query V2
    UserRolesAssignedToUsersQueryV2 = (
        "SELECT UserRoleId from User WHERE UserRoleId != null AND IsActive = True "
        "AND UserType = 'Standard' AND (NOT Name Like '%User%'){0} "
        "GROUP BY UserRoleId ORDER BY UserRoleId asc Limit {1}"
    )

    # Users Query Format
    UsersQueryFormat = (
        "SELECT Id, Name, Alias, Email, FederationIdentifier, FirstName, LastName, "
        "UserName, UserRoleId, UserRole.ParentRoleId, IsActive "
        "FROM User WHERE (NOT Name Like '%User%'){0} ORDER BY Id asc{1}"
    )

    # User and Manager Query
    UserAndMangerQuery = (
        "SELECT Id, ManagerId from User{0} ORDER BY Id asc"
    )

    # User Login Query
    UserLoginQuery = (
        "SELECT Id, UserId FROM UserLogin Where IsFrozen = True{0} ORDER BY Id asc"
    )

    # Users Query for Content Ingestion Format
    UsersQueryForContentIngestionFormat = (
        "SELECT Id, Name, Alias, Email, FederationIdentifier, FirstName, LastName, "
        "UserName, UserRoleId, UserRole.ParentRoleId, "
        "(SELECT PermissionSet.Id, PermissionSet.IsOwnedByProfile, PermissionSet.Profile.Name, PermissionSet.Label "
        "FROM PermissionSetAssignments "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true)) "
        "from User WHERE IsActive = True AND (NOT Name Like '%User%'){1} ORDER BY Id asc"
    )

    # Users and Permission Set Format
    UsersAndPermissionSetFormat = (
        "SELECT Id, Name, Alias, Email, FederationIdentifier, FirstName, LastName, "
        "UserName, UserRoleId, UserRole.ParentRoleId, IsActive, UserType, "
        "(SELECT PermissionSet.Id FROM PermissionSetAssignments "
        "WHERE PermissionSetId IN (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true)) "
        "from User WHERE (NOT Name Like '%User%'){1} ORDER BY Id asc"
    )

    # User and Role Format
    UserAndRoleFormat = (
        "SELECT Id, UserRoleId from User WHERE (NOT Name Like '%User%'){0} ORDER BY Id asc"
    )

    # Object Permissions Query Format
    ObjectPermissionsQueryFormat = (
        "SELECT ParentId FROM ObjectPermissions "
        "WHERE SObjectType = '{0}' and PermissionsRead = True "
        "and ParentId in (SELECT PermissionSetId from PermissionSetAssignment){1} "
        "GROUP BY ParentId ORDER BY ParentId asc Limit {2}"
    )

    # Object Permissions Only Profiles Query Format
    ObjectPermissionsOnlyProfilesQueryFormat = (
        "SELECT ParentId FROM ObjectPermissions "
        "WHERE SObjectType = '{0}' and PermissionsRead = True "
        "and Parent.IsOwnedByProfile = True "
        "and ParentId in (SELECT PermissionSetId from PermissionSetAssignment){1} "
        "GROUP BY ParentId ORDER BY ParentId asc Limit {2}"
    )

    # Field Permissions Query Format
    FieldPermissionsQueryFormat = (
        "SELECT Id, Field, ParentId FROM FieldPermissions "
        "WHERE SObjectType = '{0}' and Field In ({1}) "
        "and ParentId in (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true) "
        "and ParentId in (SELECT PermissionSetId from PermissionSetAssignment){2} ORDER BY Id"
    )

    # Field Permissions Only Profiles Query Format
    FieldPermissionsOnlyProfilesQueryFormat = (
        "SELECT Id, Field, ParentId FROM FieldPermissions "
        "WHERE SObjectType = '{0}' and Field In ({1}) "
        "and Parent.IsOwnedByProfile = True "
        "and ParentId in (Select ParentId FROM ObjectPermissions WHERE SObjectType = '{0}' AND PermissionsRead = true and Parent.IsOwnedByProfile = true) "
        "and ParentId in (SELECT PermissionSetId from PermissionSetAssignment){2} ORDER BY Id"
    )
