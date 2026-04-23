"""
graph/identity_publisher.py
----------------------------
Publishes Identity Crawl results to Microsoft Graph, using a SQLite-backed
store to track state and minimize API calls.

Architecture
------------
::

    IdentityCrawlResult (from identity_sync.py)
            │
            ▼
    IdentityPublisher.publish()
            │
            ├── 1. Convert crawl result → flat {group_id: (name, members)} dict
            │
            ├── 2. IdentityStore.compute_diff()
            │      Compare new state vs stored state → list[GroupDiff]
            │
            ├── 3. Apply diffs (selective Graph API calls)
            │      ├── create  → PUT group + POST each member
            │      ├── update  → POST new members + DELETE removed members
            │      ├── delete  → DELETE group
            │      └── unchanged → skip (no API call)
            │
            ├── 4. Update SQLite store on success
            │
            └── 5. Record sync session stats

Graph API endpoints used
------------------------
``PUT  /external/connections/{id}/groups/{groupId}``
    Create or replace an external group.

``DELETE /external/connections/{id}/groups/{groupId}``
    Delete an external group and all its members.

``POST /external/connections/{id}/groups/{groupId}/members``
    Add a member (user or nested group) to an external group.

``DELETE /external/connections/{id}/groups/{groupId}/members/{memberId}``
    Remove a single member from an external group.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from graph.client import GraphApiError, GraphClient, EXTERNAL_CONNECTIONS_PATH
from graph.identity_store import (
    GroupDiff,
    IdentityStore,
    MemberEntry,
    SyncSessionStats,
    create_store,
)
from acl_engine.identity_sync import (
    ChildGroupRef,
    GroupMembership,
    IdentityCrawlResult,
)
from acl_engine.identity_models import SfUser

logger = logging.getLogger("salesforce_connector.identity_publisher")


# ── Publisher ─────────────────────────────────────────────────────────────────

class IdentityPublisher:
    """
    Publishes identity crawl results to Microsoft Graph with change-aware
    optimization via SQLite state tracking.

    Parameters
    ----------
    graph_client   : Authenticated ``GraphClient`` instance.
    connection_id  : The external connection ID in Microsoft Graph.
    store          : ``IdentityStore`` for state tracking.  If None, one is
                     created automatically using ``connection_id``.
    """

    def __init__(
        self,
        graph_client: GraphClient,
        connection_id: str,
        store: IdentityStore | None = None,
    ) -> None:
        self._client = graph_client
        self._connection_id = connection_id
        self._store = store or create_store(connection_id)
        self._base_path = f"{EXTERNAL_CONNECTIONS_PATH}/{quote(connection_id, safe='')}"

    # ── Public API ────────────────────────────────────────────────────────────

    def publish(self, crawl_result: IdentityCrawlResult) -> SyncSessionStats:
        """
        Publish an identity crawl result to Microsoft Graph.

        Compares the crawl result against the SQLite store, computes a minimal
        diff, and makes only the necessary Graph API calls.

        Returns ``SyncSessionStats`` with counts of what was done.
        """
        session_id = self._store.start_session()
        stats = SyncSessionStats(session_id=session_id)

        try:
            # Step 1: Convert crawl result to flat map
            new_groups = self._flatten_crawl_result(crawl_result)

            logger.info(
                "[IdentityPublisher] Crawl produced %d group(s) with members",
                len(new_groups),
            )

            # Step 2: Compute diff against stored state
            diffs = self._store.compute_diff(new_groups)

            changed = [d for d in diffs if d.has_changes]
            unchanged = [d for d in diffs if not d.has_changes]
            stats.groups_unchanged = len(unchanged)

            total_api_calls = sum(d.api_calls_needed for d in changed)
            logger.info(
                "[IdentityPublisher] Diff: %d create, %d update, %d delete, %d unchanged "
                "(~%d API calls needed)",
                sum(1 for d in diffs if d.action == "create"),
                sum(1 for d in diffs if d.action == "update"),
                sum(1 for d in diffs if d.action == "delete"),
                stats.groups_unchanged,
                total_api_calls,
            )

            # Step 3: Apply diffs
            for diff in diffs:
                if diff.action == "create":
                    self._apply_create(diff, stats, new_groups)
                elif diff.action == "update":
                    self._apply_update(diff, stats, new_groups)
                elif diff.action == "delete":
                    self._apply_delete(diff, stats)
                # "unchanged" → skip

            # Step 4: Complete session
            self._store.complete_session(session_id, stats, status="completed")

            logger.info(
                "[IdentityPublisher] Sync complete: "
                "created=%d updated=%d deleted=%d unchanged=%d "
                "members_added=%d members_removed=%d "
                "api_calls=%d errors=%d",
                stats.groups_created, stats.groups_updated,
                stats.groups_deleted, stats.groups_unchanged,
                stats.members_added, stats.members_removed,
                stats.api_calls_made, stats.errors,
            )

        except Exception:
            self._store.complete_session(session_id, stats, status="failed")
            raise

        return stats

    # ── Crawl result conversion ───────────────────────────────────────────────

    @staticmethod
    def _flatten_crawl_result(
        crawl_result: IdentityCrawlResult,
    ) -> dict[str, tuple[str, set[MemberEntry]]]:
        """
        Convert an ``IdentityCrawlResult`` into a flat dict suitable for
        diff computation.

        Returns ``{group_id: (display_name, {MemberEntry, ...})}``
        """
        groups: dict[str, tuple[str, set[MemberEntry]]] = {}

        for membership in crawl_result.gathered_groups:
            members: set[MemberEntry] = set()

            # Add user members
            for user in membership.users:
                if user.federation_identifier:
                    members.add(MemberEntry(
                        member_id=user.federation_identifier,
                        member_type="user",
                        identity_source="azureActiveDirectory",
                    ))
                else:
                    members.add(MemberEntry(
                        member_id=user.id,
                        member_type="user",
                        identity_source="external",
                    ))

            # Add child group members
            for child in membership.child_groups:
                members.add(MemberEntry(
                    member_id=child.group_id,
                    member_type="externalGroup",
                    identity_source="external",
                ))

            groups[membership.group_id] = (membership.display_name, members)

        return groups

    # ── Diff application ──────────────────────────────────────────────────────

    def _apply_create(
        self,
        diff: GroupDiff,
        stats: SyncSessionStats,
        new_groups: dict[str, tuple[str, set[MemberEntry]]],
    ) -> None:
        """Create a new group in Graph and add all its members."""
        group_id = diff.group_id

        # PUT the group
        if not self._put_group(group_id, diff.display_name):
            stats.errors += 1
            return

        stats.api_calls_made += 1
        stats.groups_created += 1

        # POST each member
        all_members = new_groups[group_id][1]
        added = self._add_members(group_id, diff.members_to_add)
        stats.members_added += added
        stats.api_calls_made += len(diff.members_to_add)
        if added < len(diff.members_to_add):
            stats.errors += len(diff.members_to_add) - added

        # Update store: record group and all successfully-derived members
        self._store.upsert_group(group_id, diff.display_name)
        self._store.replace_members(group_id, all_members)

    def _apply_update(
        self,
        diff: GroupDiff,
        stats: SyncSessionStats,
        new_groups: dict[str, tuple[str, set[MemberEntry]]],
    ) -> None:
        """Apply member additions and removals to an existing group."""
        group_id = diff.group_id

        # Add new members
        added = self._add_members(group_id, diff.members_to_add)
        stats.members_added += added
        stats.api_calls_made += len(diff.members_to_add)
        if added < len(diff.members_to_add):
            stats.errors += len(diff.members_to_add) - added

        # Remove stale members
        removed = self._remove_members(group_id, diff.members_to_remove)
        stats.members_removed += removed
        stats.api_calls_made += len(diff.members_to_remove)
        if removed < len(diff.members_to_remove):
            stats.errors += len(diff.members_to_remove) - removed

        stats.groups_updated += 1

        # Update store with new complete membership
        all_members = new_groups[group_id][1]
        self._store.upsert_group(group_id, diff.display_name)
        self._store.replace_members(group_id, all_members)

    def _apply_delete(self, diff: GroupDiff, stats: SyncSessionStats) -> None:
        """Delete a group from Graph and remove it from the store."""
        group_id = diff.group_id

        if self._delete_group(group_id):
            stats.groups_deleted += 1
            self._store.delete_group(group_id)
        else:
            stats.errors += 1

        stats.api_calls_made += 1

    # ── Graph API wrappers ────────────────────────────────────────────────────

    def _put_group(self, group_id: str, display_name: str) -> bool:
        """
        Create or replace an external group.

        ``PUT /external/connections/{connId}/groups/{groupId}``
        """
        url = f"{self._base_path}/groups/{quote(group_id, safe='')}"
        payload = {
            "id": group_id,
            "displayName": display_name or group_id,
            "description": f"Salesforce sharing group: {group_id}",
        }
        try:
            self._client.put(url, json_body=payload)
            logger.info("[IdentityPublisher] PUT group %s", group_id)
            return True
        except GraphApiError as e:
            logger.error("[IdentityPublisher] PUT group %s failed: %s", group_id, e)
            return False

    def _delete_group(self, group_id: str) -> bool:
        """
        Delete an external group.

        ``DELETE /external/connections/{connId}/groups/{groupId}``
        """
        url = f"{self._base_path}/groups/{quote(group_id, safe='')}"
        try:
            self._client.delete(url)
            logger.info("[IdentityPublisher] DELETE group %s", group_id)
            return True
        except GraphApiError as e:
            if e.status_code == 404:
                logger.warning("[IdentityPublisher] Group %s already gone (404)", group_id)
                return True  # Already deleted — treat as success
            logger.error("[IdentityPublisher] DELETE group %s failed: %s", group_id, e)
            return False

    def _add_members(self, group_id: str, members: list[MemberEntry]) -> int:
        """
        Add members to a group one at a time.

        ``POST /external/connections/{connId}/groups/{groupId}/members``

        Returns the count of successfully added members.
        """
        url = f"{self._base_path}/groups/{quote(group_id, safe='')}/members"
        success = 0

        for member in members:
            payload = _build_member_payload(member)
            try:
                self._client.post(url, json_body=payload)
                success += 1
            except GraphApiError as e:
                if e.status_code == 409:
                    # Member already exists — treat as success
                    logger.debug(
                        "[IdentityPublisher] Member %s already in %s (409)",
                        member.member_id, group_id,
                    )
                    success += 1
                else:
                    logger.error(
                        "[IdentityPublisher] Add member %s to %s failed: %s",
                        member.member_id, group_id, e,
                    )

        if members:
            logger.info(
                "[IdentityPublisher] Added %d/%d member(s) to %s",
                success, len(members), group_id,
            )
        return success

    def _remove_members(self, group_id: str, members: list[MemberEntry]) -> int:
        """
        Remove members from a group one at a time.

        ``DELETE /external/connections/{connId}/groups/{groupId}/members/{memberId}``

        Returns the count of successfully removed members.
        """
        success = 0

        for member in members:
            url = (
                f"{self._base_path}/groups/{quote(group_id, safe='')}"
                f"/members/{quote(member.member_id, safe='')}"
            )
            try:
                self._client.delete(url)
                success += 1
            except GraphApiError as e:
                if e.status_code == 404:
                    logger.debug(
                        "[IdentityPublisher] Member %s already removed from %s (404)",
                        member.member_id, group_id,
                    )
                    success += 1
                else:
                    logger.error(
                        "[IdentityPublisher] Remove member %s from %s failed: %s",
                        member.member_id, group_id, e,
                    )

        if members:
            logger.info(
                "[IdentityPublisher] Removed %d/%d member(s) from %s",
                success, len(members), group_id,
            )
        return success


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_member_payload(member: MemberEntry) -> dict[str, str]:
    """
    Build the JSON payload for a POST member call.

    External users::

        {"id": "<sf_user_id>", "type": "user", "identitySource": "external"}

    AAD users::

        {"id": "<email>", "type": "user", "identitySource": "azureActiveDirectory"}

    Nested groups::

        {"id": "<group_format_id>", "type": "externalGroup", "identitySource": "external"}
    """
    return {
        "id": member.member_id,
        "type": member.member_type,
        "identitySource": member.identity_source,
    }
