"""
Models for Salesforce Identity Sync

Mirrors the C# identity sync models from:
- Microsoft.Graph.Connectors.Salesforce.IdentitySync.Models
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EntityVisibility(str, Enum):
    """Org-wide default visibility settings for Salesforce entities."""

    NONE = "None"  # Private
    PUBLIC_READ_ONLY = "Read"
    PUBLIC_READ_WRITE = "Edit"
    PUBLIC_READ_WRITE_TRANSFER = "ControlledByParent"
    CONTROLLED_BY_PARENT = "ControlledByParent"
    CONTROLLED_BY_CAMPAIGN = "ControlledByCampaign"
    CONTROLLED_BY_LEAD_OR_CONTACT = "ControlledByLeadOrContact"


class UserOrGroupType(str, Enum):
    """Type of user or group in Salesforce."""

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
    """Direction for record enumeration."""

    ASCENDING = "Ascending"
    DESCENDING = "Descending"


@dataclass
class IdentityResponseBase:
    """Base class for identity SOQL response objects."""

    Id: str
    attributes: Optional[dict[str, Any]] = None


@dataclass
class UserOrGroup:
    """Represents a User or Group in Salesforce."""

    Type: str
    attributes: Optional[dict[str, Any]] = None


@dataclass
class PermissionSetAssignment(IdentityResponseBase):
    """Permission set assignment for a user."""

    AssigneeId: Optional[str] = None
    PermissionSetId: Optional[str] = None
    IsActive: Optional[bool] = None


@dataclass
class PermissionSet:
    """Permission set details."""

    Id: str
    Name: Optional[str] = None
    IsOwnedByProfile: Optional[bool] = None


@dataclass
class UserRole:
    """User role in Salesforce hierarchy."""

    Id: str
    Name: Optional[str] = None
    ParentRoleId: Optional[str] = None
    DeveloperName: Optional[str] = None
    ContactAccessForAccountOwner: Optional[str] = None
    OpportunityAccessForAccountOwner: Optional[str] = None


@dataclass
class User(IdentityResponseBase):
    """Salesforce User with permissions and role."""

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
    PermissionSetAssignments: Optional[dict[str, Any]] = None  # Nested SOQL result


@dataclass
class EntityShareBase(IdentityResponseBase):
    """Base class for entity share records (AccountShare, OpportunityShare, etc.)."""

    UserOrGroupId: Optional[str] = None
    RowCause: Optional[str] = None
    UserOrGroup: Optional[UserOrGroup] = None


@dataclass
class Group(IdentityResponseBase):
    """Salesforce Group (Role, Queue, Public Group, etc.)."""

    Name: Optional[str] = None
    Type: Optional[str] = None
    RelatedId: Optional[str] = None
    DeveloperName: Optional[str] = None
    DoesIncludeBosses: Optional[bool] = None
    GroupMembers: Optional[dict[str, Any]] = None  # Nested SOQL result


@dataclass
class GroupMember(IdentityResponseBase):
    """Group membership record."""

    GroupId: Optional[str] = None
    UserOrGroupId: Optional[str] = None


@dataclass
class ObjectRecord(IdentityResponseBase):
    """Generic object record with shares."""

    IsDeleted: bool = False
    Shares: Optional[dict[str, Any]] = None  # Nested SOQL result


@dataclass
class UserLogin(IdentityResponseBase):
    """User login status."""

    UserId: Optional[str] = None
    IsFrozen: bool = False


@dataclass
class ObjectPermissions(IdentityResponseBase):
    """Object-level permissions for profiles/permission sets."""

    ParentId: Optional[str] = None
    SobjectType: Optional[str] = None
    PermissionsRead: bool = False
    PermissionsCreate: bool = False
    PermissionsEdit: bool = False
    PermissionsDelete: bool = False
    PermissionsViewAllRecords: bool = False
    PermissionsModifyAllRecords: bool = False


@dataclass
class FieldPermissions(IdentityResponseBase):
    """Field-level security permissions."""

    ParentId: Optional[str] = None
    SobjectType: Optional[str] = None
    Field: Optional[str] = None
    PermissionsRead: bool = False
    PermissionsEdit: bool = False


@dataclass
class Organization(IdentityResponseBase):
    """Org-wide default settings."""

    DefaultAccountAccess: EntityVisibility = EntityVisibility.NONE
    DefaultContactAccess: EntityVisibility = EntityVisibility.NONE
    DefaultOpportunityAccess: EntityVisibility = EntityVisibility.NONE
    DefaultLeadAccess: EntityVisibility = EntityVisibility.NONE
    DefaultCaseAccess: EntityVisibility = EntityVisibility.NONE
    DefaultCampaignAccess: EntityVisibility = EntityVisibility.NONE


@dataclass
class SfIdentityCheckpointState:
    """Checkpoint state for identity sync operations."""

    LastRecordId: str = ""
    NextUrl: str = ""
    Exhausted: bool = False
