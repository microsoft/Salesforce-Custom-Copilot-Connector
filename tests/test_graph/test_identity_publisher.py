# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for graph.identity_publisher — Graph API publishing with SQLite diff."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from dataclasses import field

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.client import GraphApiError
from graph.identity_store import IdentityStore, MemberEntry, SyncSessionStats
from graph.identity_publisher import IdentityPublisher, _build_member_payload
from acl_engine.identity_models import SfUser, GroupIdentityType
from acl_engine.identity_sync import (
    ChildGroupRef,
    GroupMembership,
    IdentityCrawlResult,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


import hashlib


def _user_guid(user_id: str) -> str:
    """Return the deterministic GUID that _make_user generates for *user_id*."""
    h = hashlib.md5(user_id.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _make_user(user_id: str = "005A", federation_id: str = "", email: str = "") -> SfUser:
    """Create a test user with a deterministic GUID-format federation_identifier
    so the static _flatten_crawl_result (GUID-only filter) keeps the user."""
    fake_guid = _user_guid(user_id)
    return SfUser(
        id=user_id,
        name="User",
        email=email or f"{user_id.lower()}@test.com",
        federation_identifier=federation_id or fake_guid,
    )


def _make_crawl_result(groups: list[GroupMembership]) -> IdentityCrawlResult:
    return IdentityCrawlResult(
        top_level_groups=[],
        gathered_groups=groups,
        total_users_emitted=sum(len(g.users) for g in groups),
        total_groups_emitted=len(groups),
    )


@pytest.fixture
def store(tmp_path) -> IdentityStore:
    db = tmp_path / "pub_test.db"
    return IdentityStore(db_path=db, connection_id="test-conn")


@pytest.fixture
def graph_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def publisher(graph_client, store) -> IdentityPublisher:
    return IdentityPublisher(
        graph_client=graph_client,
        connection_id="test-conn",
        store=store,
    )


# ── _build_member_payload tests ──────────────────────────────────────────────


class TestBuildMemberPayload:
    def test_external_user(self):
        m = MemberEntry("005A", "user", "external")
        payload = _build_member_payload(m)
        assert payload == {"id": "005A", "type": "user", "identitySource": "external"}

    def test_aad_user(self):
        m = MemberEntry("f1126041-cb51-4f20-82d5-722b4cfcdfa1", "user", "azureActiveDirectory")
        payload = _build_member_payload(m)
        assert payload == {
            "id": "f1126041-cb51-4f20-82d5-722b4cfcdfa1",
            "type": "user",
            "identitySource": "azureActiveDirectory",
        }

    def test_external_group(self):
        m = MemberEntry("AccountTopLevel", "externalGroup", "external")
        payload = _build_member_payload(m)
        assert payload == {"id": "AccountTopLevel", "type": "externalGroup", "identitySource": "external"}


# ── _flatten_crawl_result tests ──────────────────────────────────────────────


class TestFlattenCrawlResult:
    def test_users_become_user_members(self):
        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1",
                display_name="Group One",
                users=[_make_user("005A"), _make_user("005B")],
            ),
        ])
        flat = IdentityPublisher._flatten_crawl_result(crawl)
        assert "G1" in flat
        name, members = flat["G1"]
        assert name == "Group One"
        assert len(members) == 2
        ids = {m.member_id for m in members}
        assert ids == {_user_guid("005A"), _user_guid("005B")}
        # All user members should be azureActiveDirectory
        assert all(m.identity_source == "azureActiveDirectory" for m in members)

    def test_aad_users_use_federation_id(self):
        aad_guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1",
                users=[_make_user("005A", federation_id=aad_guid)],
            ),
        ])
        flat = IdentityPublisher._flatten_crawl_result(crawl)
        members = flat["G1"][1]
        m = next(iter(members))
        assert m.member_id == aad_guid
        assert m.identity_source == "azureActiveDirectory"

    def test_non_guid_federation_id_is_skipped(self):
        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1",
                users=[_make_user("005A", federation_id="alice@tenant.com")],
            ),
        ])
        flat = IdentityPublisher._flatten_crawl_result(crawl)
        user_members = [m for m in flat["G1"][1] if m.member_type == "user"]
        assert len(user_members) == 0  # email filtered out

    def test_child_groups_become_external_group_members(self):
        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1",
                child_groups=[
                    ChildGroupRef("Child1", GroupIdentityType.ROLE_WITH_PARENT, needs_gather=False),
                ],
            ),
        ])
        flat = IdentityPublisher._flatten_crawl_result(crawl)
        members = flat["G1"][1]
        m = next(iter(members))
        assert m.member_id == "Child1"
        assert m.member_type == "externalGroup"


# ── Publish with fresh store (all creates) ───────────────────────────────────


class TestPublishAllNew:
    def test_creates_groups_and_adds_members(self, publisher, graph_client, store):
        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1", display_name="Group 1",
                users=[_make_user("005A"), _make_user("005B")],
            ),
        ])
        stats = publisher.publish(crawl)

        assert stats.groups_created == 1
        assert stats.members_added == 2
        assert stats.groups_deleted == 0
        assert stats.errors == 0

        # Verify Graph API calls: 1 POST for group creation + 2 POSTs for members = 3
        post_calls = [c for c in graph_client.post.call_args_list]
        assert len(post_calls) == 3
        # First call is group creation (URL ends with /groups)
        assert post_calls[0].args[0].endswith("/groups")

        # Verify store was updated
        assert store.group_exists("G1")
        assert len(store.get_members("G1")) == 2

    def test_session_recorded(self, publisher, store):
        crawl = _make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user()]),
        ])
        publisher.publish(crawl)

        session = store.get_last_session()
        assert session is not None
        assert session["status"] == "completed"
        assert session["groups_created"] == 1


# ── Publish with no changes ──────────────────────────────────────────────────


class TestPublishUnchanged:
    def test_no_api_calls_when_unchanged(self, publisher, graph_client, store):
        # Pre-populate store with email-based member (matching what flatten produces)
        store.upsert_group("G1", "Group 1")
        store.add_member("G1", MemberEntry(_user_guid("005A"), "user", "azureActiveDirectory"))

        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1", display_name="Group 1",
                users=[_make_user("005A")],
            ),
        ])
        stats = publisher.publish(crawl)

        assert stats.groups_unchanged == 1
        assert stats.groups_created == 0
        assert stats.groups_updated == 0
        assert stats.api_calls_made == 0
        graph_client.put.assert_not_called()
        graph_client.post.assert_not_called()
        graph_client.delete.assert_not_called()


# ── Publish with member changes ──────────────────────────────────────────────


class TestPublishUpdate:
    def test_adds_new_members_removes_stale(self, publisher, graph_client, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry(_user_guid("005A"), "user", "azureActiveDirectory"))
        store.add_member("G1", MemberEntry(_user_guid("005B"), "user", "azureActiveDirectory"))

        crawl = _make_crawl_result([
            GroupMembership(
                group_id="G1",
                users=[_make_user("005A"), _make_user("005C")],
            ),
        ])
        stats = publisher.publish(crawl)

        assert stats.groups_updated == 1
        assert stats.members_added == 1   # 005c@test.com
        assert stats.members_removed == 1  # 005b@test.com

        # Store should have the new membership
        member_ids = {m.member_id for m in store.get_members("G1")}
        assert member_ids == {_user_guid("005A"), _user_guid("005C")}


# ── Publish with stale groups ────────────────────────────────────────────────


class TestPublishDelete:
    def test_deletes_stale_groups(self, publisher, graph_client, store):
        store.upsert_group("STALE")
        store.add_member("STALE", MemberEntry("005X", "user"))

        crawl = _make_crawl_result([])  # No groups in new crawl

        stats = publisher.publish(crawl)

        assert stats.groups_deleted == 1
        graph_client.delete.assert_called_once()
        assert not store.group_exists("STALE")

    def test_delete_404_treated_as_success(self, publisher, graph_client, store):
        store.upsert_group("GONE")
        graph_client.delete.side_effect = GraphApiError(404, "Not found")

        crawl = _make_crawl_result([])
        stats = publisher.publish(crawl)

        assert stats.groups_deleted == 1
        assert stats.errors == 0
        assert not store.group_exists("GONE")


# ── Error handling ────────────────────────────────────────────────────────────


class TestPublishErrors:
    def test_put_failure_counts_as_error(self, publisher, graph_client, store):
        graph_client.post.side_effect = GraphApiError(500, "Internal error")

        crawl = _make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user()]),
        ])
        stats = publisher.publish(crawl)

        assert stats.errors >= 1
        assert stats.groups_created == 0

    def test_member_add_failure_counted(self, publisher, graph_client, store):
        # First post call succeeds (group creation), subsequent ones fail (member add)
        call_count = [0]
        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {}  # group creation succeeds
            raise GraphApiError(500, "error")
        graph_client.post.side_effect = post_side_effect

        crawl = _make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user("005A")]),
        ])
        stats = publisher.publish(crawl)

        assert stats.errors >= 1
        assert stats.members_added == 0

    def test_member_409_treated_as_success(self, publisher, graph_client, store):
        # First post = group creation (success), second = member add (409)
        call_count = [0]
        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {}  # group creation
            raise GraphApiError(409, "Conflict")
        graph_client.post.side_effect = post_side_effect

        crawl = _make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user("005A")]),
        ])
        stats = publisher.publish(crawl)

        assert stats.members_added == 1
        assert stats.errors == 0

    def test_session_marked_failed_on_exception(self, publisher, graph_client, store):
        graph_client.post.side_effect = RuntimeError("boom")

        crawl = _make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user()]),
        ])
        with pytest.raises(RuntimeError):
            publisher.publish(crawl)

        # The session should be recorded as failed
        # (get_last_session only returns completed, so it should be None)
        assert store.get_last_session() is None


# ── End-to-end scenario ──────────────────────────────────────────────────────


class TestEndToEnd:
    def test_two_crawls_second_is_incremental(self, publisher, graph_client, store):
        """Simulate two crawls: first creates everything, second only applies changes."""
        # Crawl 1: initial
        crawl1 = _make_crawl_result([
            GroupMembership(
                group_id="AccountTopLevel", display_name="Account",
                users=[_make_user("005A"), _make_user("005B"), _make_user("005C")],
            ),
            GroupMembership(
                group_id="AccountGlobalUsers", display_name="GlobalUsers",
                users=[_make_user("005ADMIN")],
            ),
        ])
        stats1 = publisher.publish(crawl1)
        assert stats1.groups_created == 2
        assert stats1.members_added == 4

        # Reset mock call counts
        graph_client.reset_mock()

        # Crawl 2: 005B removed, 005D added, GlobalUsers unchanged
        crawl2 = _make_crawl_result([
            GroupMembership(
                group_id="AccountTopLevel", display_name="Account",
                users=[_make_user("005A"), _make_user("005C"), _make_user("005D")],
            ),
            GroupMembership(
                group_id="AccountGlobalUsers", display_name="GlobalUsers",
                users=[_make_user("005ADMIN")],
            ),
        ])
        stats2 = publisher.publish(crawl2)

        assert stats2.groups_created == 0
        assert stats2.groups_updated == 1  # Only TopLevel changed
        assert stats2.groups_unchanged == 1  # GlobalUsers unchanged
        assert stats2.members_added == 1   # 005d@test.com
        assert stats2.members_removed == 1  # 005b@test.com

        # No PUT calls (no new groups), only POST (add) and DELETE (remove)
        graph_client.put.assert_not_called()
        assert graph_client.post.call_count == 1   # add 005d
        assert graph_client.delete.call_count == 1  # remove 005b

        # Final store state
        tl_members = {m.member_id for m in store.get_members("AccountTopLevel")}
        assert tl_members == {_user_guid("005A"), _user_guid("005C"), _user_guid("005D")}

    def test_three_crawls_group_lifecycle(self, publisher, graph_client, store):
        """Group created in crawl 1, updated in crawl 2, deleted in crawl 3."""
        # Crawl 1: create
        stats1 = publisher.publish(_make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user("005A")]),
        ]))
        assert stats1.groups_created == 1
        graph_client.reset_mock()

        # Crawl 2: update membership
        stats2 = publisher.publish(_make_crawl_result([
            GroupMembership(group_id="G1", users=[_make_user("005B")]),
        ]))
        assert stats2.groups_updated == 1
        graph_client.reset_mock()

        # Crawl 3: group gone
        stats3 = publisher.publish(_make_crawl_result([]))
        assert stats3.groups_deleted == 1
        assert not store.group_exists("G1")
