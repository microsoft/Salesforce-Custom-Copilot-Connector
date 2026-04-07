"""
acl_engine/models.py
--------------------
Shared data classes and enumerations used across the ACL engine.
No Salesforce or HTTP logic lives here – pure data containers only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Sentinel ──────────────────────────────────────────────────────────────────
# Returned in the user_ids set when the record is visible to the entire org.
# Callers should emit a tenant-wide "grant everyone" ACL entry instead of
# enumerating individual users.
PUBLIC_SENTINEL = "__PUBLIC__"


# ── Org-Wide Default visibility values ───────────────────────────────────────
class OWDVisibility(str, Enum):
    """Mirrors Salesforce InternalSharingModel / OWD string values."""
    PRIVATE = "Private"
    PUBLIC_READ = "Read"
    PUBLIC_READ_WRITE = "Edit"
    PUBLIC_READ_WRITE_TRANSFER = "ReadEditTransfer"
    ALL = "All"
    CONTROLLED_BY_PARENT = "ControlledByParent"
    CONTROLLED_BY_CAMPAIGN = "ControlledByCampaign"
    CONTROLLED_BY_LEAD_OR_CONTACT = "ControlledByLeadOrContact"


# ── Group type values returned by the Salesforce Group object ─────────────────
class GroupType(str, Enum):
    """Values that can appear in Group.Type (and as UserOrGroup.Type on share rows)."""
    USER = "User"
    QUEUE = "Queue"
    ROLE = "Role"
    ROLE_AND_SUBORDINATES = "RoleAndSubordinates"
    ROLE_AND_SUBORDINATES_INTERNAL = "RoleAndSubordinatesInternal"
    ORGANIZATION = "Organization"
    MANAGER = "Manager"
    MANAGER_AND_SUBORDINATES_INTERNAL = "ManagerAndSubordinatesInternal"
    PUBLIC_GROUP = "Group"
    TERRITORY = "Territory"
    TERRITORY_AND_SUBORDINATES = "TerritoryAndSubordinates"
    TERRITORY_AND_SUBORDINATES_INTERNAL = "TerritoryAndSubordinatesInternal"


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class ShareEntry:
    """
    One row from an <ObjectType>Share table.

    Fields
    ------
    user_or_group_id : Salesforce User or Group Id.
    row_cause        : Why the share was created (e.g. "Manual", "Rule",
                       "Territory", "Owner").
    access_level     : The level of access granted (e.g. "Read", "Edit",
                       "All").  Field name varies by object; discovered
                       dynamically by ShareFetcher.
    """
    user_or_group_id: str
    row_cause: Optional[str] = None
    access_level: Optional[str] = None


@dataclass
class GroupRecord:
    """
    Minimal representation of a Salesforce Group record.

    `type`       – GroupType value (e.g. "Role", "Queue", "Manager", …)
    `related_id` – For dynamic groups (Role, Territory, Manager) this points
                   to the underlying RoleId / Territory2Id / UserId.
    """
    id: str
    type: Optional[str] = None
    related_id: Optional[str] = None
    does_include_bosses: Optional[bool] = None


@dataclass
class AclResult:
    """
    Final output produced by AclResolver for a single record.

    Fields
    ------
    object_type : Salesforce object API name (e.g. "Account").
    record_id   : The 18-char Salesforce record Id.
    owd         : The raw OWD string fetched for this object type.
    is_public   : True when the record is visible to every user in the org
                  (OWD is public-readable).  When True, user_ids is empty.
    user_ids    : Set of Salesforce User Ids that may see this record.
                  Contains PUBLIC_SENTINEL when is_public is True.
    """
    object_type: str
    record_id: str
    owd: str = ""
    is_public: bool = False
    user_ids: set[str] = field(default_factory=set)
