"""
acl_engine/role_handler.py
--------------------------
Step 3.3.1: Role-based group resolution.

Salesforce role hierarchy is a tree stored in the UserRole object:
  UserRole.Id  ──parent──►  UserRole.ParentRoleId  ──parent──► ...  (root)

Three resolution modes are supported
-------------------------------------
resolve_role(role_id)
    Users assigned to *exactly* this role.
    Used for Group.Type = "Role".

resolve_role_and_subordinates(role_id)
    Users in this role PLUS all descendant roles (downward BFS/DFS).
    Used for Group.Type = "RoleAndSubordinates" / "RoleAndSubordinatesInternal".

resolve_parent_roles(role_id)
    Users in all *ancestor* roles (upward walk to root).
    Used for implicit "Grant Access Using Hierarchies" – the record owner's
    managers in the role tree automatically see the record too.
    Called by the resolver after it resolves the owner's role.

Queries used
------------
  Users in a role  : SELECT Id FROM User WHERE UserRoleId = '<id>' AND IsActive = true
  Child roles      : SELECT Id FROM UserRole WHERE ParentRoleId = '<id>'
  Parent of a role : SELECT Id, ParentRoleId FROM UserRole WHERE Id = '<id>' LIMIT 1
"""
from __future__ import annotations

import logging
from typing import Optional

from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


class RoleHandler:
    """
    Resolves user sets for role-based Salesforce groups.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client

    # ── Public resolution methods ─────────────────────────────────────────────

    async def resolve_role(self, role_id: str) -> set[str]:
        """
        Return the set of active users assigned to exactly *role_id*.
        No traversal – single role only.
        """
        users = await self._users_in_role(role_id)
        logger.info("[RoleHandler] Role %s → %d user(s)", role_id, len(users))
        return users

    async def resolve_role_and_subordinates(self, role_id: str) -> set[str]:
        """
        Return users in *role_id* PLUS every descendant role.

        Traverses downward through UserRole.ParentRoleId relationships using an
        iterative DFS to avoid Python recursion limits on deep hierarchies.
        Cycle detection is included as a safety guard.
        """
        all_users: set[str] = set()
        stack = [role_id]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            users = await self._users_in_role(current)
            all_users.update(users)

            children = await self._child_role_ids(current)
            stack.extend(children)

        logger.info(
            "[RoleHandler] Role+Subordinates %s → %d user(s) across %d role(s)",
            role_id,
            len(all_users),
            len(visited),
        )
        return all_users

    async def resolve_parent_roles(self, role_id: str) -> set[str]:
        """
        Return users in all *ancestor* roles above *role_id*.

        Implements Salesforce's "Grant Access Using Hierarchies" rule:
        anyone higher up in the role tree than the record owner automatically
        inherits read access to the owner's records.

        Walks upward via UserRole.ParentRoleId until the root (ParentRoleId is
        null) or a cycle is detected.
        """
        parent_role_ids = await self._collect_ancestor_role_ids(role_id)
        if not parent_role_ids:
            logger.debug("[RoleHandler] Role %s has no parent roles", role_id)
            return set()

        all_users: set[str] = set()
        for parent_id in parent_role_ids:
            users = await self._users_in_role(parent_id)
            all_users.update(users)

        logger.info(
            "[RoleHandler] Parent roles of %s → %d ancestor role(s), %d user(s)",
            role_id,
            len(parent_role_ids),
            len(all_users),
        )
        return all_users

    # ── Private query helpers ─────────────────────────────────────────────────

    async def _users_in_role(self, role_id: str) -> set[str]:
        """Fetch active users whose UserRoleId matches *role_id*."""
        soql = (
            f"SELECT Id FROM User "
            f"WHERE UserRoleId = '{role_id}' AND IsActive = true"
        )
        records = await self._sf.query_all(soql)
        return {r["Id"] for r in records if r.get("Id")}

    async def _child_role_ids(self, role_id: str) -> list[str]:
        """Fetch direct child roles (one level down) of *role_id*."""
        soql = f"SELECT Id FROM UserRole WHERE ParentRoleId = '{role_id}'"
        records = await self._sf.query_all(soql)
        return [r["Id"] for r in records if r.get("Id")]

    async def _get_parent_role_id(self, role_id: str) -> Optional[str]:
        """Return the ParentRoleId for *role_id*, or None if it is the root."""
        soql = (
            f"SELECT Id, ParentRoleId FROM UserRole "
            f"WHERE Id = '{role_id}' LIMIT 1"
        )
        records = await self._sf.query_all(soql)
        if not records:
            return None
        return records[0].get("ParentRoleId")  # None at root

    async def _collect_ancestor_role_ids(self, role_id: str) -> set[str]:
        """
        Walk upward from *role_id* collecting every ancestor role ID.
        Stops when ParentRoleId is null or a cycle is detected.
        """
        ancestors: set[str] = set()
        visited: set[str] = {role_id}
        current = role_id

        while True:
            parent_id = await self._get_parent_role_id(current)
            if not parent_id or parent_id in visited:
                break
            ancestors.add(parent_id)
            visited.add(parent_id)
            current = parent_id

        return ancestors
