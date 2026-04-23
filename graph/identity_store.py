"""
graph/identity_store.py
-----------------------
SQLite-backed state store for Identity Crawl group membership.

Maintains a persistent record of which external groups exist and who their
members are.  On each crawl the publisher compares the new crawl result
against this store to compute a minimal diff — only changed groups/members
trigger Microsoft Graph API calls.

Database location
-----------------
One SQLite file per connection, stored at::

    {repo_root}/data/{connection_id}_identity.db

The ``data/`` directory is created on first use.

Schema
------
``groups``
    One row per external group that has been published to Graph.

``group_members``
    One row per member (user or nested group) of an external group.

``sync_sessions``
    Audit log of every identity sync run with aggregate stats.

Thread safety
-------------
SQLite in WAL mode with ``check_same_thread=False`` — safe for the
single-writer / multiple-reader pattern used by the connector.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("salesforce_connector.identity_store")


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemberEntry:
    """A single member of an external group."""
    member_id: str
    member_type: str  # "user" or "externalGroup"
    identity_source: str = "external"  # "external" or "azureActiveDirectory"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MemberEntry):
            return NotImplemented
        return self.member_id == other.member_id and self.member_type == other.member_type

    def __hash__(self) -> int:
        return hash((self.member_id, self.member_type))


@dataclass
class GroupDiff:
    """Change set for one external group."""
    group_id: str
    action: str  # "create", "update", "delete", "unchanged"
    display_name: str = ""
    members_to_add: list[MemberEntry] = field(default_factory=list)
    members_to_remove: list[MemberEntry] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return self.action != "unchanged"

    @property
    def api_calls_needed(self) -> int:
        """Estimate number of Graph API calls this diff will produce."""
        if self.action == "unchanged":
            return 0
        if self.action == "delete":
            return 1
        calls = 0
        if self.action == "create":
            calls += 1  # PUT group
        if self.members_to_add:
            calls += len(self.members_to_add)  # one POST per member
        if self.members_to_remove:
            calls += len(self.members_to_remove)  # one DELETE per member
        return calls


@dataclass
class SyncSessionStats:
    """Aggregate stats for one sync session (identity or content crawl).

    Attributes
    ----------
    session_id : Unique session identifier (UUID).
    sync_type  : ``"full"`` or ``"incremental"``.

    Identity crawl stats (groups_* / members_* / api_calls_made):
        Populated by ``IdentityPublisher.publish()`` for identity crawl sessions.

    Content crawl stats (content_*):
        Populated by ``record_content_crawl()`` after ``ingest_content()``.

    errors : Total error count across both identity and content phases.
    """
    session_id: str = ""
    sync_type: str = "full"  # "full" or "incremental"
    # Identity crawl stats
    groups_created: int = 0
    groups_updated: int = 0
    groups_deleted: int = 0
    groups_unchanged: int = 0
    members_added: int = 0
    members_removed: int = 0
    api_calls_made: int = 0
    errors: int = 0
    # Content crawl stats
    content_total_fetched: int = 0
    content_success: int = 0
    content_failed: int = 0
    content_deleted: int = 0
    content_acl_engine: str = ""


# ── Schema DDL ────────────────────────────────────────────────────────────────

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS groups (
    group_id        TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id        TEXT NOT NULL,
    member_id       TEXT NOT NULL,
    member_type     TEXT NOT NULL,
    identity_source TEXT NOT NULL DEFAULT 'external',
    added_at        TEXT NOT NULL,
    PRIMARY KEY (group_id, member_id),
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_members_group
    ON group_members(group_id);

CREATE TABLE IF NOT EXISTS sync_sessions (
    session_id       TEXT PRIMARY KEY,
    connection_id    TEXT NOT NULL,
    crawl_type       TEXT NOT NULL DEFAULT 'identity',
    sync_type        TEXT NOT NULL DEFAULT 'full',
    started_at       TEXT NOT NULL,
    completed_at     TEXT,
    status           TEXT NOT NULL DEFAULT 'running',
    groups_created   INTEGER NOT NULL DEFAULT 0,
    groups_updated   INTEGER NOT NULL DEFAULT 0,
    groups_deleted   INTEGER NOT NULL DEFAULT 0,
    groups_unchanged INTEGER NOT NULL DEFAULT 0,
    members_added    INTEGER NOT NULL DEFAULT 0,
    members_removed  INTEGER NOT NULL DEFAULT 0,
    api_calls_made   INTEGER NOT NULL DEFAULT 0,
    errors           INTEGER NOT NULL DEFAULT 0,
    content_total_fetched INTEGER NOT NULL DEFAULT 0,
    content_success  INTEGER NOT NULL DEFAULT 0,
    content_failed   INTEGER NOT NULL DEFAULT 0,
    content_deleted  INTEGER NOT NULL DEFAULT 0,
    content_acl_engine TEXT NOT NULL DEFAULT ''
);
"""


# ── Store class ───────────────────────────────────────────────────────────────

class IdentityStore:
    """
    SQLite-backed state store for external group membership.

    Parameters
    ----------
    db_path       : Path to the SQLite database file.
    connection_id : The Graph external connection ID this store tracks.
    """

    def __init__(self, db_path: str | Path, connection_id: str) -> None:
        self._db_path = Path(db_path)
        self._connection_id = connection_id
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist, and migrate if needed."""
        self._conn.executescript(_SCHEMA_DDL)
        self._migrate()

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "IdentityStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        """Add columns that may be missing from an older schema version."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(sync_sessions)").fetchall()
        }
        migrations = [
            ("crawl_type", "TEXT NOT NULL DEFAULT 'identity'"),
            ("sync_type", "TEXT NOT NULL DEFAULT 'full'"),
            ("content_total_fetched", "INTEGER NOT NULL DEFAULT 0"),
            ("content_success", "INTEGER NOT NULL DEFAULT 0"),
            ("content_failed", "INTEGER NOT NULL DEFAULT 0"),
            ("content_deleted", "INTEGER NOT NULL DEFAULT 0"),
            ("content_acl_engine", "TEXT NOT NULL DEFAULT ''"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                self._conn.execute(f"ALTER TABLE sync_sessions ADD COLUMN {col_name} {col_def}")
                logger.info("[IdentityStore] Migrated: added column sync_sessions.%s", col_name)

    # ── Group CRUD ────────────────────────────────────────────────────────────

    def get_all_group_ids(self) -> set[str]:
        """Return all group IDs tracked for this connection."""
        rows = self._conn.execute(
            "SELECT group_id FROM groups WHERE connection_id = ?",
            (self._connection_id,),
        ).fetchall()
        return {r[0] for r in rows}

    def group_exists(self, group_id: str) -> bool:
        """Check if a group is already tracked."""
        row = self._conn.execute(
            "SELECT 1 FROM groups WHERE group_id = ? AND connection_id = ?",
            (group_id, self._connection_id),
        ).fetchone()
        return row is not None

    def upsert_group(self, group_id: str, display_name: str = "", description: str = "") -> None:
        """Insert or update a group record."""
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO groups (group_id, connection_id, display_name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                display_name = excluded.display_name,
                description  = excluded.description,
                updated_at   = excluded.updated_at
            """,
            (group_id, self._connection_id, display_name, description, now, now),
        )

    def delete_group(self, group_id: str) -> None:
        """Delete a group and all its members (CASCADE)."""
        self._conn.execute("DELETE FROM groups WHERE group_id = ?", (group_id,))

    # ── Member CRUD ───────────────────────────────────────────────────────────

    def get_members(self, group_id: str) -> set[MemberEntry]:
        """Return all current members of a group."""
        rows = self._conn.execute(
            "SELECT member_id, member_type, identity_source FROM group_members WHERE group_id = ?",
            (group_id,),
        ).fetchall()
        return {MemberEntry(member_id=r[0], member_type=r[1], identity_source=r[2]) for r in rows}

    def add_member(self, group_id: str, member: MemberEntry) -> None:
        """Add a single member to a group."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO group_members (group_id, member_id, member_type, identity_source, added_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, member.member_id, member.member_type, member.identity_source, _now_iso()),
        )

    def remove_member(self, group_id: str, member: MemberEntry) -> None:
        """Remove a single member from a group."""
        self._conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND member_id = ?",
            (group_id, member.member_id),
        )

    def replace_members(self, group_id: str, members: set[MemberEntry]) -> None:
        """
        Atomically replace all members of a group.

        Used after a successful full-group publish to ensure the store
        matches the state that was pushed to Graph.
        """
        self._conn.execute("BEGIN")
        try:
            self._conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
            now = _now_iso()
            self._conn.executemany(
                """
                INSERT INTO group_members (group_id, member_id, member_type, identity_source, added_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(group_id, m.member_id, m.member_type, m.identity_source, now) for m in members],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ── Diff computation ──────────────────────────────────────────────────────

    def compute_diff(
        self,
        new_groups: dict[str, tuple[str, set[MemberEntry]]],
    ) -> list[GroupDiff]:
        """
        Compare *new_groups* against the stored state and return a list of diffs.

        Parameters
        ----------
        new_groups : ``{group_id: (display_name, {MemberEntry, ...})}``
            The complete desired state from the current crawl.

        Returns
        -------
        list[GroupDiff] — one entry per group that needs action.

        Change detection logic
        ----------------------
        - Group in new but not in store → ``create`` (PUT group + POST all members)
        - Group in both, members changed → ``update`` (POST adds + DELETE removes)
        - Group in both, members identical → ``unchanged`` (skip)
        - Group in store but not in new → ``delete`` (DELETE group)
        """
        diffs: list[GroupDiff] = []
        existing_ids = self.get_all_group_ids()
        new_ids = set(new_groups.keys())

        # ── Groups to create or update ────────────────────────────────────────
        for group_id, (display_name, desired_members) in new_groups.items():
            if group_id not in existing_ids:
                # Brand-new group
                diffs.append(GroupDiff(
                    group_id=group_id,
                    action="create",
                    display_name=display_name,
                    members_to_add=list(desired_members),
                    members_to_remove=[],
                ))
            else:
                # Existing group — diff members
                current_members = self.get_members(group_id)
                to_add = desired_members - current_members
                to_remove = current_members - desired_members

                if to_add or to_remove:
                    diffs.append(GroupDiff(
                        group_id=group_id,
                        action="update",
                        display_name=display_name,
                        members_to_add=list(to_add),
                        members_to_remove=list(to_remove),
                    ))
                else:
                    diffs.append(GroupDiff(
                        group_id=group_id,
                        action="unchanged",
                        display_name=display_name,
                    ))

        # ── Groups to delete ──────────────────────────────────────────────────
        for stale_id in existing_ids - new_ids:
            diffs.append(GroupDiff(
                group_id=stale_id,
                action="delete",
            ))

        return diffs

    # ── Sync session tracking ─────────────────────────────────────────────────

    def start_session(self, crawl_type: str = "identity", sync_type: str = "full") -> str:
        """Create a new sync session record. Returns the session ID.

        Parameters
        ----------
        crawl_type : What is being crawled.
                     ``"identity"`` — external group creation/update.
                     ``"content"``  — Salesforce record ingestion.
                     ``"identity-dry-run"`` — preview without Graph calls.
        sync_type  : How much data is crawled.
                     ``"full"``        — all records from scratch.
                     ``"incremental"`` — only records modified since last crawl.
        """
        session_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO sync_sessions
                (session_id, connection_id, crawl_type, sync_type, started_at, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (session_id, self._connection_id, crawl_type, sync_type, _now_iso()),
        )
        return session_id

    def complete_session(self, session_id: str, stats: SyncSessionStats, status: str = "completed") -> None:
        """Finalise a sync session with aggregate stats."""
        self._conn.execute(
            """
            UPDATE sync_sessions SET
                completed_at     = ?,
                status           = ?,
                sync_type        = ?,
                groups_created   = ?,
                groups_updated   = ?,
                groups_deleted   = ?,
                groups_unchanged = ?,
                members_added    = ?,
                members_removed  = ?,
                api_calls_made   = ?,
                errors           = ?,
                content_total_fetched = ?,
                content_success  = ?,
                content_failed   = ?,
                content_deleted  = ?,
                content_acl_engine = ?
            WHERE session_id = ?
            """,
            (
                _now_iso(), status, stats.sync_type,
                stats.groups_created, stats.groups_updated, stats.groups_deleted,
                stats.groups_unchanged, stats.members_added, stats.members_removed,
                stats.api_calls_made, stats.errors,
                stats.content_total_fetched, stats.content_success,
                stats.content_failed, stats.content_deleted,
                stats.content_acl_engine,
                session_id,
            ),
        )

    def get_last_session(self, crawl_type: str | None = None) -> dict[str, Any] | None:
        """Return the most recent completed sync session, or None.

        Parameters
        ----------
        crawl_type : Filter by crawl type (e.g. ``"identity"``, ``"content"``).  
                     If None, returns the latest session of any type.
        """
        _SELECT = """
            SELECT session_id, crawl_type, sync_type, started_at, completed_at, status,
                   groups_created, groups_updated, groups_deleted, groups_unchanged,
                   members_added, members_removed, api_calls_made, errors,
                   content_total_fetched, content_success, content_failed,
                   content_deleted, content_acl_engine
            FROM sync_sessions
        """
        if crawl_type:
            row = self._conn.execute(
                _SELECT + " WHERE connection_id = ? AND status = 'completed' AND crawl_type = ?"
                " ORDER BY completed_at DESC LIMIT 1",
                (self._connection_id, crawl_type),
            ).fetchone()
        else:
            row = self._conn.execute(
                _SELECT + " WHERE connection_id = ? AND status = 'completed'"
                " ORDER BY completed_at DESC LIMIT 1",
                (self._connection_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0], "crawl_type": row[1], "sync_type": row[2],
            "started_at": row[3], "completed_at": row[4],
            "status": row[5], "groups_created": row[6], "groups_updated": row[7],
            "groups_deleted": row[8], "groups_unchanged": row[9],
            "members_added": row[10], "members_removed": row[11],
            "api_calls_made": row[12], "errors": row[13],
            "content_total_fetched": row[14], "content_success": row[15],
            "content_failed": row[16], "content_deleted": row[17],
            "content_acl_engine": row[18],
        }

    def get_last_successful_content_crawl_time(self) -> datetime | None:
        """
        Return the ``started_at`` timestamp of the last successful content
        crawl, or None if no content crawl has completed.

        Used by the incremental content crawl to determine the ``since``
        parameter — only Salesforce records modified after this timestamp
        are fetched.

        Returns
        -------
        datetime (UTC) or None.
        """
        row = self._conn.execute(
            """
            SELECT started_at FROM sync_sessions
            WHERE connection_id = ? AND crawl_type = 'content'
                  AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
            """,
            (self._connection_id,),
        ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row[0])
        except (ValueError, TypeError):
            return None

    # ── Utility ───────────────────────────────────────────────────────────────

    @property
    def connection_id(self) -> str:
        return self._connection_id

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_stats(self) -> dict[str, int]:
        """Return current counts of groups and members."""
        group_count = self._conn.execute(
            "SELECT COUNT(*) FROM groups WHERE connection_id = ?",
            (self._connection_id,),
        ).fetchone()[0]
        member_count = self._conn.execute(
            """
            SELECT COUNT(*) FROM group_members gm
            JOIN groups g ON gm.group_id = g.group_id
            WHERE g.connection_id = ?
            """,
            (self._connection_id,),
        ).fetchone()[0]
        return {"groups": group_count, "members": member_count}


# ── Factory ───────────────────────────────────────────────────────────────────

def create_store(connection_id: str, data_dir: str | Path | None = None) -> IdentityStore:
    """
    Create an ``IdentityStore`` for the given connection.

    Parameters
    ----------
    connection_id : The Graph external connection ID.
    data_dir      : Directory to store the DB file.  Defaults to
                    ``{repo_root}/data/``.
    """
    if data_dir is None:
        from salesforce.settings import REPO_ROOT
        data_dir = REPO_ROOT / "data"
    db_path = Path(data_dir) / f"{connection_id}_identity.db"
    return IdentityStore(db_path=db_path, connection_id=connection_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
