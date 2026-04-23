"""Tests for graph.identity_store — SQLite state store for identity crawl."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.identity_store import (
    GroupDiff,
    IdentityStore,
    MemberEntry,
    SyncSessionStats,
)


@pytest.fixture
def store(tmp_path) -> IdentityStore:
    """Create a fresh IdentityStore backed by a temp SQLite DB."""
    db = tmp_path / "test_identity.db"
    return IdentityStore(db_path=db, connection_id="test-conn")


# ── MemberEntry tests ────────────────────────────────────────────────────────


class TestMemberEntry:
    def test_equality_by_id_and_type(self):
        a = MemberEntry("005A", "user", "external")
        b = MemberEntry("005A", "user", "azureActiveDirectory")
        assert a == b  # identity_source is NOT part of equality

    def test_inequality_different_id(self):
        a = MemberEntry("005A", "user")
        b = MemberEntry("005B", "user")
        assert a != b

    def test_inequality_different_type(self):
        a = MemberEntry("005A", "user")
        b = MemberEntry("005A", "externalGroup")
        assert a != b

    def test_hashable_in_set(self):
        s = {
            MemberEntry("005A", "user"),
            MemberEntry("005A", "user"),
            MemberEntry("005B", "user"),
        }
        assert len(s) == 2

    def test_frozen(self):
        m = MemberEntry("005A", "user")
        with pytest.raises(AttributeError):
            m.member_id = "005B"  # type: ignore[misc]


# ── Group CRUD tests ─────────────────────────────────────────────────────────


class TestGroupCrud:
    def test_group_exists_false_initially(self, store):
        assert not store.group_exists("G1")

    def test_upsert_and_exists(self, store):
        store.upsert_group("G1", "Group One")
        assert store.group_exists("G1")

    def test_upsert_updates_display_name(self, store):
        store.upsert_group("G1", "Old Name")
        store.upsert_group("G1", "New Name")
        # Still only one group
        assert store.get_all_group_ids() == {"G1"}

    def test_get_all_group_ids(self, store):
        store.upsert_group("G1")
        store.upsert_group("G2")
        store.upsert_group("G3")
        assert store.get_all_group_ids() == {"G1", "G2", "G3"}

    def test_delete_group(self, store):
        store.upsert_group("G1")
        store.delete_group("G1")
        assert not store.group_exists("G1")

    def test_delete_group_cascades_members(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))
        store.add_member("G1", MemberEntry("005B", "user"))
        store.delete_group("G1")
        assert store.get_members("G1") == set()

    def test_isolation_by_connection_id(self, tmp_path):
        db = tmp_path / "shared.db"
        store_a = IdentityStore(db_path=db, connection_id="conn-a")
        store_b = IdentityStore(db_path=db, connection_id="conn-b")

        store_a.upsert_group("G1", "A's group")
        store_b.upsert_group("G2", "B's group")

        assert store_a.get_all_group_ids() == {"G1"}
        assert store_b.get_all_group_ids() == {"G2"}

        store_a.close()
        store_b.close()


# ── Member CRUD tests ────────────────────────────────────────────────────────


class TestMemberCrud:
    def test_add_and_get_members(self, store):
        store.upsert_group("G1")
        m1 = MemberEntry("005A", "user")
        m2 = MemberEntry("GRP-Child", "externalGroup")
        store.add_member("G1", m1)
        store.add_member("G1", m2)
        members = store.get_members("G1")
        assert len(members) == 2
        assert m1 in members
        assert m2 in members

    def test_add_duplicate_member_is_ignored(self, store):
        store.upsert_group("G1")
        m = MemberEntry("005A", "user")
        store.add_member("G1", m)
        store.add_member("G1", m)  # Should not raise
        assert len(store.get_members("G1")) == 1

    def test_remove_member(self, store):
        store.upsert_group("G1")
        m = MemberEntry("005A", "user")
        store.add_member("G1", m)
        store.remove_member("G1", m)
        assert len(store.get_members("G1")) == 0

    def test_remove_nonexistent_member_is_safe(self, store):
        store.upsert_group("G1")
        store.remove_member("G1", MemberEntry("005X", "user"))  # No error

    def test_replace_members(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005OLD", "user"))
        store.add_member("G1", MemberEntry("005KEEP", "user"))

        new = {MemberEntry("005KEEP", "user"), MemberEntry("005NEW", "user")}
        store.replace_members("G1", new)

        members = store.get_members("G1")
        assert len(members) == 2
        member_ids = {m.member_id for m in members}
        assert "005KEEP" in member_ids
        assert "005NEW" in member_ids
        assert "005OLD" not in member_ids

    def test_empty_group_has_no_members(self, store):
        store.upsert_group("G1")
        assert store.get_members("G1") == set()


# ── Diff computation tests ───────────────────────────────────────────────────


class TestComputeDiff:
    def test_all_new_groups(self, store):
        new_groups = {
            "G1": ("Group 1", {MemberEntry("005A", "user")}),
            "G2": ("Group 2", {MemberEntry("005B", "user")}),
        }
        diffs = store.compute_diff(new_groups)
        actions = {d.group_id: d.action for d in diffs}
        assert actions == {"G1": "create", "G2": "create"}

    def test_all_stale_groups(self, store):
        store.upsert_group("STALE1")
        store.upsert_group("STALE2")
        diffs = store.compute_diff({})
        actions = {d.group_id: d.action for d in diffs}
        assert actions == {"STALE1": "delete", "STALE2": "delete"}

    def test_unchanged_group(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))

        new_groups = {"G1": ("G1", {MemberEntry("005A", "user")})}
        diffs = store.compute_diff(new_groups)
        assert len(diffs) == 1
        assert diffs[0].action == "unchanged"
        assert not diffs[0].has_changes

    def test_members_added(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))

        new_groups = {"G1": ("G1", {
            MemberEntry("005A", "user"),
            MemberEntry("005B", "user"),
        })}
        diffs = store.compute_diff(new_groups)
        assert len(diffs) == 1
        assert diffs[0].action == "update"
        assert len(diffs[0].members_to_add) == 1
        assert diffs[0].members_to_add[0].member_id == "005B"
        assert len(diffs[0].members_to_remove) == 0

    def test_members_removed(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))
        store.add_member("G1", MemberEntry("005B", "user"))

        new_groups = {"G1": ("G1", {MemberEntry("005A", "user")})}
        diffs = store.compute_diff(new_groups)
        assert len(diffs) == 1
        assert diffs[0].action == "update"
        assert len(diffs[0].members_to_remove) == 1
        assert diffs[0].members_to_remove[0].member_id == "005B"

    def test_mixed_add_and_remove(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))
        store.add_member("G1", MemberEntry("005B", "user"))

        new_groups = {"G1": ("G1", {
            MemberEntry("005A", "user"),
            MemberEntry("005C", "user"),
        })}
        diffs = store.compute_diff(new_groups)
        d = diffs[0]
        assert d.action == "update"
        added_ids = {m.member_id for m in d.members_to_add}
        removed_ids = {m.member_id for m in d.members_to_remove}
        assert added_ids == {"005C"}
        assert removed_ids == {"005B"}

    def test_mixed_create_update_delete_unchanged(self, store):
        store.upsert_group("KEEP_SAME")
        store.add_member("KEEP_SAME", MemberEntry("005A", "user"))

        store.upsert_group("WILL_UPDATE")
        store.add_member("WILL_UPDATE", MemberEntry("005X", "user"))

        store.upsert_group("WILL_DELETE")

        new_groups = {
            "KEEP_SAME": ("Same", {MemberEntry("005A", "user")}),
            "WILL_UPDATE": ("Updated", {MemberEntry("005Y", "user")}),
            "BRAND_NEW": ("New", {MemberEntry("005Z", "user")}),
        }
        diffs = store.compute_diff(new_groups)
        actions = {d.group_id: d.action for d in diffs}
        assert actions == {
            "KEEP_SAME": "unchanged",
            "WILL_UPDATE": "update",
            "BRAND_NEW": "create",
            "WILL_DELETE": "delete",
        }

    def test_api_calls_estimate(self, store):
        new_groups = {
            "G1": ("G1", {MemberEntry("005A", "user"), MemberEntry("005B", "user")}),
        }
        diffs = store.compute_diff(new_groups)
        d = diffs[0]
        assert d.action == "create"
        # 1 PUT group + 2 POST members = 3
        assert d.api_calls_needed == 3

    def test_unchanged_needs_zero_calls(self, store):
        store.upsert_group("G1")
        store.add_member("G1", MemberEntry("005A", "user"))

        diffs = store.compute_diff({"G1": ("G1", {MemberEntry("005A", "user")})})
        assert diffs[0].api_calls_needed == 0


# ── Sync session tests ───────────────────────────────────────────────────────


class TestSyncSession:
    def test_start_and_complete_session(self, store):
        sid = store.start_session()
        assert sid
        stats = SyncSessionStats(
            groups_created=2, groups_updated=1, groups_deleted=0,
            groups_unchanged=5, members_added=10, members_removed=3,
            api_calls_made=16, errors=0,
        )
        store.complete_session(sid, stats)

        last = store.get_last_session()
        assert last is not None
        assert last["session_id"] == sid
        assert last["status"] == "completed"
        assert last["crawl_type"] == "identity"
        assert last["groups_created"] == 2
        assert last["members_added"] == 10
        assert last["api_calls_made"] == 16

    def test_no_completed_session_returns_none(self, store):
        assert store.get_last_session() is None

    def test_failed_session_not_returned_as_last(self, store):
        sid = store.start_session()
        store.complete_session(sid, SyncSessionStats(), status="failed")
        assert store.get_last_session() is None

    def test_crawl_type_stored(self, store):
        sid = store.start_session(crawl_type="content")
        store.complete_session(sid, SyncSessionStats())
        last = store.get_last_session()
        assert last["crawl_type"] == "content"

    def test_filter_by_crawl_type(self, store):
        sid1 = store.start_session(crawl_type="identity")
        store.complete_session(sid1, SyncSessionStats(groups_created=5))

        sid2 = store.start_session(crawl_type="content")
        store.complete_session(sid2, SyncSessionStats(content_total_fetched=100, content_success=95))

        identity_last = store.get_last_session(crawl_type="identity")
        assert identity_last is not None
        assert identity_last["crawl_type"] == "identity"
        assert identity_last["groups_created"] == 5

        content_last = store.get_last_session(crawl_type="content")
        assert content_last is not None
        assert content_last["crawl_type"] == "content"
        assert content_last["content_total_fetched"] == 100
        assert content_last["content_success"] == 95

    def test_content_crawl_stats(self, store):
        sid = store.start_session(crawl_type="content")
        stats = SyncSessionStats(
            content_total_fetched=50,
            content_success=48,
            content_failed=2,
            content_deleted=3,
            content_acl_engine="GROUP",
            errors=2,
        )
        store.complete_session(sid, stats)

        last = store.get_last_session(crawl_type="content")
        assert last["content_total_fetched"] == 50
        assert last["content_success"] == 48
        assert last["content_failed"] == 2
        assert last["content_deleted"] == 3
        assert last["content_acl_engine"] == "GROUP"

    def test_dry_run_saved_session(self, store):
        sid = store.start_session(crawl_type="identity-dry-run")
        stats = SyncSessionStats(groups_created=10, members_added=25)
        store.complete_session(sid, stats)

        last = store.get_last_session(crawl_type="identity-dry-run")
        assert last is not None
        assert last["crawl_type"] == "identity-dry-run"
        assert last["groups_created"] == 10

    def test_sync_type_stored(self, store):
        sid = store.start_session(crawl_type="content", sync_type="incremental")
        store.complete_session(sid, SyncSessionStats(sync_type="incremental", content_total_fetched=10))
        last = store.get_last_session(crawl_type="content")
        assert last["sync_type"] == "incremental"
        assert last["content_total_fetched"] == 10

    def test_sync_type_defaults_to_full(self, store):
        sid = store.start_session(crawl_type="content")
        store.complete_session(sid, SyncSessionStats())
        last = store.get_last_session(crawl_type="content")
        assert last["sync_type"] == "full"

    def test_get_last_successful_content_crawl_time(self, store):
        # No sessions yet
        assert store.get_last_successful_content_crawl_time() is None

        # Add a completed content session
        sid = store.start_session(crawl_type="content", sync_type="full")
        store.complete_session(sid, SyncSessionStats(content_total_fetched=50))

        result = store.get_last_successful_content_crawl_time()
        assert result is not None
        # Should be a datetime object
        from datetime import datetime
        assert isinstance(result, datetime)

    def test_get_last_successful_content_crawl_time_ignores_identity(self, store):
        # Only identity session exists
        sid = store.start_session(crawl_type="identity")
        store.complete_session(sid, SyncSessionStats(groups_created=5))

        assert store.get_last_successful_content_crawl_time() is None


# ── Stats test ────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty_store_stats(self, store):
        stats = store.get_stats()
        assert stats == {"groups": 0, "members": 0}

    def test_stats_after_population(self, store):
        store.upsert_group("G1")
        store.upsert_group("G2")
        store.add_member("G1", MemberEntry("005A", "user"))
        store.add_member("G1", MemberEntry("005B", "user"))
        store.add_member("G2", MemberEntry("005C", "user"))

        stats = store.get_stats()
        assert stats == {"groups": 2, "members": 3}
