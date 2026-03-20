"""
Mock Identity Sync Client for Testing ACL/Permissions Logic

This module provides a mock identity sync client that returns pre-defined
permission and user data for testing ACL building logic without requiring
live Salesforce identity sync operations.

Usage:
    from tests.mock_identity_sync_client import MockIdentitySyncClient
    
    client = MockIdentitySyncClient()
    users = client.get_authorized_users("Account")
    org_defaults = client.get_org_wide_defaults()
    # Returns mock permission data ready for ACL building
"""

from __future__ import annotations

from typing import Any, Optional

from mock_data.permissions import (
    build_frozen_user,
    build_group,
    build_group_member,
    build_org_defaults_map,
    build_org_defaults_response,
    build_share,
    build_user,
    build_user_role,
)
from mock_data.common import (
    OWNER_USER_ID,
    SHARED_USER_ID,
    OWNER_USERNAME,
    SHARED_USERNAME,
    OWNER_NAME,
    SHARED_NAME,
    PUBLIC_GROUP_ID,
    ROLE_ID,
    OWNER_GUID,
    SHARED_GUID,
)


class MockIdentitySyncClient:
    """
    Mock identity sync client that returns predefined permission/user data.
    
    Simulates Salesforce identity sync for testing ACL logic without live connections.
    """
    
    def __init__(self):
        """Initialize mock identity sync client with default permission data."""
        self._setup_default_users()
        self._setup_default_groups()
        self._setup_default_roles()
        self._setup_default_org_defaults()
    
    def _setup_default_users(self):
        """Setup default user data."""
        self.users = {
            OWNER_USER_ID: build_user(
                OWNER_USER_ID,
                name=OWNER_NAME,
                email=OWNER_USERNAME,
                username=OWNER_USERNAME,
                federation_identifier=OWNER_GUID,
                role_id=ROLE_ID,
            ),
            SHARED_USER_ID: build_user(
                SHARED_USER_ID,
                name=SHARED_NAME,
                email=SHARED_USERNAME,
                username=SHARED_USERNAME,
                federation_identifier=SHARED_GUID,
                role_id=ROLE_ID,
            ),
        }
        
        # Add to dict format for handler compatibility
        self.users_dict = {
            uid: {
                "Id": user.Id,
                "Name": user.Name,
                "Email": user.Email,
                "UserName": user.UserName,
                "FederationIdentifier": user.FederationIdentifier,
                "IsActive": user.IsActive,
                "IsFrozen": user.IsFrozen,
                "UserRoleId": user.UserRoleId,
                "ManagerId": user.ManagerId,
                "Alias": getattr(user, "Alias", f"alias{uid[-3:]}"),
                "FirstName": user.Name.split()[0] if user.Name else "",
                "LastName": user.Name.split()[-1] if user.Name else "",
                "PermissionSets": [{"Id": f"0PS{uid[-9:]}"}],  # Mock permission set
                "UserRole": {
                    "Id": user.UserRoleId,
                    "ParentRoleId": ROLE_ID if user.UserRoleId != ROLE_ID else None,
                } if user.UserRoleId else None,
            }
            for uid, user in self.users.items()
        }
    
    def _setup_default_groups(self):
        """Setup default group data."""
        self.groups = {
            PUBLIC_GROUP_ID: build_group(
                group_id=PUBLIC_GROUP_ID,
                group_type="Group",  # Public group
            ),
        }
        
        # Add dict format
        self.groups_dict = {
            gid: {
                "Id": group.Id,
                "Name": group.Name,
                "Type": group.Type,
                "RelatedId": group.RelatedId,
                "DoesIncludeBosses": group.DoesIncludeBosses,
            }
            for gid, group in self.groups.items()
        }
    
    def _setup_default_roles(self):
        """Setup default role data."""
        self.roles = {
            ROLE_ID: build_user_role(
                role_id=ROLE_ID,
                parent_role_id=None,  # Top-level role
            ),
        }
    
    def _setup_default_org_defaults(self):
        """Setup default org-wide defaults."""
        self.org_defaults_map = build_org_defaults_map()
        self.org_defaults_response = build_org_defaults_response()
    
    def get_authorized_users(
        self,
        object_name: str,
        include_permission_sets: bool = True,
    ) -> dict[str, dict]:
        """
        Get authorized users for a specific Salesforce object.
        
        Args:
            object_name: Salesforce object name (e.g., "Account", "Contact")
            include_permission_sets: Include permission set data
        
        Returns:
            Dictionary mapping user ID to user dict
        """
        # In a real scenario, this would filter based on object permissions
        # For mock data, we return all users
        return self.users_dict.copy()
    
    def get_authorized_users_for_objects(
        self,
        object_names: list[str],
    ) -> dict[str, dict[str, dict]]:
        """
        Get authorized users for multiple objects.
        
        Args:
            object_names: List of Salesforce object names
        
        Returns:
            Dictionary mapping object name to user dictionary
        """
        return {
            obj_name: self.get_authorized_users(obj_name)
            for obj_name in object_names
        }
    
    def get_groups(self, group_ids: Optional[list[str]] = None) -> dict[str, dict]:
        """
        Get Salesforce groups by IDs.
        
        Args:
            group_ids: List of group IDs to retrieve. If None, returns all.
        
        Returns:
            Dictionary mapping group ID to group dict
        """
        if group_ids is None:
            return self.groups_dict.copy()
        
        return {
            gid: self.groups_dict[gid]
            for gid in group_ids
            if gid in self.groups_dict
        }
    
    def get_org_wide_defaults(self) -> dict[str, str]:
        """
        Get org-wide default sharing settings for all objects.
        
        Returns:
            Dictionary mapping object name to EntityVisibility value
        """
        return self.org_defaults_map.copy()
    
    def get_org_wide_default(self, object_name: str) -> str:
        """
        Get org-wide default for a specific object.
        
        Args:
            object_name: Salesforce object name
        
        Returns:
            EntityVisibility value (e.g., "None", "Read", "Edit")
        """
        return self.org_defaults_map.get(object_name, "None")
    
    def get_shares_for_record(
        self,
        record_id: str,
        object_name: str,
    ) -> list[dict]:
        """
        Get share records for a specific Salesforce record.
        
        Args:
            record_id: Salesforce record ID
            object_name: Object type (e.g., "Account")
        
        Returns:
            List of share dicts
        """
        # Return mock shares - in real scenario would query based on record_id
        share1 = build_share(
            share_id=f"00r{record_id[-12:]}1",
            user_or_group_id=OWNER_USER_ID,
            principal_type="User",
        )
        share2 = build_share(
            share_id=f"00r{record_id[-12:]}2",
            user_or_group_id=SHARED_USER_ID,
            principal_type="User",
        )
        
        return [
            {
                "Id": share1.Id,
                "UserOrGroupId": share1.UserOrGroupId,
                "RowCause": share1.RowCause,
                "UserOrGroup": {
                    "Type": share1.UserOrGroup.Type if share1.UserOrGroup else None,
                },
            },
            {
                "Id": share2.Id,
                "UserOrGroupId": share2.UserOrGroupId,
                "RowCause": share2.RowCause,
                "UserOrGroup": {
                    "Type": share2.UserOrGroup.Type if share2.UserOrGroup else None,
                },
            },
        ]
    
    def add_user(
        self,
        user_id: str,
        name: str,
        email: str,
        federation_identifier: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Add a custom user to mock data.
        
        Args:
            user_id: Salesforce user ID
            name: User's full name
            email: User's email
            federation_identifier: Azure AD federation ID
            **kwargs: Additional user attributes
        """
        user = build_user(
            user_id,
            name=name,
            email=email,
            username=email,
            federation_identifier=federation_identifier,
            **kwargs,
        )
        self.users[user_id] = user
        self.users_dict[user_id] = {
            "Id": user.Id,
            "Name": user.Name,
            "Email": user.Email,
            "UserName": user.UserName,
            "FederationIdentifier": user.FederationIdentifier,
            "IsActive": user.IsActive,
            "IsFrozen": user.IsFrozen,
            "UserRoleId": user.UserRoleId,
            "ManagerId": user.ManagerId,
            "Alias": kwargs.get("alias", f"alias{user_id[-3:]}"),
            "FirstName": name.split()[0] if name else "",
            "LastName": name.split()[-1] if name else "",
            "PermissionSets": [{"Id": f"0PS{user_id[-9:]}"}],
            "UserRole": {
                "Id": user.UserRoleId,
                "ParentRoleId": ROLE_ID if user.UserRoleId != ROLE_ID else None,
            } if user.UserRoleId else None,
        }
    
    def add_group(
        self,
        group_id: str,
        group_type: str = "Group",
        related_id: Optional[str] = None,
    ) -> None:
        """
        Add a custom group to mock data.
        
        Args:
            group_id: Salesforce group ID
            group_type: Group type (e.g., "Role", "Queue", "Group")
            related_id: Related role/user ID for certain group types
        """
        group = build_group(
            group_id=group_id,
            group_type=group_type,
            related_id=related_id,
        )
        self.groups[group_id] = group
        self.groups_dict[group_id] = {
            "Id": group.Id,
            "Name": group.Name,
            "Type": group.Type,
            "RelatedId": group.RelatedId,
            "DoesIncludeBosses": group.DoesIncludeBosses,
        }
    
    def set_org_wide_default(self, object_name: str, visibility: str) -> None:
        """
        Set org-wide default for an object.
        
        Args:
            object_name: Salesforce object name
            visibility: EntityVisibility value
        """
        self.org_defaults_map[object_name] = visibility


def get_mock_acl_data_bundle(
    object_name: str = "Account",
    entity_visibility: str = "None",
) -> dict[str, Any]:
    """
    Get a complete bundle of mock data for ACL testing.
    
    Args:
        object_name: Salesforce object type
        entity_visibility: Org-wide default visibility
    
    Returns:
        Dictionary with all necessary ACL data
    
    Example:
        >>> bundle = get_mock_acl_data_bundle("Account", "None")
        >>> 
        >>> # Use with handler
        >>> items = handler.construct_ingestion_items(
        ...     query_result,
        ...     instance_url,
        ...     schema_properties,
        ...     entity_visibility=bundle["entity_visibility"],
        ...     authorized_users_for_sf_objects=bundle["authorized_users"],
        ...     sf_groups=bundle["sf_groups"],
        ... )
    """
    client = MockIdentitySyncClient()
    
    # Get authorized users and convert from dict to list
    authorized_users_dict = client.get_authorized_users(object_name)
    authorized_users_list = list(authorized_users_dict.values())
    
    return {
        "entity_visibility": entity_visibility,
        "authorized_users": {
            object_name: authorized_users_list  # Convert to list
        },
        "sf_groups": client.get_groups(),
        "org_defaults": client.get_org_wide_defaults(),
    }


__all__ = [
    "MockIdentitySyncClient",
    "get_mock_acl_data_bundle",
]
