# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for silent drop fixes across the ingestion pipeline.

Covers every scenario where items could previously vanish without a trace:
1. Null record ID in converter
2. transform_record exception / empty return
3. ACL resolution failure → dead-letter logging
4. Worker thread crash → stats + dead-letter tracking
5. Sub-batch dispatcher crash → remaining sub-batches survive
6. Pipeline wait crash → remaining chunks survive
7. iter_object_chunks mid-pagination failure → partial buffer yielded
8. append_failed_records I/O failure → fallback logging
9. Count reconciliation — mismatch detection
10. Empty/null Graph batch response → all items marked failed
11. Missing items in Graph batch response → unmatched items marked failed
8. append_failed_records I/O failure → fallback logging
9. Count reconciliation — mismatch detection
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from config.sync_state import append_failed_records
from graph.ingest import (
    IngestionStats,
    _AdaptiveConcurrency,
    _ingest_chunk_graph_batch,
    ingest_content,
)
from item.converter import SalesforceConverter
from salesforce.api_client import SalesforceObjectConfig, _SkipObjectError


# ── Shared helpers ────────────────────────────────────────────────────────────

_ACCOUNT_CFG = SalesforceObjectConfig(object_type="Account", fields=("Id",))


def _make_record(record_id: str, object_type: str = "Account") -> dict:
    return {
        "Id": record_id,
        "objectType": object_type,
        "url": f"https://sf.com/{record_id}",
    }


def _make_stats() -> IngestionStats:
    return IngestionStats()


def _make_transformer_mock(items_per_record=None, side_effect=None):
    """Return a mock transformer. items_per_record overrides the return."""
    mock = MagicMock()
    if side_effect is not None:
        mock.transform_record.side_effect = side_effect
    elif items_per_record is not None:
        mock.transform_record.return_value = items_per_record
    else:
        mock.transform_record.return_value = [
            {"id": "x", "properties": {}, "acl": [], "content": {"value": ""}}
        ]
    mock.handlers = {}
    return mock


# =============================================================================
# 1. Null record ID in converter
# =============================================================================


class TestNullRecordIdConverter:
    """item/converter.py — records without an Id are logged, not silently dropped."""

    def test_record_without_id_returns_none(self):
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        record = {
            "Name": "Ghost Record",
            "IsDeleted": False,
            "objectType": "Account",
            "attributes": {"type": "Account"},
            "OwnerId": "005abc",
            "Owner": {"Name": "User", "UserRole": {"Id": "r1", "ParentRoleId": None}},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedById": "005abc",
            "CreatedBy": {"Name": "Creator"},
            "LastModifiedById": "005abc",
            "LastModifiedBy": {"Name": "Modifier"},
        }
        # No "Id" key at all
        items = converter.convert({"records": [record]}, object_name="Account")
        assert items == []

    def test_record_with_none_id_returns_none(self):
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        record = {
            "Id": None,
            "Name": "Null ID Record",
            "IsDeleted": False,
            "objectType": "Account",
            "attributes": {"type": "Account"},
            "OwnerId": "005abc",
            "Owner": {"Name": "User", "UserRole": {"Id": "r1", "ParentRoleId": None}},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedById": "005abc",
            "CreatedBy": {"Name": "Creator"},
            "LastModifiedById": "005abc",
            "LastModifiedBy": {"Name": "Modifier"},
        }
        items = converter.convert({"records": [record]}, object_name="Account")
        assert items == []

    def test_record_with_empty_id_returns_none(self):
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        record = {
            "Id": "",
            "Name": "Empty ID",
            "IsDeleted": False,
            "objectType": "Account",
            "attributes": {"type": "Account"},
            "OwnerId": "005abc",
            "Owner": {"Name": "User", "UserRole": {"Id": "r1", "ParentRoleId": None}},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedById": "005abc",
            "CreatedBy": {"Name": "Creator"},
            "LastModifiedById": "005abc",
            "LastModifiedBy": {"Name": "Modifier"},
        }
        items = converter.convert({"records": [record]}, object_name="Account")
        assert items == []

    def test_valid_records_not_affected(self):
        converter = SalesforceConverter(instance_url="https://test.my.salesforce.com")
        good_record = {
            "Id": "001valid",
            "Name": "Good Record",
            "IsDeleted": False,
            "objectType": "Account",
            "attributes": {"type": "Account"},
            "OwnerId": "005abc",
            "Owner": {"Name": "User", "UserRole": {"Id": "r1", "ParentRoleId": None}},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedById": "005abc",
            "CreatedBy": {"Name": "Creator"},
            "LastModifiedById": "005abc",
            "LastModifiedBy": {"Name": "Modifier"},
        }
        items = converter.convert({"records": [good_record]}, object_name="Account")
        assert len(items) == 1
        assert items[0]["id"] == "001valid"


# =============================================================================
# 2. transform_record exception / empty return → dead-letter
# =============================================================================


class TestTransformRecordFailure:
    """graph/ingest.py — transform_record failures are caught and written to dead-letter."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_config, tmp_path):
        self.config = replace(test_config, debug_object_type="Account")
        self.dl_path = tmp_path / "dl.jsonl"
        self.stats = _make_stats()
        self.stats_lock = threading.Lock()
        self.client = MagicMock()
        self.concurrency = _AdaptiveConcurrency(1)

    def test_transform_exception_writes_to_dead_letter(self):
        """If transform_record raises, the item is marked failed in stats and dead-letter."""
        transformer = _make_transformer_mock(
            side_effect=ValueError("bad field type")
        )
        records = [_make_record("001")]
        acl_map = {"001": [{"accessType": "grant", "type": "everyone", "value": "e"}]}

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, self.client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )
            mock_append.assert_called_once()
            failures = mock_append.call_args[0][1]
            assert len(failures) == 1
            assert failures[0][0] == "001"
            assert "ValueError" in failures[0][1]

        assert self.stats.failed_count == 1

    def test_transform_empty_return_writes_to_dead_letter(self):
        """If transform_record returns [], the record is marked failed."""
        transformer = _make_transformer_mock(items_per_record=[])
        records = [_make_record("002")]
        acl_map = {"002": []}

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, self.client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )
            mock_append.assert_called_once()
            failures = mock_append.call_args[0][1]
            assert failures[0][0] == "002"
            assert "empty" in failures[0][1].lower()

        assert self.stats.failed_count == 1

    def test_transform_failure_does_not_block_remaining_records(self):
        """One record's transform failure must not prevent others from being ingested."""
        call_count = 0

        def _side_effect(record, acl):
            nonlocal call_count
            call_count += 1
            if record["Id"] == "BAD":
                raise RuntimeError("corrupt record")
            return [{"id": record["Id"], "properties": {}, "acl": [], "content": {"value": ""}}]

        transformer = _make_transformer_mock(side_effect=_side_effect)
        records = [_make_record("OK1"), _make_record("BAD"), _make_record("OK2")]
        acl_map = {r["Id"]: [] for r in records}

        self.client.batch_requests.return_value = [
            {"id": "0", "status": 200},
            {"id": "1", "status": 200},
        ]

        with patch("graph.ingest.append_failed_records"):
            submitted = _ingest_chunk_graph_batch(
                self.config, self.client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        # BAD failed, OK1 and OK2 should have been sent
        assert self.stats.failed_count == 1
        assert self.stats.success_count == 2
        assert call_count == 3  # All three were attempted


# =============================================================================
# 3. ACL resolution failure → dead-letter logging
# =============================================================================


class TestAclFallbackDeadLetter:
    """graph/ingest.py — ACL resolution crash logs affected items to dead-letter."""

    @pytest.fixture(autouse=True)
    def _patch_state(self, tmp_path):
        with (
            patch("graph.ingest.read_checkpoint", return_value=None),
            patch("graph.ingest.write_checkpoint"),
            patch("graph.ingest.clear_checkpoint"),
            patch("graph.ingest.failed_records_path", return_value=tmp_path / "dl.jsonl"),
        ):
            yield

    @patch("graph.ingest.append_failed_records")
    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_acl_crash_writes_all_affected_items_to_dead_letter(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        mock_append, test_config, tmp_path,
    ):
        cfg = replace(test_config, debug_object_type="Account")
        records = [_make_record(f"00{i}") for i in range(5)]
        mock_chunks.return_value = iter([records])
        mock_acl_cls.return_value.resolve.side_effect = RuntimeError("ACL engine crash")

        mock_transformer_cls.return_value = _make_transformer_mock()

        client = MagicMock()
        client.batch_requests.return_value = [{"id": "0", "status": 200}]

        stats = ingest_content(cfg, client)
        assert stats.acl_fallback_used is True

        # Verify append_failed_records was called with all 5 affected item IDs
        acl_fallback_calls = [
            c for c in mock_append.call_args_list
            if c[0][1] and any("ACL" in str(f) for f in c[0][1])
        ]
        assert len(acl_fallback_calls) >= 1
        all_ids = []
        for c in acl_fallback_calls:
            for entry in c[0][1]:
                all_ids.append(entry[0] if isinstance(entry, tuple) else entry)
        for r in records:
            assert r["Id"] in all_ids


# =============================================================================
# 4. Worker thread crash → stats + dead-letter
# =============================================================================


class TestWorkerThreadCrash:
    """graph/ingest.py — worker thread exception updates stats and dead-letter."""

    @pytest.fixture(autouse=True)
    def _patch_state(self, tmp_path):
        self.dl_path = tmp_path / "dl.jsonl"
        with (
            patch("graph.ingest.read_checkpoint", return_value=None),
            patch("graph.ingest.write_checkpoint"),
            patch("graph.ingest.clear_checkpoint"),
            patch("graph.ingest.failed_records_path", return_value=self.dl_path),
        ):
            yield

    @patch("graph.ingest.append_failed_records")
    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_worker_crash_records_failure_in_stats(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        mock_append, test_config,
    ):
        """If _ingest_single_object_type raises, failed_count reflects fetched records."""
        cfg = replace(test_config, debug_object_type="Account")
        records = [_make_record(f"00{i}") for i in range(3)]

        # iter_object_chunks yields one good chunk then crashes —
        # this simulates a mid-stream failure that escapes all chunk-level
        # try/excepts inside _ingest_single_object_type.
        def _crashing_iter(*args, **kwargs):
            yield records
            raise RuntimeError("Unexpected crash in SF fetch after first chunk")

        mock_chunks.side_effect = _crashing_iter

        mock_transformer_cls.return_value = _make_transformer_mock()
        mock_acl_cls.return_value.resolve.return_value = {
            "Account": {r["Id"]: [] for r in records}
        }
        mock_acl_cls.return_value.prewarm_caches.return_value = None

        client = MagicMock()
        client.batch_requests.return_value = [
            {"id": str(i), "status": 200} for i in range(3)
        ]

        stats = ingest_content(cfg, client)

        # The worker crashed — append_failed_records should have been called
        # with a WORKER_CRASH entry
        worker_crash_calls = [
            c for c in mock_append.call_args_list
            if any("WORKER_CRASH" in str(f) for f in c[0][1])
        ]
        assert len(worker_crash_calls) >= 1


# =============================================================================
# 5. Sub-batch dispatcher crash → remaining sub-batches survive
# =============================================================================


class TestSubBatchDispatcherCrash:
    """graph/ingest.py — one sub-batch crash doesn't kill remaining sub-batches."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_config, tmp_path):
        self.config = replace(test_config, debug_object_type="Account")
        self.dl_path = tmp_path / "dl.jsonl"
        self.stats = _make_stats()
        self.stats_lock = threading.Lock()
        self.concurrency = _AdaptiveConcurrency(1)

    def test_batch_post_exception_continues_remaining_batches(self):
        """If the Graph $batch POST throws for one sub-batch, the next sub-batch still runs."""
        transformer = MagicMock()
        # 25 records → 2 sub-batches of 20 + 5
        records = [_make_record(f"R{i:03d}") for i in range(25)]
        acl_map = {r["Id"]: [] for r in records}
        items = [
            {"id": r["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
            for r in records
        ]
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]

        client = MagicMock()
        call_count = 0

        def _batch_side_effect(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network timeout on first sub-batch")
            return [{"id": str(i), "status": 200} for i in range(len(payload))]

        client.batch_requests.side_effect = _batch_side_effect

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        # First sub-batch (20 items) failed, second (5 items) should have succeeded
        assert self.stats.failed_count == 20
        assert self.stats.success_count == 5
        assert call_count == 2  # Both sub-batches were attempted


# =============================================================================
# 6. Pipeline wait crash → remaining chunks survive
# =============================================================================


class TestPipelineWaitCrash:
    """graph/ingest.py — Graph upload failure for chunk N doesn't kill chunk N+1."""

    @pytest.fixture(autouse=True)
    def _patch_state(self, tmp_path):
        with (
            patch("graph.ingest.read_checkpoint", return_value=None),
            patch("graph.ingest.write_checkpoint"),
            patch("graph.ingest.clear_checkpoint"),
            patch("graph.ingest.failed_records_path", return_value=tmp_path / "dl.jsonl"),
            patch("graph.ingest.append_failed_records"),
        ):
            yield

    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_chunk_failure_does_not_abort_remaining_chunks(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        test_config,
    ):
        cfg = replace(test_config, debug_object_type="Account")
        chunk1 = [_make_record("C1_001"), _make_record("C1_002")]
        chunk2 = [_make_record("C2_001"), _make_record("C2_002")]
        mock_chunks.return_value = iter([chunk1, chunk2])

        mock_acl_cls.return_value.resolve.return_value = {
            "Account": {r["Id"]: [] for r in chunk1 + chunk2}
        }

        mock_t = _make_transformer_mock()
        # transform_record returns items with correct IDs
        mock_t.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        mock_transformer_cls.return_value = mock_t

        client = MagicMock()
        batch_call_count = 0

        def _batch_side_effect(payload):
            nonlocal batch_call_count
            batch_call_count += 1
            if batch_call_count == 1:
                # First chunk's Graph push fails
                raise ConnectionError("Graph API unreachable")
            return [{"id": str(i), "status": 200} for i in range(len(payload))]

        client.batch_requests.side_effect = _batch_side_effect

        stats = ingest_content(cfg, client)

        # Both chunks were fetched
        assert stats.total_fetched == 4
        # Second chunk should have succeeded despite first chunk's failure
        assert batch_call_count >= 2
        assert stats.success_count >= 2


# =============================================================================
# 7. iter_object_chunks mid-pagination failure → partial buffer yielded
# =============================================================================


class TestIterObjectChunksMidPaginationFailure:
    """salesforce/api_client.py — partial buffer is yielded when fetch crashes mid-stream."""

    def test_partial_buffer_yielded_on_fetch_crash(self):
        """If fetch_salesforce_records crashes after 3 records, those 3 are still yielded."""
        from salesforce.api_client import iter_object_chunks

        cfg = MagicMock()
        cfg.connector.salesforce.instance_url = "https://test.my.salesforce.com"
        cfg.debug_item_id = None
        cfg.tuning.salesforce_query_limit = 0

        obj_config = SalesforceObjectConfig(object_type="Account", fields=("Id",))

        def _failing_generator(*args, **kwargs):
            for i in range(3):
                yield {"Id": f"00{i}", "objectType": "Account"}
            raise ConnectionError("Salesforce connection reset")

        with patch("salesforce.api_client.fetch_salesforce_records", side_effect=_failing_generator):
            with patch("salesforce.api_client.get_salesforce_access_token", return_value="token"):
                chunks = list(iter_object_chunks(cfg, obj_config, None, chunk_size=100))

        # All 3 records should be in one partial chunk
        assert len(chunks) == 1
        assert len(chunks[0]) == 3

    def test_full_chunks_before_crash_are_preserved(self):
        """Records yielded before the crash in full chunks are preserved."""
        from salesforce.api_client import iter_object_chunks

        cfg = MagicMock()
        cfg.connector.salesforce.instance_url = "https://test.my.salesforce.com"
        cfg.debug_item_id = None
        cfg.tuning.salesforce_query_limit = 0

        obj_config = SalesforceObjectConfig(object_type="Account", fields=("Id",))

        def _failing_generator(*args, **kwargs):
            # 5 records, then crash — with chunk_size=3, we get chunk of 3 + partial of 2
            for i in range(5):
                yield {"Id": f"00{i}", "objectType": "Account"}
            raise RuntimeError("SOQL timeout")

        with patch("salesforce.api_client.fetch_salesforce_records", side_effect=_failing_generator):
            with patch("salesforce.api_client.get_salesforce_access_token", return_value="token"):
                chunks = list(iter_object_chunks(cfg, obj_config, None, chunk_size=3))

        assert len(chunks) == 2
        assert len(chunks[0]) == 3  # Full chunk
        assert len(chunks[1]) == 2  # Partial buffer after crash

    def test_skip_object_error_still_propagates(self):
        """_SkipObjectError must propagate (it's not a data loss — the object doesn't exist)."""
        from salesforce.api_client import iter_object_chunks

        cfg = MagicMock()
        cfg.connector.salesforce.instance_url = "https://test.my.salesforce.com"
        cfg.debug_item_id = None
        cfg.tuning.salesforce_query_limit = 0

        obj_config = SalesforceObjectConfig(object_type="FakeObj", fields=("Id",))

        def _skip_generator(*args, **kwargs):
            raise _SkipObjectError("FakeObj not available")
            yield  # make it a generator

        with patch("salesforce.api_client.fetch_salesforce_records", side_effect=_skip_generator):
            with patch("salesforce.api_client.get_salesforce_access_token", return_value="token"):
                with pytest.raises(_SkipObjectError):
                    list(iter_object_chunks(cfg, obj_config, None, chunk_size=100))

    def test_no_crash_yields_all_records(self):
        """Normal operation is not affected by the try/except."""
        from salesforce.api_client import iter_object_chunks

        cfg = MagicMock()
        cfg.connector.salesforce.instance_url = "https://test.my.salesforce.com"
        cfg.debug_item_id = None
        cfg.tuning.salesforce_query_limit = 0

        obj_config = SalesforceObjectConfig(object_type="Account", fields=("Id",))

        def _ok_generator(*args, **kwargs):
            for i in range(7):
                yield {"Id": f"00{i}", "objectType": "Account"}

        with patch("salesforce.api_client.fetch_salesforce_records", side_effect=_ok_generator):
            with patch("salesforce.api_client.get_salesforce_access_token", return_value="token"):
                chunks = list(iter_object_chunks(cfg, obj_config, None, chunk_size=3))

        assert len(chunks) == 3  # 3 + 3 + 1
        assert len(chunks[0]) == 3
        assert len(chunks[1]) == 3
        assert len(chunks[2]) == 1


# =============================================================================
# 8. append_failed_records I/O failure → fallback to logger
# =============================================================================


class TestAppendFailedRecordsIOError:
    """config/sync_state.py — I/O errors don't lose failure information."""

    def test_io_error_does_not_raise(self, tmp_path):
        """If the dead-letter file can't be written, no exception propagates."""
        bad_path = tmp_path / "nonexistent_subdir" / "nested" / "dl.jsonl"
        # Make the parent path a file so mkdir fails
        bad_path.parent.parent.mkdir(parents=True)
        bad_path.parent.parent.joinpath("nested").write_text("block")
        # This should be a file, not a directory, so writing under it fails
        bad_file_path = bad_path.parent.parent / "nested" / "dl.jsonl"

        # Should not raise — error is logged instead
        append_failed_records(
            bad_file_path,
            [("item1", "some error"), ("item2", "another error")],
            "Account",
        )

    def test_successful_write_creates_entries(self, tmp_path):
        """Normal operation: entries are written correctly."""
        dl_path = tmp_path / "dl.jsonl"
        append_failed_records(
            dl_path,
            [("item1", "error1"), ("item2", "error2")],
            "Account",
        )
        lines = dl_path.read_text().strip().split("\n")
        assert len(lines) == 2
        entry1 = json.loads(lines[0])
        assert entry1["item_id"] == "item1"
        assert entry1["object_type"] == "Account"
        assert entry1["error"] == "error1"

    def test_tuple_and_string_formats_both_work(self, tmp_path):
        """Both (id, error) tuples and plain id strings are handled."""
        dl_path = tmp_path / "dl.jsonl"
        append_failed_records(
            dl_path,
            ["id1", "id2"],
            "Contact",
            error="shared error",
        )
        lines = dl_path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert entry["error"] == "shared error"

    def test_empty_failures_writes_nothing(self, tmp_path):
        """Empty list produces no file."""
        dl_path = tmp_path / "dl.jsonl"
        append_failed_records(dl_path, [], "Account")
        assert not dl_path.exists()

    def test_request_and_response_bodies_included(self, tmp_path):
        """Request/response bodies are included in the JSONL entry."""
        dl_path = tmp_path / "dl.jsonl"
        append_failed_records(
            dl_path,
            [("item1", "error1")],
            "Account",
            request_bodies={"item1": {"properties": {"Name": "Test"}}},
            response_bodies={"item1": {"status": 400, "body": {"error": {"message": "bad"}}}},
        )
        entry = json.loads(dl_path.read_text().strip())
        assert "request_body" in entry
        assert "response_body" in entry
        assert entry["request_body"]["properties"]["Name"] == "Test"


# =============================================================================
# 9. Count reconciliation — mismatch detection
# =============================================================================


class TestCountReconciliation:
    """graph/ingest.py — final count reconciliation detects silent drops."""

    @pytest.fixture(autouse=True)
    def _patch_state(self, tmp_path):
        with (
            patch("graph.ingest.read_checkpoint", return_value=None),
            patch("graph.ingest.write_checkpoint"),
            patch("graph.ingest.clear_checkpoint"),
            patch("graph.ingest.failed_records_path", return_value=tmp_path / "dl.jsonl"),
            patch("graph.ingest.append_failed_records"),
        ):
            yield

    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_counts_match_on_full_success(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        test_config,
    ):
        cfg = replace(test_config, debug_object_type="Account")
        records = [_make_record(f"00{i}") for i in range(5)]
        mock_chunks.return_value = iter([records])
        mock_acl_cls.return_value.resolve.return_value = {
            "Account": {r["Id"]: [] for r in records}
        }
        mock_t = _make_transformer_mock()
        mock_t.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        mock_transformer_cls.return_value = mock_t

        client = MagicMock()
        client.batch_requests.return_value = [
            {"id": str(i), "status": 200} for i in range(5)
        ]

        stats = ingest_content(cfg, client)
        accounted = stats.success_count + stats.failed_count + stats.deleted_count + stats.skipped_count
        assert accounted == stats.total_fetched
        assert stats.total_fetched == 5

    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_counts_match_with_mixed_results(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        test_config,
    ):
        """Mix of successes and failures should still reconcile."""
        cfg = replace(test_config, debug_object_type="Account")
        records = [_make_record(f"00{i}") for i in range(4)]
        mock_chunks.return_value = iter([records])
        mock_acl_cls.return_value.resolve.return_value = {
            "Account": {r["Id"]: [] for r in records}
        }
        mock_t = _make_transformer_mock()
        mock_t.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        mock_transformer_cls.return_value = mock_t

        client = MagicMock()
        # 2 succeed, 2 fail
        client.batch_requests.return_value = [
            {"id": "0", "status": 200},
            {"id": "1", "status": 200},
            {"id": "2", "status": 400, "body": "bad"},
            {"id": "3", "status": 400, "body": "bad"},
        ]

        stats = ingest_content(cfg, client)
        accounted = stats.success_count + stats.failed_count + stats.deleted_count + stats.skipped_count
        assert accounted == stats.total_fetched
        assert stats.success_count == 2
        assert stats.failed_count == 2

    @patch("graph.ingest.iter_object_chunks")
    @patch("graph.ingest.get_object_config", return_value=_ACCOUNT_CFG)
    @patch("graph.ingest.LegacyAclResolver")
    @patch("graph.ingest.SalesforceItemTransformer")
    def test_transform_failures_included_in_reconciliation(
        self, mock_transformer_cls, mock_acl_cls, mock_get_cfg, mock_chunks,
        test_config,
    ):
        """Records that fail during transform are counted in failed_count."""
        cfg = replace(test_config, debug_object_type="Account")
        records = [_make_record("OK1"), _make_record("BAD"), _make_record("OK2")]
        mock_chunks.return_value = iter([records])
        mock_acl_cls.return_value.resolve.return_value = {
            "Account": {r["Id"]: [] for r in records}
        }

        def _transform(rec, acl):
            if rec["Id"] == "BAD":
                raise ValueError("corrupt")
            return [{"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}]

        mock_t = MagicMock()
        mock_t.transform_record.side_effect = _transform
        mock_t.handlers = {}
        mock_transformer_cls.return_value = mock_t

        client = MagicMock()
        client.batch_requests.return_value = [
            {"id": "0", "status": 200},
            {"id": "1", "status": 200},
        ]

        stats = ingest_content(cfg, client)
        accounted = stats.success_count + stats.failed_count + stats.deleted_count + stats.skipped_count
        assert accounted == stats.total_fetched
        assert stats.failed_count == 1
        assert stats.success_count == 2


# =============================================================================
# 10. Empty/null Graph batch response → all items marked failed
# =============================================================================


class TestEmptyBatchResponse:
    """graph/ingest.py — empty or null $batch response marks all items as failed."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_config, tmp_path):
        self.config = replace(test_config, debug_object_type="Account")
        self.dl_path = tmp_path / "dl.jsonl"
        self.stats = _make_stats()
        self.stats_lock = threading.Lock()
        self.concurrency = _AdaptiveConcurrency(1)

    def test_empty_response_marks_all_items_failed(self):
        """If batch_requests returns [], every item must be counted as failed."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"E{i:03d}") for i in range(5)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        client.batch_requests.return_value = []  # Empty response

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )
            mock_append.assert_called()
            # All 5 item IDs should be in the failures
            all_failures = []
            for c in mock_append.call_args_list:
                all_failures.extend(c[0][1])
            failed_ids = [f[0] if isinstance(f, tuple) else f for f in all_failures]
            for r in records:
                assert r["Id"] in failed_ids

        assert self.stats.failed_count == 5

    def test_none_response_marks_all_items_failed(self):
        """If batch_requests returns None, every item must be counted as failed."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"N{i:03d}") for i in range(3)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        client.batch_requests.return_value = None  # Null response

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        assert self.stats.failed_count == 3


# =============================================================================
# 11. Missing items in Graph batch response → unmatched items marked failed
# =============================================================================


class TestMissingItemsInBatchResponse:
    """graph/ingest.py — items with no matching response entry are marked failed."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_config, tmp_path):
        self.config = replace(test_config, debug_object_type="Account")
        self.dl_path = tmp_path / "dl.jsonl"
        self.stats = _make_stats()
        self.stats_lock = threading.Lock()
        self.concurrency = _AdaptiveConcurrency(1)

    def test_partial_response_detects_missing_items(self):
        """If Graph returns responses for 3 of 5 items, the other 2 are marked failed."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"P{i:03d}") for i in range(5)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        # Only return responses for items 0, 1, 2 — items 3 and 4 are missing
        client.batch_requests.return_value = [
            {"id": "0", "status": 200},
            {"id": "1", "status": 200},
            {"id": "2", "status": 200},
        ]

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        assert self.stats.success_count == 3
        assert self.stats.failed_count == 2
        # The 2 missing items should be in failed_ids
        assert len(self.stats.failed_ids) == 2

    def test_mismatched_response_ids_detected(self):
        """If response IDs don't match request IDs, unmatched items are marked failed."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"M{i:03d}") for i in range(3)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        # Return responses with wrong IDs — none match
        client.batch_requests.return_value = [
            {"id": "99", "status": 200},
            {"id": "98", "status": 200},
            {"id": "97", "status": 200},
        ]

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        # All 3 items should be failed — none were matched
        assert self.stats.success_count == 0
        assert self.stats.failed_count == 3

    def test_response_with_no_id_field_does_not_cause_silent_drop(self):
        """Response entries with missing 'id' field must not cause items to vanish."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"X{i:03d}") for i in range(2)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        # One response has no "id", the other has a valid "id"
        client.batch_requests.return_value = [
            {"status": 200},           # Missing "id" field
            {"id": "1", "status": 200},  # Valid
        ]

        with patch("graph.ingest.append_failed_records"):
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        # Item "0" got no matching response → failed; item "1" matched → success
        assert self.stats.success_count == 1
        assert self.stats.failed_count == 1

    def test_all_items_matched_means_zero_missing(self):
        """When all items have matching responses, no items are marked as missing."""
        transformer = _make_transformer_mock()
        transformer.transform_record.side_effect = lambda rec, acl: [
            {"id": rec["Id"], "properties": {}, "acl": [], "content": {"value": ""}}
        ]
        records = [_make_record(f"OK{i:02d}") for i in range(4)]
        acl_map = {r["Id"]: [] for r in records}

        client = MagicMock()
        client.batch_requests.return_value = [
            {"id": str(i), "status": 200} for i in range(4)
        ]

        with patch("graph.ingest.append_failed_records") as mock_append:
            _ingest_chunk_graph_batch(
                self.config, client, transformer, records, acl_map,
                self.stats, 20, dl_path=self.dl_path, object_type="Account",
                concurrency=self.concurrency, stats_lock=self.stats_lock,
            )

        assert self.stats.success_count == 4
        assert self.stats.failed_count == 0
        # No dead-letter entries for missing items
        for c in mock_append.call_args_list:
            failures = c[0][1]
            for f in failures:
                error = f[1] if isinstance(f, tuple) else ""
                assert "No response" not in error
