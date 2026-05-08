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
from acl_engine.principal_mapper import PrincipalMapper, _looks_like_guid

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
    principal_mapper : ``PrincipalMapper`` for resolving Salesforce User IDs
                       to AAD GUIDs / UPNs.  Required for user members to be
                       accepted by the Graph external groups API.
    """

    def __init__(
        self,
        graph_client: GraphClient,
        connection_id: str,
        store: IdentityStore | None = None,
        principal_mapper: PrincipalMapper | None = None,
    ) -> None:
        self._client = graph_client
        self._connection_id = connection_id
        self._store = store or create_store(connection_id)
        # External groups API endpoint
        self._base_path = f"{EXTERNAL_CONNECTIONS_PATH}/{quote(connection_id, safe='')}"
        self._principal_mapper = principal_mapper

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
            # Step 1: Convert crawl result to flat map, resolving SF user IDs → AAD
            import asyncio
            new_groups = asyncio.run(self._flatten_crawl_result_async(crawl_result))

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

            # Step 3: Apply diffs in two passes
            # Pass 1: Create all new groups and delete stale ones (no members yet)
            # This ensures child groups exist before parents try to reference them.
            for diff in diffs:
                if diff.action == "create":
                    if self._create_group_only(diff.group_id, diff.display_name):
                        stats.groups_created += 1
                        stats.api_calls_made += 1
                    else:
                        stats.errors += 1
                elif diff.action == "delete":
                    self._apply_delete(diff, stats)

            # Pass 2: Add/remove members for created and updated groups
            for diff in diffs:
                if diff.action == "create":
                    all_members = new_groups[diff.group_id][1]
                    added = self._add_members(diff.group_id, diff.members_to_add)
                    stats.members_added += added
                    stats.api_calls_made += len(diff.members_to_add)
                    if added < len(diff.members_to_add):
                        stats.errors += len(diff.members_to_add) - added
                    self._store.upsert_group(diff.group_id, diff.display_name)
                    self._store.replace_members(diff.group_id, all_members)
                elif diff.action == "update":
                    self._apply_update(diff, stats, new_groups)
                # "unchanged" and "delete" → skip in pass 2

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

    async def _flatten_crawl_result_async(
        self,
        crawl_result: IdentityCrawlResult,
    ) -> dict[str, tuple[str, set[MemberEntry]]]:
        """
        Convert an ``IdentityCrawlResult`` into a flat dict suitable for
        diff computation, resolving Salesforce User IDs to AAD identifiers.

        Uses ``PrincipalMapper`` (when available) to resolve each SF user ID
        to an AAD GUID, UPN, or email.  The Graph external groups API requires
        ``identitySource: azureActiveDirectory`` for user members — raw
        Salesforce IDs (``005...``) are not accepted.

        Returns ``{group_id: (display_name, {MemberEntry, ...})}``
        """
        # If no PrincipalMapper, fall back to static method (dry-run / tests)
        if not self._principal_mapper:
            return self._flatten_crawl_result(crawl_result)

        # Collect all unique SF user IDs across all groups
        all_user_ids: set[str] = set()
        for membership in crawl_result.gathered_groups:
            for user in membership.users:
                all_user_ids.add(user.id)

        # Bulk-resolve SF user IDs → AAD identifiers via PrincipalMapper
        resolved_map: dict[str, str] = {}
        if self._principal_mapper and all_user_ids:
            # Pre-warm the mapper's cache with all user IDs at once
            await self._principal_mapper.prewarm_users(all_user_ids)

            # Resolve each user to an AAD identifier
            for uid in all_user_ids:
                details = self._principal_mapper._user_details_cache.get(uid)
                if details:
                    import asyncio
                    aad_id = await asyncio.to_thread(
                        self._principal_mapper._resolve_principal, details
                    )
                    if aad_id:
                        resolved_map[uid] = aad_id

            logger.info(
                "[IdentityPublisher] Resolved %d/%d SF user IDs to AAD identifiers",
                len(resolved_map), len(all_user_ids),
            )

        # Build the flat group map
        groups: dict[str, tuple[str, set[MemberEntry]]] = {}

        for membership in crawl_result.gathered_groups:
            members: set[MemberEntry] = set()

            for user in membership.users:
                aad_id = resolved_map.get(user.id)
                if aad_id and _looks_like_guid(aad_id):
                    # Resolved to AAD GUID — the only format Graph accepts for users
                    members.add(MemberEntry(
                        member_id=aad_id,
                        member_type="user",
                        identity_source="azureActiveDirectory",
                    ))
                else:
                    # Could not resolve to GUID — skip this user
                    # Graph rejects emails/UPNs with "InvalidGuidForAadMemberType"
                    logger.debug(
                        "[IdentityPublisher] Skipping user %s — no AAD GUID resolved (got: %s)",
                        user.id, aad_id,
                    )

            # Add child group members (always external identity source)
            for child in membership.child_groups:
                members.add(MemberEntry(
                    member_id=child.group_id,
                    member_type="externalGroup",
                    identity_source="external",
                ))

            groups[membership.group_id] = (membership.display_name, members)

        return groups

    @staticmethod
    def _flatten_crawl_result(
        crawl_result: IdentityCrawlResult,
    ) -> dict[str, tuple[str, set[MemberEntry]]]:
        """
        Synchronous fallback for flattening without AAD resolution.
        Used by identity-dry-run (no Graph client available).

        Only emits user members whose identifier looks like a valid AAD GUID.
        Non-GUID identifiers (emails, UPNs, SF IDs) are skipped because
        the Graph external groups API rejects them.

        Returns ``{group_id: (display_name, {MemberEntry, ...})}``
        """
        groups: dict[str, tuple[str, set[MemberEntry]]] = {}

        for membership in crawl_result.gathered_groups:
            members: set[MemberEntry] = set()

            # Add user members — only GUIDs are valid for Graph
            for user in membership.users:
                # Try federation_identifier, then email, then user_name
                candidate = user.federation_identifier or user.email or user.user_name
                if candidate and _looks_like_guid(candidate):
                    members.add(MemberEntry(
                        member_id=candidate,
                        member_type="user",
                        identity_source="azureActiveDirectory",
                    ))
                # else: skip — non-GUID identifiers are rejected by Graph

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

    def _create_group_only(self, group_id: str, display_name: str) -> bool:
        """
        Create an external group (without adding members).

        ``POST /external/connections/{connId}/groups``

        Called in Pass 1 to ensure all groups exist before Pass 2 adds
        members (child group references require the target group to exist).
        """
        return self._put_group(group_id, display_name)

    def _put_group(self, group_id: str, display_name: str) -> bool:
        """
        Create an external group.

        ``POST /external/connections/{connId}/groups``
        """
        url = f"{self._base_path}/groups"
        payload = {
            "id": group_id,
            "displayName": display_name or group_id,
        }
        try:
            self._client.post(url, json_body=payload)
            logger.info("[IdentityPublisher] POST group %s", group_id)
            return True
        except GraphApiError as e:
            if e.status_code == 409:
                # Group already exists — treat as success
                logger.info("[IdentityPublisher] Group %s already exists (409)", group_id)
                return True
            logger.error("[IdentityPublisher] POST group %s failed [%s]: %s", group_id, e.status_code, e)
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
    Build the JSON payload for adding a member to an external group.

    The Microsoft Graph external groups API accepts these formats:

    AAD users (id MUST be an AAD Object GUID)::

        {
            "id": "<aad-object-guid>",
            "type": "user",
            "identitySource": "azureActiveDirectory"
        }

    External groups (nested)::

        {
            "id": "<group_id>",
            "type": "externalGroup",
            "identitySource": "external"
        }

    Note: ``@odata.type`` is NOT needed.  AAD users require the GUID — emails
    and UPNs are rejected with ``InvalidGuidForAadMemberType``.
    """
    if member.member_type == "externalGroup":
        return {
            "id": member.member_id,
            "type": "externalGroup",
            "identitySource": "external",
        }
    else:
        return {
            "id": member.member_id,
            "type": member.member_type,
            "identitySource": member.identity_source,
        }
