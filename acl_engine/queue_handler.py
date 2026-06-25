# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
acl_engine/queue_handler.py
---------------------------
Step 3.3.3: Queue, Public Group, and Manager-based group resolution.

This handler covers the "everything else" branch in group dispatch:

Group.Type = "Organization"
    The share was granted to the entire org.
    Returns the PUBLIC_SENTINEL constant so the caller can emit a
    tenant-wide grant without enumerating users.

Group.Type = "Queue" | "Group" (Public Group) | anything unrecognised
    Static membership stored in GroupMember.
    We expand the membership recursively – a group can nest other groups.
    User IDs are collected directly; nested group IDs are pushed onto the
    traversal stack and expanded in the same loop.
    Cycle detection prevents infinite loops.

Group.Type = "Manager"
    The share was granted to a specific manager identified by Group.RelatedId
    (a User Id).  We grant access to that manager AND their *direct reports*
    (one level) via User.ManagerId.

Group.Type = "ManagerAndSubordinatesInternal"
    Same as "Manager" but includes ALL transitive reports at every depth.

Why User.ManagerId and not UserRole?
--------------------------------------
The Manager / ManagerAndSubordinatesInternal group types mirror the org chart
(HR reporting line) stored in User.ManagerId, which is independent of the role
hierarchy stored in UserRole.ParentRoleId.  Both are valid sharing mechanisms
in Salesforce but they model different org structures.

Queries used
------------
  Group members       : SELECT UserOrGroupId FROM GroupMember WHERE GroupId = '<id>'
  Manager chain       : SELECT Id, ManagerId FROM User WHERE IsActive = true
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Optional

from acl_engine.models import PUBLIC_SENTINEL
from acl_engine.salesforce_client import SalesforceClient
from acl_engine.user_handler import USER_ID_PREFIX

logger = logging.getLogger("salesforce_connector.acl_engine")


class QueueHandler:
    """
    Resolves user sets for Queue, Public Group, and Manager-based groups.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client
        # Pre-warm cache: group_id → [member_ids] (None = not yet fetched)
        self._group_members: Optional[dict[str, list[str]]] = None
        self._prewarm_lock = threading.Lock()

    # ── Bulk pre-warm (once per run) ─────────────────────────────────────

    async def prewarm(self) -> None:
        """
        Fetch ALL GroupMember rows in one SOQL call.
        After this, resolve_static_group() is a pure in-memory DFS.
        """
        if self._group_members is not None:
            return

        members: dict[str, list[str]] = {}
        try:
            rows = await self._sf.query_all(
                "SELECT GroupId, UserOrGroupId FROM GroupMember"
            )
            for r in rows:
                gid = r.get("GroupId")
                mid = r.get("UserOrGroupId")
                if gid and mid:
                    members.setdefault(gid, []).append(mid)
            logger.info("[QueueHandler] Pre-warmed group members for %d group(s)", len(members))
        except RuntimeError as exc:
            logger.warning(
                "[QueueHandler] GroupMember prewarm failed: %s; will fall back to per-group SOQL", exc
            )

        with self._prewarm_lock:
            if self._group_members is None:
                self._group_members = members

    # ── Organization ──────────────────────────────────────────────────────────

    async def resolve_organization(self) -> set[str]:
        """
        Return {PUBLIC_SENTINEL} to signal org-wide (everyone) access.

        The caller (AclResolver) should emit a tenant-wide grant ACL entry
        instead of enumerating individual users.
        """
        logger.info("[QueueHandler] Organization group → public sentinel")
        return {PUBLIC_SENTINEL}

    # ── Queue / Public Group (static membership) ──────────────────────────────

    async def resolve_static_group(self, group_id: str) -> set[str]:
        """
        Expand a Queue or Public Group by iterating GroupMember records.

        Algorithm (iterative DFS, cycle-safe)
        --------------------------------------
        1. Pop a group_id from the stack.
        2. Fetch its GroupMember rows.
        3. For each member:
           - If it looks like a User (prefix "005") → add to result set.
           - Otherwise → push onto the stack for recursive expansion.
        4. Repeat until the stack is empty.

        Note: we rely on UserHandler.USER_ID_PREFIX ("005") to identify user
        IDs without an extra network call.  Any member ID that is *not* a user
        will be treated as a nested group and expanded in the next iteration.

        Parameters
        ----------
        group_id : The Group Id to expand.

        Returns
        -------
        set[str] – Salesforce User Ids reachable from this group.
        """
        all_users: set[str] = set()
        stack = [group_id]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            for member_id in await self._get_group_members(current):
                if member_id.startswith(USER_ID_PREFIX):
                    all_users.add(member_id)
                else:
                    # Nested group / queue / role group – expand on next iteration
                    stack.append(member_id)

        logger.debug(
            "[QueueHandler] Static group %s → %d user(s) after full expansion "
            "(%d group node(s) visited)",
            group_id,
            len(all_users),
            len(visited),
        )
        return all_users

    # ── Manager hierarchy (User.ManagerId) ────────────────────────────────────

    async def resolve_manager(self, manager_id: str) -> set[str]:
        """
        Return the manager + their *direct reports* (one level only).

        Uses User.ManagerId (HR org chart), not the role hierarchy.
        Group.Type = "Manager".
        """
        return await self._walk_manager_chain(manager_id, transitive=False)

    async def resolve_manager_and_subordinates(self, manager_id: str) -> set[str]:
        """
        Return the manager + ALL transitive reports at every depth.

        Uses User.ManagerId (HR org chart), not the role hierarchy.
        Group.Type = "ManagerAndSubordinatesInternal".
        """
        return await self._walk_manager_chain(manager_id, transitive=True)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _walk_manager_chain(self, manager_id: str, transitive: bool) -> set[str]:
        """
        BFS from *manager_id* through User.ManagerId relationships.

        Strategy
        --------
        1. Fetch all active users + their ManagerId in a single query.
        2. Build a reports_by_manager dict in memory.
        3. BFS/DFS from manager_id:
           - transitive=False : only one level of direct reports.
           - transitive=True  : all levels until no more reports are found.

        Pulling all users in one query is O(total_users) but avoids issuing
        one query per manager level, which is much worse on large orgs.
        """
        all_users = await self._sf.query_all(
            "SELECT Id, ManagerId FROM User WHERE IsActive = true"
        )

        # Build: manager_id → set of direct report user IDs
        reports_by_manager: dict[str, set[str]] = defaultdict(set)
        for user in all_users:
            uid = user.get("Id")
            mid = user.get("ManagerId")
            if uid and mid:
                reports_by_manager[mid].add(uid)

        # Start with the manager themselves; BFS through direct reports
        resolved: set[str] = {manager_id}
        frontier: list[str] = list(reports_by_manager.get(manager_id, set()))

        while frontier:
            current = frontier.pop()
            if current in resolved:
                continue
            resolved.add(current)
            if transitive:
                frontier.extend(reports_by_manager.get(current, set()))

        logger.debug(
            "[QueueHandler] Manager%s %s → %d user(s)",
            "+Subordinates" if transitive else "",
            manager_id,
            len(resolved),
        )
        return resolved

    async def _get_group_members(self, group_id: str) -> list[str]:
        """Return all UserOrGroupId values from GroupMember for *group_id*."""
        soql = f"SELECT UserOrGroupId FROM GroupMember WHERE GroupId = '{group_id}'"
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning("[QueueHandler] GroupMember query failed for %s: %s", group_id, exc)
            return []
        return [r["UserOrGroupId"] for r in records if r.get("UserOrGroupId")]
