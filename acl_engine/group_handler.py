# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
acl_engine/group_handler.py
----------------------------
Step 3.3.0: General group dispatcher.

This class is the single entry point for every UserOrGroupId that is NOT a
plain User (i.e. does not start with "005").

It fetches the Group record to discover its Type and RelatedId, then routes
to the appropriate specialist handler:

  ┌─────────────────────────────────────────────┬───────────────────────────┐
  │ Group.Type                                  │ Handler / method          │
  ├─────────────────────────────────────────────┼───────────────────────────┤
  │ Role                                        │ RoleHandler.resolve_role  │
  │ RoleAndSubordinates                         │ RoleHandler               │
  │ RoleAndSubordinatesInternal                 │   .resolve_role_and_subs  │
  ├─────────────────────────────────────────────┼───────────────────────────┤
  │ Territory                                   │ TerritoryHandler          │
  │ TerritoryAndSubordinates                    │   .resolve_territory      │
  │ TerritoryAndSubordinatesInternal            │   .resolve_territory_and  │
  │                                             │   _subordinates           │
  ├─────────────────────────────────────────────┼───────────────────────────┤
  │ Organization                                │ QueueHandler              │
  │                                             │   .resolve_organization   │
  ├─────────────────────────────────────────────┼───────────────────────────┤
  │ Manager                                     │ QueueHandler              │
  │ ManagerAndSubordinatesInternal              │   .resolve_manager[_subs] │
  ├─────────────────────────────────────────────┼───────────────────────────┤
  │ Queue / Group (Public Group) / unrecognised │ QueueHandler              │
  │                                             │   .resolve_static_group   │
  └─────────────────────────────────────────────┴───────────────────────────┘

Why query the Group table instead of relying solely on the UserOrGroup.Type
embedded in share rows?
    Share rows include a UserOrGroup sub-object with Type, but that type is
    "User" or "Group" – not the specific group sub-type.  The specific sub-type
    (Role, Queue, Territory, …) lives in the Group table itself.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from acl_engine.models import GroupRecord
from acl_engine.role_handler import RoleHandler
from acl_engine.territory_handler import TerritoryHandler
from acl_engine.queue_handler import QueueHandler
from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


class GroupHandler:
    """
    Identifies the type of a Salesforce Group and delegates to the correct
    specialist handler.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client
        self._role_handler = RoleHandler(sf_client)
        self._territory_handler = TerritoryHandler(sf_client)
        self._queue_handler = QueueHandler(sf_client)
        # Bulk pre-warm cache: group_id → GroupRecord
        self._group_cache: Optional[dict[str, GroupRecord]] = None
        self._prewarm_lock = threading.Lock()

    # ── Expose sub-handler prewarm ──────────────────────────────────────────

    async def prewarm(self) -> None:
        """
        Fetch ALL Salesforce Group records in 1 SOQL call, and pre-warm the
        role handler (2 SOQL calls).  After this, every group and role lookup
        during ACL resolution is a pure in-memory dict lookup.
        """
        if self._group_cache is not None:
            return

        with self._prewarm_lock:
            if self._group_cache is not None:
                return

            cache: dict[str, GroupRecord] = {}
            try:
                rows = await self._sf.query_all(
                    "SELECT Id, Type, RelatedId, DoesIncludeBosses FROM Group"
                )
                for r in rows:
                    gid = r.get("Id")
                    if gid:
                        cache[gid] = GroupRecord(
                            id=gid,
                            type=r.get("Type"),
                            related_id=r.get("RelatedId"),
                            does_include_bosses=r.get("DoesIncludeBosses"),
                        )
                logger.info("[GroupHandler] Pre-warmed %d group(s)", len(cache))
            except RuntimeError as exc:
                logger.warning("[GroupHandler] Bulk group prewarm failed: %s; will fall back to per-group SOQL", exc)

            self._group_cache = cache

        # Pre-warm role handler (independent of group cache)
        await self._role_handler.prewarm()
        # Pre-warm queue/group members, territory data, and active user set
        await self._queue_handler.prewarm()
        await self._territory_handler.prewarm()
        await self._user_handler.prewarm()

    @property
    def _user_handler(self):
        """Access the UserHandler from the RoleHandler's sf_client (shared)."""
        # UserHandler is instantiated inside GroupHandler to share prewarm
        if not hasattr(self, '_uh'):
            from acl_engine.user_handler import UserHandler
            self._uh = UserHandler(self._sf)
        return self._uh

    # ── Public API ────────────────────────────────────────────────────────────

    async def resolve(self, group_id: str) -> set[str]:
        """
        Resolve *group_id* to a set of Salesforce User Ids.

        Returns {PUBLIC_SENTINEL} when the group represents the entire
        organisation (Group.Type = "Organization").

        Parameters
        ----------
        group_id : Any non-user UserOrGroupId from a share row or owner field.

        Returns
        -------
        set[str] – Salesforce User Ids, or {PUBLIC_SENTINEL} for org-wide access.
        """
        group = await self._fetch_group(group_id)

        if not group:
            logger.warning("[GroupHandler] Group record not found for Id=%s; skipping", group_id)
            return set()

        gtype = (group.type or "").strip()
        related = group.related_id

        logger.debug(
            "[GroupHandler] Dispatching group %s  Type=%s  RelatedId=%s",
            group_id,
            gtype,
            related,
        )

        # ── 3.3.1  Role-based ─────────────────────────────────────────────────
        if gtype == "Role":
            if not related:
                logger.warning("[GroupHandler] Role group %s has no RelatedId", group_id)
                return set()
            return await self._role_handler.resolve_role(related)

        if gtype in ("RoleAndSubordinates", "RoleAndSubordinatesInternal"):
            if not related:
                logger.warning("[GroupHandler] Role+Subordinates group %s has no RelatedId", group_id)
                return set()
            return await self._role_handler.resolve_role_and_subordinates(related)

        # ── 3.3.2  Territory-based ────────────────────────────────────────────
        if gtype == "Territory":
            if not related:
                logger.warning("[GroupHandler] Territory group %s has no RelatedId", group_id)
                return set()
            return await self._territory_handler.resolve_territory(related)

        if gtype in (
            "TerritoryAndSubordinates",
            "TerritoryAndSubordinatesInternal",
        ):
            if not related:
                logger.warning(
                    "[GroupHandler] Territory+Subordinates group %s has no RelatedId", group_id
                )
                return set()
            return await self._territory_handler.resolve_territory_and_subordinates(related)

        # ── 3.3.3  Organization (everyone) ────────────────────────────────────
        if gtype == "Organization":
            return await self._queue_handler.resolve_organization()

        # ── 3.3.3  Manager-based ──────────────────────────────────────────────
        if gtype == "Manager":
            if not related:
                logger.warning("[GroupHandler] Manager group %s has no RelatedId", group_id)
                return set()
            return await self._queue_handler.resolve_manager(related)

        if gtype == "ManagerAndSubordinatesInternal":
            if not related:
                logger.warning(
                    "[GroupHandler] Manager+Subordinates group %s has no RelatedId", group_id
                )
                return set()
            return await self._queue_handler.resolve_manager_and_subordinates(related)

        # ── 3.3.3  Queue / Public Group / unrecognised ────────────────────────
        # Covers Group.Type = "Queue", "Group" (Public Group), and anything
        # we do not explicitly recognise – safe fallback is static expansion.
        return await self._queue_handler.resolve_static_group(group_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_group(self, group_id: str) -> Optional[GroupRecord]:
        """
        Return the GroupRecord for *group_id*.

        Serves from the bulk pre-warm cache when available; falls back to a
        per-group SOQL otherwise.
        """
        # Fast path — bulk cache hit
        if self._group_cache is not None:
            return self._group_cache.get(group_id)

        # Slow path — per-group SOQL fallback
        soql = (
            f"SELECT Id, Type, RelatedId, DoesIncludeBosses "
            f"FROM Group "
            f"WHERE Id = '{group_id}' LIMIT 1"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning("[GroupHandler] Could not fetch Group %s: %s", group_id, exc)
            return None

        if not records:
            return None

        r = records[0]
        return GroupRecord(
            id=r.get("Id", group_id),
            type=r.get("Type"),
            related_id=r.get("RelatedId"),
            does_include_bosses=r.get("DoesIncludeBosses"),
        )
