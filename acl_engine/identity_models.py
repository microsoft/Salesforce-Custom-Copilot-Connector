# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
acl_engine/identity_models.py
-----------------------------
Data models and enumerations for the Identity Crawl and Group-based ACL
resolution pipeline.

These models represent Salesforce sharing concepts (users, groups, roles,
share entries) and the group identity types used when creating external
groups in Microsoft Graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Org-Wide Default visibility ──────────────────────────────────────────────

class EntityVisibility(str, Enum):
    """Org-Wide Default visibility of a Salesforce object."""
    NONE = "None"                                          # Private
    READ = "Read"                                          # Public Read
    EDIT = "Edit"                                          # Public Read/Write
    READ_EDIT_TRANSFER = "ReadEditTransfer"                # Public Read/Write/Transfer
    CONTROLLED_BY_PARENT = "ControlledByParent"
    CONTROLLED_BY_LEAD_OR_CONTACT = "ControlledByLeadOrContact"
    CONTROLLED_BY_CAMPAIGN = "ControlledByCampaign"


# ── User/Group type on share records ────────────────────────────────────────

class UserOrGroupType(str, Enum):
    """Type from the UserOrGroup relationship on share records."""
    USER = "User"
    QUEUE = "Queue"
    ROLE = "Role"
    ROLE_AND_SUBORDINATES = "RoleAndSubordinates"
    ROLE_AND_SUBORDINATES_INTERNAL = "RoleAndSubordinatesInternal"
    ORGANIZATION = "Organization"
    MANAGER = "Manager"
    MANAGER_AND_SUBORDINATES_INTERNAL = "ManagerAndSubordinatesInternal"
    TERRITORY = "Territory"
    TERRITORY_AND_SUBORDINATES = "TerritoryAndSubordinates"
    TERRITORY_AND_SUBORDINATES_INTERNAL = "TerritoryAndSubordinatesInternal"
    REGULAR = "Regular"
    PERSONAL = "Personal"


# ── Child group identity type ────────────────────────────────────────────────

class GroupIdentityType(str, Enum):
    """Type of child group emitted during identity sync."""
    ROLE_WITH_PARENT = "RoleWithParentRole"
    ROLE_WITHOUT_PARENT = "RoleWithoutParentRole"
    ROLE_AND_SUB_WITH_PARENT = "RoleAndSubWithParentRole"
    ROLE_AND_SUB_WITHOUT_PARENT = "RoleAndSubWithoutParentRole"
    PUBLIC_GROUP = "PublicGroup"
    GLOBAL_ACCESS_USERS = "GlobalAccessUsers"
    ALL_INTERNAL_USERS = "AllInternalUsers"
    MANAGER = "Manager"
    MANAGER_AND_SUBORDINATES = "ManagerAndSubordinates"
    TERRITORY = "Territory"
    TERRITORY_AND_SUBORDINATES = "TerritoryAndSubordinates"


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SfUser:
    """Represents a Salesforce user relevant to ACL resolution."""
    id: str
    name: str = ""
    alias: str = ""
    email: str = ""
    federation_identifier: str = ""
    first_name: str = ""
    last_name: str = ""
    user_name: str = ""
    user_role_id: str = ""
    parent_role_id: str = ""
    manager_id: str = ""
    is_active: bool = True
    is_frozen: bool = False
    permission_sets: list = field(default_factory=list)

    def get_source_properties(self) -> dict[str, str]:
        """Returns identity source properties for external identity mapping."""
        return {
            "Name": self.name or "",
            "Alias": self.alias or "",
            "Email": self.email or "",
            "FirstName": self.first_name or "",
            "LastName": self.last_name or "",
            "FederationIdentifier": self.federation_identifier or "",
            "UserName": self.user_name or "",
        }


@dataclass
class SfGroup:
    """Represents a Salesforce Group record used during ACL resolution."""
    id: str
    type: UserOrGroupType
    related_id: str = ""
    does_include_bosses: bool = False
    group_members: list[str] = field(default_factory=list)


@dataclass
class EntityShare:
    """A single share entry from an ``{ObjectName}Share`` table."""
    user_or_group_id: str
    user_or_group_type: UserOrGroupType


@dataclass
class UserRole:
    """Minimal representation of a Salesforce UserRole record."""
    id: str
    parent_role_id: str = ""


# ── Visibility helpers ───────────────────────────────────────────────────────

_PUBLIC_VISIBILITIES = frozenset({
    EntityVisibility.READ,
    EntityVisibility.EDIT,
    EntityVisibility.READ_EDIT_TRANSFER,
})

_CONTROLLED_BY_PARENT_VISIBILITIES = frozenset({
    EntityVisibility.CONTROLLED_BY_PARENT,
    EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
    EntityVisibility.CONTROLLED_BY_CAMPAIGN,
})


def is_public_visibility(visibility: EntityVisibility) -> bool:
    """Return True when the OWD means the object is publicly readable."""
    return visibility in _PUBLIC_VISIBILITIES


def is_private_visibility(visibility: EntityVisibility) -> bool:
    """Return True when the OWD means per-record ACLs are required."""
    return visibility == EntityVisibility.NONE


def is_controlled_by_parent(visibility: EntityVisibility) -> bool:
    """Return True when the OWD means sharing is inherited from a parent."""
    return visibility in _CONTROLLED_BY_PARENT_VISIBILITIES


def parse_visibility(raw: str) -> EntityVisibility:
    """Parse a raw Salesforce OWD string into an ``EntityVisibility`` enum value."""
    mapping = {
        "Private": EntityVisibility.NONE,
        "None": EntityVisibility.NONE,
        "Read": EntityVisibility.READ,
        "Edit": EntityVisibility.EDIT,
        "ReadEditTransfer": EntityVisibility.READ_EDIT_TRANSFER,
        "ControlledByParent": EntityVisibility.CONTROLLED_BY_PARENT,
        "ControlledByCampaign": EntityVisibility.CONTROLLED_BY_CAMPAIGN,
        "ControlledByLeadOrContact": EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT,
    }
    return mapping.get(raw, EntityVisibility.NONE)
