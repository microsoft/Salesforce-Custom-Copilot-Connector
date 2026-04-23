"""
acl_engine/group_id_formats.py
-------------------------------
Canonical format strings for external group IDs used in both the Identity
Crawl (group creation) and Content ACL (group references).

**Critical**: The group IDs generated here MUST be used in both the identity
crawl and the content ACL builder.  Any mismatch causes silent authorization
failures at search time.

Format placeholders:
    {0} = object name  (e.g. "Account")
    {1} = related ID   (e.g. role ID, group ID, user ID)
"""
from __future__ import annotations


class SfGroupIdFormats:
    """
    Format strings for external group IDs.

    Every constant uses ``str.format()`` placeholders:
        {0} = object name  (e.g. "Account")
        {1} = related Salesforce ID  (e.g. role ID, group ID, user ID)
    """

    # Top-level group (one per object, used in PUBLIC OWD ACLs)
    TOP_LEVEL = "{0}-TopLevel"
    # Example: "Account-TopLevel"

    # Global users with ViewAll/ModifyAll (PRIVATE OWD)
    GLOBAL_USERS = "{0}-GlobalUsers"
    # Example: "Account-GlobalUsers"

    # All internal users (Organization-type share)
    ALL_INTERNAL_USERS = "{0}-AllInternalUsers"
    # Example: "Account-AllInternalUsers"

    # Role-based groups (with parent role nesting)
    ROLE = "{0}-{1}-Role"
    # Example: "Account-00E5g000001ABC-Role"

    ROLE_AND_SUBORDINATES = "{0}-{1}-RoleAndSubordinates"
    # Example: "Account-00E5g000001ABC-RoleAndSubordinates"

    # Role groups WITHOUT parent nesting (used as child of RoleAndSub)
    ROLE_NO_PARENTS = "{0}-{1}-RoleNoParents"
    ROLE_AND_SUBORDINATES_NO_PARENTS = "{0}-{1}-RoleAndSubordinatesNoParents"

    # Public/Queue groups
    PUBLIC_GROUP = "{0}-{1}-PublicGroup"
    # Example: "Account-00G5g000001XYZ-PublicGroup"

    # Manager groups
    MANAGER = "{0}-{1}-Manager"
    # Example: "Account-0055g000001DEF-Manager"

    MANAGER_AND_SUBORDINATES = "{0}-{1}-ManagerAndSubordinates"
    # Example: "Account-0055g000001DEF-ManagerAndSubordinates"

    # Territory groups
    TERRITORY = "{0}-{1}-Territory"
    # Example: "Account-0ML5g000000ABC-Territory"

    TERRITORY_AND_SUBORDINATES = "{0}-{1}-TerritoryAndSubordinates"
    # Example: "Account-0ML5g000000ABC-TerritoryAndSubordinates"
