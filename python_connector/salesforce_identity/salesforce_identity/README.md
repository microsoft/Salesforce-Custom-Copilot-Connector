# Salesforce Identity Sync Module

This module provides identity and permissions management for Salesforce Graph Connector, ported from the C# `ClientHelperForIdentitySync` implementation.

## Overview

The `salesforce_identity` module handles:

- **Permission Sets**: Fetching permission set assignments for users
- **Users & Roles**: Retrieving user data with roles and permission hierarchies
- **Org-Wide Defaults**: Getting organization-level access control settings
- **Entity Shares**: Fetching share records for ACL building
- **Groups**: Managing Salesforce groups (Roles, Queues, Public Groups, etc.)
- **Object & Field Permissions**: Checking permissions at object and field levels

## Module Structure

```
salesforce_identity/
├── __init__.py                 # Public API exports
├── client_helper.py            # Main ClientHelperForIdentitySync class
├── models.py                   # Data models (User, Group, EntityShareBase, etc.)
├── queries.py                  # SOQL query templates
├── response_processor.py       # SOQL response parsing
└── README.md                   # This file
```

## Key Components

### ClientHelperForIdentitySync

Main class for fetching identity-related data from Salesforce.

```python
from salesforce_identity import ClientHelperForIdentitySync

helper = ClientHelperForIdentitySync(
    salesforce_client=sf_client,
    instance_url="https://yourinstance.salesforce.com",
    access_token="your_oauth_token"
)

# Get users with permission sets
users = await helper.get_users_for_content_ingestion(
    object_name="Account",
    filter_conditions="IsActive = true"
)

# Get org-wide defaults
org_defaults = await helper.get_org_wide_defaults_from_salesforce()
print(org_defaults.DefaultAccountAccess)  # EntityVisibility.NONE
```

### Models

Core data models matching Salesforce identity objects:

- **`User`**: Salesforce user with permissions, roles, and federation info
- **`Group`**: Salesforce group (Role, Queue, Public Group, etc.)
- **`PermissionSetAssignment`**: Permission set assignment to a user
- **`EntityShareBase`**: Base class for share records (AccountShare, etc.)
- **`EntityVisibility`**: Enum for org-wide default visibility (None, Read, Edit, etc.)
- **`Organization`**: Org-wide default settings
- **`ObjectPermissions`**: Object-level permissions
- **`FieldPermissions`**: Field-level security

### Queries

Pre-built SOQL query templates in `IdentitySyncQueries`:

```python
from salesforce_identity import IdentitySyncQueries

# Example: Users with permission sets
query = IdentitySyncQueries.UsersAndPermissionSetFormat.format(
    "Account",  # Object name
    ""          # Additional filter
)
```

### Response Processor

Parses SOQL JSON responses into typed Python dataclasses:

```python
from salesforce_identity import SalesforceIdentitySOQLResponseProcessor

processor = SalesforceIdentitySOQLResponseProcessor()
users = processor.get(soql_response, User)
```

## Usage Examples

### 1. Get Permission Sets for an Object

```python
permission_sets = await helper.get_permission_sets_from_salesforce(
    object_name="Account",
    filter_conditions="",
    fetch_all=True
)
```

### 2. Get Authorized Users and Groups

```python
authorized_users, sf_groups = await helper.get_authorized_users_and_groups_from_salesforce(
    user_ids=["005...", "005..."],
    group_ids=["00G...", "00G..."],
    salesforce_object_handler=account_handler,
    entity_visibility=EntityVisibility.NONE,
    frozen_users=set()
)
```

### 3. Get Entity Shares for ACL Building

```python
shares = await helper.get_shares_for_public_groups(
    object_name="Account",
    fetch_all=True
)

for share in shares:
    print(f"User/Group: {share.UserOrGroupId}, Type: {share.UserOrGroup.Type}")
```

### 4. Get User Role Hierarchy

```python
roles = await helper.get_user_role_hierarchy_from_salesforce()

for role in roles:
    print(f"{role.Name} (Parent: {role.ParentRoleId})")
```

### 5. Check Object Permissions

```python
obj_perms = await helper.get_object_permissions(
    object_name="Account",
    should_only_check_profile_for_fls=False
)

for perm in obj_perms:
    if perm.PermissionsRead:
        print(f"Profile/PermSet {perm.ParentId} can read {perm.SobjectType}")
```

## Integration with ACL Building

This module is designed to work with the ACL building logic in `handler.py`:

```python
from salesforce_identity import (
    ClientHelperForIdentitySync,
    EntityVisibility
)

# 1. Initialize helper
identity_helper = ClientHelperForIdentitySync(sf_client, instance_url, token)

# 2. Get org-wide defaults
entity_visibility_map = await identity_helper.get_org_wide_defaults_map()
account_visibility = entity_visibility_map["Account"]

# 3. Get authorized users for ACL
if account_visibility == EntityVisibility.NONE:
    # Private - need to fetch users/groups from shares
    authorized_users, groups = await identity_helper.get_authorized_users_and_groups_from_salesforce(
        user_ids=user_ids_from_shares,
        group_ids=group_ids_from_shares,
        salesforce_object_handler=handler,
        entity_visibility=account_visibility,
        frozen_users=frozen_user_set
    )
```

## Checkpointing

All fetch methods support checkpointing for resumable operations:

```python
from salesforce_identity import SfIdentityCheckpointState

checkpoint = SfIdentityCheckpointState(
    LastRecordId="",
    NextUrl="",
    Exhausted=False
)

users = await helper.get_users_from_salesforce(
    filter_conditions="Department = 'Sales'",
    checkpoint=checkpoint,
    fetch_all=True
)

# Save checkpoint.LastRecordId for resumption
print(f"Resume from: {checkpoint.LastRecordId}")
print(f"Exhausted: {checkpoint.Exhausted}")
```

## Constants

`SalesforceConstants` provides common constants:

```python
from salesforce_identity import SalesforceConstants

print(SalesforceConstants.SF_QUERY_BATCH_SIZE)  # 2000
print(SalesforceConstants.ACCOUNT)              # "Account"
print(SalesforceConstants.CONTACT)              # "Contact"
```

## Error Handling

The module expects the Salesforce client to handle errors. Network errors, timeouts, and API errors should be raised by the underlying client.

## Requirements

- Python 3.9+
- Async/await support
- Salesforce client with `query()` and `query_all()` methods

## Porting Notes

This module is a direct port of the C# implementation from:
- `Microsoft.Graph.Connectors.Salesforce.IdentitySync.ClientHelperForIdentitySync`
- `Microsoft.Graph.Connectors.Salesforce.IdentitySync.Models`
- `Microsoft.Graph.Connectors.Salesforce.IdentitySync.IdentitySyncQueries`

Key differences from C#:
- Uses Python `async`/`await` instead of C# `Task`
- Uses dataclasses instead of C# classes
- Uses Python `Enum` instead of C# enums
- Simplified error handling (delegates to client)

## Next Steps

To complete ACL integration:

1. Implement ACL building methods in `handler.py`
2. Add `AccessControlList` property to `SearchableItem` model
3. Create `AccessControlEntry` and `Principal` classes
4. Port `BuildAcls`, `BuildAclsForPrivateOrgWideDefault`, etc. from C#
