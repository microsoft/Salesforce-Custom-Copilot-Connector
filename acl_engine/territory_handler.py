"""
acl_engine/territory_handler.py
--------------------------------
Step 3.3.2: Territory2-based group resolution.

Salesforce Territory Management 2.0 introduces a hierarchy of Territory2 nodes
stored in the Territory2 object:
  Territory2.Id  ──parent──►  Territory2.ParentTerritory2Id  ──► ... (root)

Users are linked to territories via UserTerritory2Association.
Records (Account, Opportunity, …) are linked via ObjectTerritory2Association.

Resolution modes
----------------
resolve_territory(territory2_id)
    Users assigned to exactly *this* territory.
    Used for Group.Type = "Territory".

resolve_territory_and_subordinates(territory2_id)
    Users in this territory PLUS all descendant territories (downward DFS).
    Used for Group.Type = "TerritoryAndSubordinates" /
    "TerritoryAndSubordinatesInternal".

resolve_parent_territories(territory2_id)
    Users in all *ancestor* territories (upward walk to root).
    Mirrors the role hierarchy "implicit sharing" concept – users who manage a
    territory can see records in all child territories.

get_territory_ids_for_record(record_id)
    Entry point for Account / Opportunity records: fetches the Territory2Ids
    directly assigned to the record via ObjectTerritory2Association.
    The caller then passes each territory ID into one of the resolution methods.

Full flow for a record (called by the resolver)
-----------------------------------------------
  1. get_territory_ids_for_record(record_id)      → direct Territory2Id(s)
  2. For each territory ID:
       a. resolve_territory(t_id)                  → users in that territory
       b. _collect_ancestor_territory_ids(t_id)    → walk upward
       c. Fetch users for each ancestor territory
  3. Union all user sets

Queries used
------------
  Direct territory assignments  : SELECT Territory2Id FROM ObjectTerritory2Association WHERE ObjectId = '<id>'
  Users in a territory          : SELECT UserId FROM UserTerritory2Association WHERE Territory2Id = '<id>'
  Child territories             : SELECT Id FROM Territory2 WHERE ParentTerritory2Id = '<id>'
  Parent of a territory         : SELECT Id, ParentTerritory2Id FROM Territory2 WHERE Id = '<id>' LIMIT 1
"""
from __future__ import annotations

import logging
from typing import Optional

from acl_engine.sf_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")


class TerritoryHandler:
    """
    Resolves user sets for Territory2-based Salesforce groups and record
    territory assignments.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client

    # ── Record-level territory look-up ────────────────────────────────────────

    async def get_territory_ids_for_record(self, record_id: str) -> list[str]:
        """
        Return Territory2Ids directly assigned to *record_id* via
        ObjectTerritory2Association.

        This is the starting point for Account / Opportunity records.
        Returns an empty list if the object has no territory assignments.
        """
        soql = (
            f"SELECT Territory2Id "
            f"FROM ObjectTerritory2Association "
            f"WHERE ObjectId = '{record_id}'"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning(
                "[TerritoryHandler] Could not fetch territory assignments for %s: %s",
                record_id,
                exc,
            )
            return []

        territory_ids = [r["Territory2Id"] for r in records if r.get("Territory2Id")]
        logger.info(
            "[TerritoryHandler] Record %s → %d direct territory assignment(s): %s",
            record_id,
            len(territory_ids),
            territory_ids,
        )
        return territory_ids

    # ── Group-type resolution methods ─────────────────────────────────────────

    async def resolve_territory(self, territory2_id: str) -> set[str]:
        """
        Return users assigned to exactly *territory2_id*.
        No traversal – single territory only.
        Used for Group.Type = "Territory".
        """
        users = await self._users_in_territory(territory2_id)
        logger.info(
            "[TerritoryHandler] Territory %s → %d user(s)", territory2_id, len(users)
        )
        return users

    async def resolve_territory_and_subordinates(self, territory2_id: str) -> set[str]:
        """
        Return users in *territory2_id* PLUS every descendant territory.

        Traverses downward through Territory2.ParentTerritory2Id relationships
        using an iterative DFS with cycle detection.
        Used for Group.Type = "TerritoryAndSubordinates" /
        "TerritoryAndSubordinatesInternal".
        """
        all_users: set[str] = set()
        stack = [territory2_id]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            users = await self._users_in_territory(current)
            all_users.update(users)

            children = await self._child_territory_ids(current)
            stack.extend(children)

        logger.info(
            "[TerritoryHandler] Territory+Subordinates %s → %d user(s) across %d territory/territories",
            territory2_id,
            len(all_users),
            len(visited),
        )
        return all_users

    async def resolve_parent_territories(self, territory2_id: str) -> set[str]:
        """
        Return users in all *ancestor* territories above *territory2_id*.

        Walks upward via Territory2.ParentTerritory2Id until the root
        (ParentTerritory2Id is null) or a cycle is detected.
        Mirrors implicit sharing upward in the territory tree.
        """
        ancestor_ids = await self._collect_ancestor_territory_ids(territory2_id)
        if not ancestor_ids:
            logger.debug(
                "[TerritoryHandler] Territory %s has no parent territories", territory2_id
            )
            return set()

        all_users: set[str] = set()
        for ancestor_id in ancestor_ids:
            users = await self._users_in_territory(ancestor_id)
            all_users.update(users)

        logger.info(
            "[TerritoryHandler] Parent territories of %s → %d ancestor(s), %d user(s)",
            territory2_id,
            len(ancestor_ids),
            len(all_users),
        )
        return all_users

    # ── Private query helpers ─────────────────────────────────────────────────

    async def _users_in_territory(self, territory2_id: str) -> set[str]:
        """Fetch users assigned to *territory2_id* via UserTerritory2Association."""
        soql = (
            f"SELECT UserId "
            f"FROM UserTerritory2Association "
            f"WHERE Territory2Id = '{territory2_id}'"
        )
        records = await self._sf.query_all(soql)
        user_ids = {r["UserId"] for r in records if r.get("UserId")}
        logger.debug(
            "[TerritoryHandler] Territory %s → %d user(s)", territory2_id, len(user_ids)
        )
        return user_ids

    async def _child_territory_ids(self, territory2_id: str) -> list[str]:
        """Fetch direct child territories (one level down) of *territory2_id*."""
        soql = (
            f"SELECT Id FROM Territory2 "
            f"WHERE ParentTerritory2Id = '{territory2_id}'"
        )
        records = await self._sf.query_all(soql)
        return [r["Id"] for r in records if r.get("Id")]

    async def _get_parent_territory_id(self, territory2_id: str) -> Optional[str]:
        """Return the ParentTerritory2Id for *territory2_id*, or None at root."""
        soql = (
            f"SELECT Id, ParentTerritory2Id FROM Territory2 "
            f"WHERE Id = '{territory2_id}' LIMIT 1"
        )
        records = await self._sf.query_all(soql)
        if not records:
            return None
        return records[0].get("ParentTerritory2Id")

    async def _collect_ancestor_territory_ids(self, territory2_id: str) -> set[str]:
        """
        Walk upward from *territory2_id* collecting every ancestor territory ID.
        Stops when ParentTerritory2Id is null or a cycle is detected.
        """
        ancestors: set[str] = set()
        visited: set[str] = {territory2_id}
        current: Optional[str] = territory2_id

        while current:
            parent_id = await self._get_parent_territory_id(current)
            if not parent_id or parent_id in visited:
                break
            ancestors.add(parent_id)
            visited.add(parent_id)
            current = parent_id

        return ancestors
