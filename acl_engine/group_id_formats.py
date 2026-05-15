"""
acl_engine/group_id_formats.py
-------------------------------
Canonical format strings for external group IDs used in both the Identity
Crawl (group creation) and Content ACL (group references).

**Critical**: The group IDs generated here MUST be used in both the identity
crawl and the content ACL builder.  Any mismatch causes silent authorization
failures at search time.

**Constraint**: Microsoft Graph external group IDs must contain only ASCII
alphanumeric characters (no hyphens, underscores, or special characters).

Format placeholders:
    {0} = object name  (e.g. "Account")
    {1} = related ID   (e.g. role ID, group ID, user ID)
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


class _SanitizedFormat(str):
    """A str subclass whose ``.format()`` strips non-alphanumeric characters."""

    def format(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        raw = super().format(*args, **kwargs)
        return _NON_ALNUM.sub("", raw)


class SfGroupIdFormats:
    """
    Format strings for external group IDs.

    Every constant uses ``str.format()`` placeholders:
        {0} = object name  (e.g. "Account")
        {1} = related Salesforce ID  (e.g. role ID, group ID, user ID)

    All IDs are alphanumeric only (Graph API requirement).
    Non-alphanumeric characters in inputs are automatically stripped.
    """

    # Top-level group (one per object, used in PUBLIC OWD ACLs)
    TOP_LEVEL = _SanitizedFormat("{0}TopLevel")
    # Example: "AccountTopLevel"

    # Global users with ViewAll/ModifyAll (PRIVATE OWD)
    GLOBAL_USERS = _SanitizedFormat("{0}GlobalUsers")
    # Example: "AccountGlobalUsers"

    # All internal users (Organization-type share)
    ALL_INTERNAL_USERS = _SanitizedFormat("{0}AllInternalUsers")
    # Example: "AccountAllInternalUsers"

    # Role-based groups (with parent role nesting)
    ROLE = _SanitizedFormat("{0}{1}Role")
    # Example: "Account00E5g000001ABCRole"

    ROLE_AND_SUBORDINATES = _SanitizedFormat("{0}{1}RoleAndSubordinates")
    # Example: "Account00E5g000001ABCRoleAndSubordinates"

    # Role groups WITHOUT parent nesting (used as child of RoleAndSub)
    ROLE_NO_PARENTS = _SanitizedFormat("{0}{1}RoleNoParents")
    ROLE_AND_SUBORDINATES_NO_PARENTS = _SanitizedFormat("{0}{1}RoleAndSubordinatesNoParents")

    # Public/Queue groups
    PUBLIC_GROUP = _SanitizedFormat("{0}{1}PublicGroup")
    # Example: "Account00G5g000001XYZPublicGroup"

    # Manager groups
    MANAGER = _SanitizedFormat("{0}{1}Manager")
    # Example: "Account0055g000001DEFManager"

    MANAGER_AND_SUBORDINATES = _SanitizedFormat("{0}{1}ManagerAndSubordinates")
    # Example: "Account0055g000001DEFManagerAndSubordinates"

    # Territory groups
    TERRITORY = _SanitizedFormat("{0}{1}Territory")
    # Example: "Account0ML5g000000ABCTerritory"

    TERRITORY_AND_SUBORDINATES = _SanitizedFormat("{0}{1}TerritoryAndSubordinates")
    # Example: "Account0ML5g000000ABCTerritoryAndSubordinates"
