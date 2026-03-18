"""
Salesforce Identity Sync Module

This module handles identity-related operations for Salesforce,
including permission sets, users, roles, groups, and ACL building.
"""

from .client_helper import ClientHelperForIdentitySync, SalesforceConstants
from .models import (
    EntityVisibility,
    PermissionSetAssignment,
    User,
    Group,
    GroupMember,
    EntityShareBase,
    ObjectRecord,
    UserRole,
    UserLogin,
    ObjectPermissions,
    FieldPermissions,
    Organization,
    SfIdentityCheckpointState,
    UserOrGroupType,
    RecordEnumerationDirection,
)
from .queries import IdentitySyncQueries
from .response_processor import SalesforceIdentitySOQLResponseProcessor

__all__ = [
    "ClientHelperForIdentitySync",
    "SalesforceConstants",
    "EntityVisibility",
    "PermissionSetAssignment",
    "User",
    "Group",
    "GroupMember",
    "EntityShareBase",
    "ObjectRecord",
    "UserRole",
    "UserLogin",
    "ObjectPermissions",
    "FieldPermissions",
    "Organization",
    "SfIdentityCheckpointState",
    "UserOrGroupType",
    "RecordEnumerationDirection",
    "IdentitySyncQueries",
    "SalesforceIdentitySOQLResponseProcessor",
]
