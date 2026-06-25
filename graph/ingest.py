# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Item ingestion pipeline — Salesforce → Graph external items.

Orchestrates the full ingestion flow:

1. **Fetch** — queries all Salesforce objects defined in ``config/schema.json``
   via the Salesforce REST API (supports full and incremental sync).
2. **ACL resolution** — for each record, resolves the Salesforce sharing model
   (OWD, roles, groups, territories, sharing rules, parent chains) into
   Microsoft Graph ACL entries.  Two engines are available:
   * *Legacy* (``Graph.acl.AclResolver``) — default.
   * *New* (``acl_engine``) — enabled by setting ``USE_NEW_ACL_ENGINE=true``.
3. **Transform** — converts each Salesforce record + ACLs into a Graph
   ``externalItem`` payload using ``SalesforceItemTransformer``.
4. **Upsert / delete** — PUTs each item into the external connection (or
   DELETEs it if the transformer marks it as ``deleted``).

Debug modes
-----------
``DEBUG_ITEM_ID``
    Set via the ``single-item`` command.  Restricts ingestion to a single
    Salesforce record ID.
``DEBUG_OBJECT_TYPE``
    Set via the ``single-object`` command.  Restricts ingestion to one
    Salesforce object type (e.g. ``Case``, ``Account``).

Key functions
-------------
ingest_content(config, client, since)
    Main entry point.  Called by every command that performs ingestion.
load_content(config, client, item)
    PUTs a single transformed item into the Graph external connection.
delete_content(config, client, item_id)
    DELETEs a single item from the Graph external connection.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator
from urllib.parse import quote
import json
import logging
import threading
import time

from graph.legacy_acl_resolver import AclResolver as LegacyAclResolver
from graph.client import GraphApiError, GraphClient, EXTERNAL_CONNECTIONS_PATH, GRAPH_BATCH_MAX_SIZE
from salesforce.api_client import get_all_items_from_api, iter_object_chunks, get_object_config, OBJECT_CONFIGS, _SkipObjectError
from salesforce.settings import AppConfig
from salesforce.item_transformer import SalesforceItemTransformer
from config.sync_state import (
    append_failed_records,
    clear_checkpoint,
    failed_records_path,
    read_checkpoint,
    write_checkpoint,
)


@dataclass
class IngestionStats:
    """Tracks ingestion outcomes for summary reporting."""
    total_fetched: int = 0
    success_count: int = 0
    failed_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    failed_ids: list[str] = field(default_factory=list)
    object_type_counts: dict[str, int] = field(default_factory=dict)
    acl_engine: str = "LEGACY"
    acl_fallback_used: bool = False
    # {obj_type: {phase: (total_secs, chunk_count)}}
    phase_timings: dict[str, dict[str, tuple[float, int]]] = field(default_factory=dict)
    _timing_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_phase_time(self, obj_type: str, phase: str, duration: float) -> None:
        """Accumulate timing for a pipeline phase (thread-safe)."""
        with self._timing_lock:
            if obj_type not in self.phase_timings:
                self.phase_timings[obj_type] = {}
            prev = self.phase_timings[obj_type].get(phase, (0.0, 0))
            self.phase_timings[obj_type][phase] = (prev[0] + duration, prev[1] + 1)

logger = logging.getLogger("salesforce_connector")

# Track sample items for detailed logging
_sample_items_logged_by_type = set()
_FAILED_IDS_DISPLAY_LIMIT = 100


class _AdaptiveConcurrency:
    """Tracks concurrency level: dials down on 429, dials up on sustained success."""

    def __init__(self, max_workers: int) -> None:
        self._max = max(1, max_workers)
        self._current = self._max
        self._success_streak = 0
        self._lock = threading.Lock()

    @property
    def current(self) -> int:
        return self._current

    def on_success(self) -> None:
        with self._lock:
            self._success_streak += 1
            # Ramp up after 3 consecutive successes (not 10) to recover quickly
            if self._success_streak >= 3 and self._current < self._max:
                self._current += 1
                self._success_streak = 0
                logger.info("Graph concurrency ramped up to %d", self._current)

    def on_throttle(self) -> None:
        with self._lock:
            prev = self._current
            self._current = max(1, self._current - 1)
            self._success_streak = 0
            if self._current != prev:
                logger.warning("Graph 429 throttling — concurrency reduced to %d", self._current)


def load_content(config: AppConfig, client: GraphClient, item: dict) -> None:
    """PUT a single transformed item into the Graph external connection."""
    item_id = item["id"]
    payload = {key: value for key, value in item.items() if key != "id"}
    url = f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items/{quote(item_id, safe='')}"

    # Log sample item request/response for first item of each object type
    object_type = item.get("properties", {}).get("ObjectName")
    if object_type and object_type not in _sample_items_logged_by_type:
        _sample_items_logged_by_type.add(object_type)
        logger.info("SAMPLE ITEM REQUEST: %s (ID: %s)", object_type, item_id)
        logger.info("PUT %s", url)
        logger.info("\nRequest Payload:")
        logger.info(json.dumps(payload, indent=2))
    elif logger.isEnabledFor(logging.DEBUG):
        logger.debug("ITEM REQUEST: %s | PUT %s", item_id, url)

    logger.debug("PUT %s", url)

    try:
        response = client.put(url, json_body=payload, headers={"content-type": "application/json"})
        
        # Log response for sample items
        if object_type and object_type in _sample_items_logged_by_type and len(_sample_items_logged_by_type) <= 6:
            logger.info("\nResponse:")
            logger.info(json.dumps(response if response else {"status": "success"}, indent=2))
            logger.info("=" * 80 + "\n")
            
    except GraphApiError as error:
        logger.error("Failed to load %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)
        raise


def delete_content(config: AppConfig, client: GraphClient, item_id: str) -> None:
    """DELETE a single item from the Graph external connection."""
    url = f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}/items/{quote(item_id, safe='')}"
    logger.info("DELETE %s", url)

    try:
        client.delete(url)
    except GraphApiError as error:
        logger.error("Failed to delete %s: %s", item_id, error)
        if error.body:
            logger.error("Graph response: %s", error.body)


# ── New ACL engine helper ─────────────────────────────────────────────────────

async def _resolve_acl_new_engine(
    config: AppConfig,
    graph_client: GraphClient,
    records_by_object_type: dict[str, list[dict]],
    *,
    resolver=None,
    mapper=None,
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """
    Resolve ACLs for all records using the new acl_engine pipeline.

    Flow
    ----
    1. For each record, call AclResolver.resolve_async(object_type, record_id)
       concurrently (asyncio.gather per object type).
    2. Collect the union of all Salesforce User IDs across every record.
    3. PrincipalMapper bulk-fetches user identity fields (FederationIdentifier /
       UserName / Email) in a single SOQL per 100 IDs.
    4. For each record's AclResult, call PrincipalMapper.to_acl_entries() to
       produce the final Graph-API-ready ACL list.

    When *resolver* and *mapper* are provided they are reused across calls so
    that the PrincipalMapper's identity cache persists between chunks.

    Returns
    -------
    {object_type: {record_id: [acl_entry, ...]}}  – same shape as the legacy
    AclResolver so the rest of ingest_content is unchanged.
    """
    if resolver is None or mapper is None:
        from acl_engine import AclResolver as NewAclResolver, PrincipalMapper
        from acl_engine.salesforce_client import SalesforceClient
        from salesforce.api_client import get_salesforce_access_token

        sf_client = SalesforceClient(
            instance_url=config.connector.salesforce.instance_url,
            api_version=config.connector.salesforce.api_version,
            access_token=get_salesforce_access_token(config),
        )
        resolver = NewAclResolver(
            sf_client,
            owd_field_map=config.owd_field_map,
            parent_map=config.parent_map,
            owd_overrides=config.owd_overrides,
            max_parent_depth=config.tuning.acl_max_parent_depth,
            use_entity_definition_owd=config.use_entity_definition_owd,
            object_names=config.object_names,
        )
        mapper = PrincipalMapper(
            sf_client=sf_client,
            graph_client=graph_client,
            tenant_id=config.tenant_id,
            batch_size=config.tuning.salesforce_batch_size,
        )

    acl_map_by_object: dict[str, dict[str, list[dict[str, str]]]] = {}

    for object_type, records in records_by_object_type.items():
        logger.info("[NewACL] Resolving %d %s record(s)", len(records), object_type)

        # Bulk pre-warm: fetch all owner IDs, share entries, groups, roles for
        # this chunk in ~5 SOQL calls total instead of O(N) per-record calls.
        record_ids = [str(r["Id"]) for r in records if r.get("Id")]
        await resolver.prewarm_chunk(object_type, record_ids)

        # Pre-warm PrincipalMapper user-identity details for all owners that
        # will likely appear in this chunk (eliminates per-record SOQL in mapper).
        # Use the owner IDs already in the share_fetcher cache as a first approximation.
        owner_ids = {v for v in resolver._share_fetcher._owner_cache.values() if v}  # noqa: SLF001
        if owner_ids:
            await mapper.prewarm_users(owner_ids)

        # Configurable concurrency — after pre-warm most work is in-memory so
        # raising this to 500 is safe and gives a large throughput boost.
        import os as _os
        _ACL_CONCURRENCY = int(_os.environ.get("ACL_RESOLVE_CONCURRENCY", "500"))
        semaphore = asyncio.Semaphore(_ACL_CONCURRENCY)

        async def _resolve_with_sem(ot: str, rid: str):
            async with semaphore:
                return await resolver.resolve_async(ot, rid)

        # Resolve all records for this object type concurrently
        tasks = [
            _resolve_with_sem(object_type, str(record["Id"]))
            for record in records
            if record.get("Id")
        ]
        acl_results = await asyncio.gather(*tasks, return_exceptions=True)

        object_acl: dict[str, list[dict[str, str]]] = {}
        valid_pairs = []
        for record, acl_result in zip(records, acl_results):
            record_id = str(record["Id"])
            if isinstance(acl_result, Exception):
                logger.error(
                    "[NewACL] resolve_async failed for %s/%s: %s – using deny-everyone ACL",
                    object_type, record_id, acl_result,
                )
                object_acl[record_id] = _deny_all_acl_entry()
            else:
                valid_pairs.append((record_id, acl_result))

        # Run to_acl_entries() concurrently — it was previously sequential
        # which serialised all Graph/SOQL lookups across 2000 records.
        if valid_pairs:
            acl_entry_results = await asyncio.gather(
                *[mapper.to_acl_entries(acl_result) for _, acl_result in valid_pairs],
                return_exceptions=True,
            )
            for (record_id, _), acl_entries in zip(valid_pairs, acl_entry_results):
                if isinstance(acl_entries, Exception):
                    logger.error("[NewACL] to_acl_entries failed for %s/%s: %s — using deny-everyone ACL", object_type, record_id, acl_entries)
                    object_acl[record_id] = _deny_all_acl_entry()
                else:
                    object_acl[record_id] = acl_entries

        # Free the raw ACL result objects — only the mapped entries are needed downstream
        acl_results.clear()

        acl_map_by_object[object_type] = object_acl
        logger.info("[NewACL] %s: resolved %d ACL(s)", object_type, len(object_acl))

    return acl_map_by_object


def _deny_all_acl_entry() -> list[dict[str, str]]:
    """Return a deny-everyone ACL when ACL resolution fails.

    Items ingested with this ACL will not appear in any user's search results.
    This is the safe default — it prevents accidental data exposure when ACL
    resolution fails.
    """
    return [{"accessType": "deny", "type": "everyone", "value": "everyone"}]

###Dead code. Can be removed
def _iter_record_chunks(
    config: AppConfig,
    since: datetime | None,
    chunk_size: int,
) -> Iterator[tuple[str, list[dict]]]:
    """
    Yield ``(object_type, chunk)`` tuples where *chunk* is at most *chunk_size*
    records of the same object type.

    Records stream directly from the Salesforce API page-by-page.  Only a
    single chunk is buffered at a time — peak RAM is therefore
    ``chunk_size × avg_record_size`` rather than
    ``total_records × avg_record_size``.

    Object-type filtering (``DEBUG_OBJECT_TYPE``) is handled upstream in
    ``get_all_items_from_api`` — only the matching thread is started so no
    wasted Salesforce API calls occur here.

    ``DEBUG_ITEM_ID`` filtering is applied here because all object threads must
    run (we don't know which object owns the ID) and we need to drop every
    non-matching record before it enters the ingest pipeline.
    """
    DEBUG_ITEM_ID = config.debug_item_id

    current_type: str | None = None
    buffer: list[dict] = []

    for record in get_all_items_from_api(config, since):
        object_type = record.get("objectType", "")

        # When the object type changes, flush whatever is in the buffer
        if object_type != current_type:
            if buffer and current_type:
                yield current_type, buffer
            current_type = object_type
            buffer = []

        # DEBUG: skip individual records that don't match the requested ID
        if DEBUG_ITEM_ID and record.get("Id") != DEBUG_ITEM_ID:
            continue

        buffer.append(record)

        # Flush whenever the chunk is full — this is the key memory-saving step
        if len(buffer) >= chunk_size:
            yield current_type, buffer  # type: ignore[arg-type]
            buffer = []

    # Flush the final partial chunk
    if buffer and current_type:
        yield current_type, buffer


def _ingest_chunk_graph_batch(
    config: AppConfig,
    client: GraphClient,
    transformer: "SalesforceItemTransformer",
    records: list[dict],
    acl_map: dict[str, list[dict[str, str]]],
    stats: "IngestionStats",
    batch_size: int,
    *,
    dl_path=None,
    object_type: str = "",
    dashboard=None,
    concurrency: "_AdaptiveConcurrency | None" = None,
    stats_lock: threading.Lock | None = None,
) -> int:
    """
    Transform *records* and push them to Graph using JSON batching.

    Sub-batches (max 20 items each) are sent **concurrently** using a
    ``ThreadPoolExecutor``.  The concurrency level is controlled by
    *concurrency* (``_AdaptiveConcurrency``): on 429 throttling it dials
    down; on sustained success it dials back up.

    Returns the total number of items submitted (success + failure).
    """
    # 1. Transform all records into Graph external-item payloads
    _transform_t0 = time.monotonic()
    items_to_process: list[dict] = []
    transform_failures: list[tuple[str, str]] = []
    for record in records:
        item_id = record.get("Id", "unknown")
        acl = acl_map.get(item_id)
        if acl is None:
            logger.warning(
                "No ACL found for item %s (%s) — using fallback ACL",
                item_id, record.get("objectType"),
            )
        try:
            transformed_items = transformer.transform_record(record, acl)
            if not transformed_items:
                logger.warning(
                    "[%s] transform_record returned empty for item %s — record dropped",
                    object_type, item_id,
                )
                transform_failures.append((item_id, f"[Transform] Returned empty result for {object_type}"))
                continue
            for transformed in transformed_items:
                items_to_process.append(transformed)
        except Exception as exc:
            logger.error(
                "[%s] transform_record failed for item %s: %s",
                object_type, item_id, exc,
            )
            transform_failures.append((item_id, f"[Transform] {type(exc).__name__}: {exc}"))

    if transform_failures:
        with stats_lock:
            stats.failed_count += len(transform_failures)
            for fid, _ in transform_failures:
                if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                    stats.failed_ids.append(fid)
        if dl_path:
            append_failed_records(dl_path, transform_failures, object_type)
        logger.warning(
            "[%s] %d record(s) failed during transform and were written to dead-letter file",
            object_type, len(transform_failures),
        )

    _transform_dur = time.monotonic() - _transform_t0

    if not items_to_process:
        return 0

    logger.info(
        "[TIMING] Transform %s: %.1fs for %d records → %d items (%.0f rec/s)",
        object_type, _transform_dur, len(records), len(items_to_process),
        len(records) / _transform_dur if _transform_dur > 0 else 0,
    )
    if dashboard:
        dashboard.record_phase_time(object_type, "Transform", _transform_dur)
    stats.record_phase_time(object_type, "Transform", _transform_dur)

    effective_batch = min(batch_size, GRAPH_BATCH_MAX_SIZE)
    max_workers = concurrency.current if concurrency else 1

    # 2. Build all sub-batch payloads up front
    sub_batches: list[tuple[list[dict], list[dict]]] = []  # (items, requests_payload)
    for start in range(0, len(items_to_process), effective_batch):
        batch_items = items_to_process[start : start + effective_batch]
        payload: list[dict] = []
        for idx, item in enumerate(batch_items):
            item_url = (
                f"{EXTERNAL_CONNECTIONS_PATH}/{config.connector.id}"
                f"/items/{quote(item['id'], safe='')}"
            )
            if item.get("type") == "deleted":
                payload.append({"id": str(idx), "method": "DELETE", "url": item_url})
            else:
                body = {k: v for k, v in item.items() if k != "id"}
                payload.append({
                    "id": str(idx), "method": "PUT", "url": item_url,
                    "headers": {"Content-Type": "application/json"}, "body": body,
                })
                if config.debug_item_id:
                    logger.info(
                        "ITEM REQUEST — PUT %s\nRequest Body:\n%s",
                        item_url, json.dumps(body, indent=2),
                    )
                elif logger.isEnabledFor(logging.DEBUG):
                    logger.debug("ITEM REQUEST — PUT %s", item_url)
        sub_batches.append((batch_items, payload))

    # 3. Send sub-batches concurrently with adaptive throttling
    submitted = 0
    if stats_lock is None:
        stats_lock = threading.Lock()

    _MAX_ITEM_RETRIES = config.tuning.graph_max_retries
    _BACKOFF_BASE = config.tuning.graph_retry_backoff_base

    def _extract_error(status: int, resp: dict) -> str:
        body = resp.get("body", {})
        if isinstance(body, dict):
            err_obj = body.get("error", {})
            err_msg = err_obj.get("message", "") if isinstance(err_obj, dict) else str(body)
            err_code = err_obj.get("code", "") if isinstance(err_obj, dict) else ""
            return f"[Graph] HTTP {status}: {err_code} -- {err_msg}".rstrip(" -")
        return f"[Graph] HTTP {status}: {body}"

    def _send_one(batch_items: list[dict], payload: list[dict]) -> None:
        nonlocal submitted
        # Items and payload may shrink on retry (only 429s are re-sent)
        cur_items = list(batch_items)
        cur_payload = list(payload)
        total_ok = 0
        all_failed: list[tuple[str, str]] = []
        saw_throttle = False

        for attempt in range(_MAX_ITEM_RETRIES + 1):
            if attempt > 0:
                wait = _BACKOFF_BASE * (2 ** (attempt - 1))
                retry_after = None
                # Use Retry-After from first 429 response if available
                for r in responses:
                    if r.get("status") == 429:
                        headers = r.get("headers", {})
                        if isinstance(headers, dict):
                            retry_after = headers.get("Retry-After") or headers.get("retry-after")
                        break
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except (ValueError, TypeError):
                        pass
                # Hard cap: never wait more than 60s regardless of Retry-After
                _MAX_RETRY_WAIT = 60
                if wait > _MAX_RETRY_WAIT:
                    logger.warning(
                        "Retry-After of %.0fs exceeds cap; clamping to %ds",
                        wait, _MAX_RETRY_WAIT,
                    )
                    wait = _MAX_RETRY_WAIT
                logger.warning(
                    "Retrying %d throttled items in %.0fs (attempt %d/%d)",
                    len(cur_items), wait, attempt, _MAX_ITEM_RETRIES,
                )
                import time as _time
                _time.sleep(wait)

            try:
                responses = client.batch_requests(cur_payload)
                if config.debug_item_id:
                    logger.info(
                        "ITEM RESPONSE (attempt %d):\n%s",
                        attempt, json.dumps(responses, indent=2),
                    )
                elif logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "BATCH RESPONSE (attempt %d): %s",
                        attempt, json.dumps(responses, indent=2),
                    )
            except Exception as exc:
                logger.error("Graph $batch call failed for %d items: %s", len(cur_items), exc)
                failed_ids: list[str] = []
                with stats_lock:
                    for item in cur_items:
                        stats.failed_count += 1
                        fid = item.get("id", "unknown")
                        failed_ids.append(fid)
                        if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                            stats.failed_ids.append(fid)
                    submitted += len(cur_items)
                if dl_path:
                    append_failed_records(dl_path, failed_ids, object_type, f"[Graph] $batch POST failed: {exc}")
                if dashboard:
                    dashboard.chunk_ingested(object_type, total_ok, len(failed_ids) + len(all_failed))
                return

            # Sort responses into ok / retry (429) / permanent failure
            ok_this_round = 0
            retry_items: list[dict] = []
            retry_payload: list[dict] = []
            id_to_item = {str(j): item for j, item in enumerate(cur_items)}
            id_to_req = {str(j): req for j, req in enumerate(cur_payload)}
            failed_request_bodies: dict[str, Any] = {}
            failed_response_bodies: dict[str, Any] = {}
            accounted_ids: set[str] = set()  # Track which items got a response

            with stats_lock:
                if not responses:
                    # Graph returned empty/null response — all items unaccounted
                    logger.error(
                        "Graph $batch returned empty response for %d items — "
                        "marking all as failed",
                        len(cur_items),
                    )
                    for item in cur_items:
                        stats.failed_count += 1
                        fid = item.get("id", "unknown")
                        all_failed.append((fid, "[Graph] $batch returned empty response"))
                        if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                            stats.failed_ids.append(fid)
                else:
                    for resp in responses:
                        idx_str = str(resp.get("id", ""))
                        item = id_to_item.get(idx_str)
                        status = resp.get("status", 0)
                        if item is None:
                            logger.warning(
                                "Graph $batch response has unrecognised id '%s' — "
                                "cannot match to a submitted item",
                                idx_str,
                            )
                            continue

                        accounted_ids.add(idx_str)

                        if 200 <= status < 300:
                            if item.get("type") == "deleted":
                                stats.deleted_count += 1
                            else:
                                stats.success_count += 1
                            ok_this_round += 1

                        elif status == 429:
                            saw_throttle = True
                            # Queue for retry — don't count as failed yet
                            retry_items.append(item)
                            req = id_to_req.get(idx_str)
                            if req:
                                retry_payload.append(req)

                        elif status == 503:
                            # Transient Graph outage — retry without signalling
                            # throttle (503 is not a rate-limit, don't penalise
                            # adaptive concurrency for it).
                            retry_items.append(item)
                            req = id_to_req.get(idx_str)
                            if req:
                                retry_payload.append(req)
                            logger.warning("Graph 503 on item %s — will retry", item.get("id", "?"))

                        else:
                            # Permanent failure — capture request + response for debugging
                            stats.failed_count += 1
                            fid = item.get("id", "unknown")
                            detail = _extract_error(status, resp)
                            all_failed.append((fid, detail))
                            if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                                stats.failed_ids.append(fid)
                            req = id_to_req.get(idx_str)
                            if req:
                                failed_request_bodies[fid] = req.get("body", {})
                            failed_response_bodies[fid] = resp
                            logger.error("Graph batch item %s failed — %s", fid, detail)

                    # Detect items that got NO response at all (missing from Graph batch response)
                    for idx_str, item in id_to_item.items():
                        if idx_str not in accounted_ids:
                            stats.failed_count += 1
                            fid = item.get("id", "unknown")
                            all_failed.append((fid, "[Graph] No response received for this item in $batch"))
                            if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                                stats.failed_ids.append(fid)
                            logger.error(
                                "Graph batch item %s — no response received (missing from $batch response)",
                                fid,
                            )

            total_ok += ok_this_round

            if not retry_items:
                break  # No 429s — done

            # Renumber payload IDs for the retry batch
            for new_idx, req in enumerate(retry_payload):
                req["id"] = str(new_idx)
            cur_items = retry_items
            cur_payload = retry_payload

        else:
            # Exhausted all retries — mark remaining 429s as permanent failures
            with stats_lock:
                for item in cur_items:
                    stats.failed_count += 1
                    fid = item.get("id", "unknown")
                    all_failed.append((fid, "[Graph] HTTP 429: throttled after all retries"))
                    if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                        stats.failed_ids.append(fid)
                    logger.error("Graph batch item %s failed — 429 after %d retries", fid, _MAX_ITEM_RETRIES)

        with stats_lock:
            submitted += len(batch_items)

        if all_failed and dl_path:
            append_failed_records(
                dl_path, all_failed, object_type,
                request_bodies=failed_request_bodies,
                response_bodies=failed_response_bodies,
            )
        if dashboard:
            dashboard.chunk_ingested(object_type, total_ok, len(all_failed))
            for fid, detail in all_failed:
                dashboard.add_error(f"{object_type}/{fid} -- {detail}")

        # Adaptive concurrency feedback
        if concurrency:
            if saw_throttle:
                concurrency.on_throttle()
            else:
                concurrency.on_success()

    # Dispatch sub-batches with adaptive parallelism
    _graph_t0 = time.monotonic()
    i = 0
    while i < len(sub_batches):
        workers = concurrency.current if concurrency else 1
        window = sub_batches[i : i + workers]
        if workers <= 1:
            for batch_items, payload in window:
                try:
                    _send_one(batch_items, payload)
                except Exception as exc:
                    logger.error(
                        "[%s] Unexpected error in _send_one for sub-batch — "
                        "%d item(s) in this sub-batch may be lost: %s",
                        object_type, len(batch_items), exc, exc_info=True,
                    )
                    _sb_failed = [(it.get("id", "unknown"), f"[Graph] sub-batch crash: {exc}") for it in batch_items]
                    with stats_lock:
                        for it in batch_items:
                            stats.failed_count += 1
                            fid = it.get("id", "unknown")
                            if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                                stats.failed_ids.append(fid)
                        submitted += len(batch_items)
                    if dl_path:
                        append_failed_records(dl_path, _sb_failed, object_type)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_send_one, bi, pl): bi for bi, pl in window}
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        failed_batch = futures[f]
                        logger.error(
                            "[%s] Unexpected error in _send_one for sub-batch — "
                            "%d item(s) in this sub-batch may be lost: %s",
                            object_type, len(failed_batch), exc, exc_info=True,
                        )
                        _sb_failed = [(it.get("id", "unknown"), f"[Graph] sub-batch crash: {exc}") for it in failed_batch]
                        with stats_lock:
                            for it in failed_batch:
                                stats.failed_count += 1
                                fid = it.get("id", "unknown")
                                if len(stats.failed_ids) < _FAILED_IDS_DISPLAY_LIMIT:
                                    stats.failed_ids.append(fid)
                            submitted += len(failed_batch)
                        if dl_path:
                            append_failed_records(dl_path, _sb_failed, object_type)
        i += len(window)

    _graph_dur = time.monotonic() - _graph_t0
    logger.info(
        "[TIMING] Graph push %s: %.1fs for %d items in %d sub-batches (%.0f items/s, concurrency=%d)",
        object_type, _graph_dur, len(items_to_process), len(sub_batches),
        len(items_to_process) / _graph_dur if _graph_dur > 0 else 0,
        concurrency.current if concurrency else 1,
    )
    if dashboard:
        dashboard.record_phase_time(object_type, "Graph Push", _graph_dur)
    stats.record_phase_time(object_type, "Graph Push", _graph_dur)

    items_to_process.clear()
    return submitted


def _ingest_single_object_type(
    object_type: str,
    config: AppConfig,
    client: GraphClient,
    transformer: SalesforceItemTransformer,
    legacy_resolver: LegacyAclResolver | None,
    new_acl_resolver,
    new_acl_mapper,
    group_acl_builder,
    stats: IngestionStats,
    concurrency: _AdaptiveConcurrency,
    since: datetime | None,
    checkpoint: dict | None,
    since_iso: str | None,
    dl_path,
    dashboard,
    stats_lock: threading.Lock,
) -> int:
    """Ingest all chunks for a single Salesforce object type.

    Designed to run inside a ``ThreadPoolExecutor`` so multiple object
    types can be ingested in parallel.  All shared state (``stats``,
    ``concurrency``, ``dashboard``) is thread-safe.

    Returns the number of items submitted to the Graph API.
    """
    progress = logging.getLogger("progress")
    _USE_NEW_ACL_ENGINE = config.use_new_acl_engine
    _USE_GROUP_ACL = config.use_group_acl
    chunk_size = config.tuning.ingest_chunk_size
    graph_batch_size = config.tuning.ingest_graph_batch_size
    _connector_id = config.connector.id

    obj_config = get_object_config(object_type)
    if obj_config is None:
        logger.warning("No config found for object type '%s' — skipping", object_type)
        return 0

    ingested_count = 0
    chunk_index = 0
    _debug_item_id = config.debug_item_id

    # Pipeline: overlap Graph upload of chunk N with ACL resolution of chunk N+1
    _pipeline_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"graph-{object_type}")
    _pending_graph_future = None
    _next_chunk_t0 = time.monotonic()  # track SF fetch time

    try:
        for records in iter_object_chunks(config, obj_config, since, chunk_size):
            _fetch_dur = time.monotonic() - _next_chunk_t0            # ── Debug: single item filter ─────────────────────────────────
            if _debug_item_id:
                records = [r for r in records if r.get("Id") == _debug_item_id]
                if not records:
                    continue
                # Log the raw Salesforce record for single-item debugging
                logger.info(
                    "SALESFORCE RECORD — %s/%s\n%s",
                    object_type, _debug_item_id,
                    json.dumps(records[0], indent=2, default=str),
                )

            # ── Graceful stop ─────────────────────────────────────────────
            if dashboard and dashboard.stop_requested:
                if _pending_graph_future is not None:
                    ingested_count += _pending_graph_future.result()
                    _pending_graph_future = None
                break

            chunk_index += 1
            batch_size = len(records)
            with stats_lock:
                stats.total_fetched += batch_size
                stats.object_type_counts[object_type] = (
                    stats.object_type_counts.get(object_type, 0) + batch_size
                )

            logger.info(
                "\n" + "=" * 70 + "\nSALESFORCE CHUNK: %s chunk #%d (%d records)\n" + "=" * 70,
                object_type, chunk_index, batch_size,
            )
            logger.info(
                "[TIMING] SF fetch %s chunk #%d: %.1fs for %d records",
                object_type, chunk_index, _fetch_dur, batch_size,
            )
            progress.info(
                "  [%s] chunk #%d — fetched %d record(s)",
                object_type, chunk_index, batch_size,
            )
            if dashboard:
                dashboard.chunk_fetched(object_type, chunk_index, batch_size)
                dashboard.record_phase_time(object_type, "SF Fetch", _fetch_dur)
            stats.record_phase_time(object_type, "SF Fetch", _fetch_dur)

            # ── Checkpoint: skip already-completed chunks ─────────────────
            if checkpoint:
                completed_up_to = checkpoint["completed"].get(object_type, 0)
                if chunk_index <= completed_up_to:
                    logger.info("Skipping %s chunk #%d (already checkpointed)", object_type, chunk_index)
                    with stats_lock:
                        stats.skipped_count += batch_size
                    if dashboard:
                        dashboard.chunk_skipped(object_type, batch_size)
                    del records
                    continue

            # ── ACL resolution for this chunk ─────────────────────────────
            acl_map_for_chunk: dict[str, list[dict[str, str]]] = {}
            try:
                logger.info(
                    "Resolving ACLs for %d %s record(s) (chunk #%d)",
                    batch_size, object_type, chunk_index,
                )
                if dashboard:
                    dashboard.acl_started(object_type, chunk_index, batch_size)
                _acl_t0 = time.monotonic()
                if _USE_GROUP_ACL:
                    acl_map_by_object = group_acl_builder.resolve({object_type: records})  # type: ignore[union-attr]
                    acl_map_for_chunk = acl_map_by_object.get(object_type, {})
                    acl_map_by_object.clear()
                elif _USE_NEW_ACL_ENGINE:
                    acl_map_by_object = asyncio.run(
                        _resolve_acl_new_engine(
                            config, client, {object_type: records},
                            resolver=new_acl_resolver, mapper=new_acl_mapper,
                        )
                    )
                    acl_map_for_chunk = acl_map_by_object.get(object_type, {})
                    acl_map_by_object.clear()
                else:
                    acl_map_by_object = legacy_resolver.resolve({object_type: records})  # type: ignore[union-attr]
                    acl_map_for_chunk = acl_map_by_object.get(object_type, {})
                    acl_map_by_object.clear()
                _acl_dur = time.monotonic() - _acl_t0
                logger.info(
                    "[TIMING] ACL resolution %s chunk #%d: %.1fs for %d records (%.0f rec/s) → %d entries",
                    object_type, chunk_index, _acl_dur, batch_size,
                    batch_size / _acl_dur if _acl_dur > 0 else 0,
                    len(acl_map_for_chunk),
                )
                if dashboard:
                    dashboard.record_phase_time(object_type, "ACL", _acl_dur)
                stats.record_phase_time(object_type, "ACL", _acl_dur)
            except Exception as error:
                logger.exception(
                    "ACL resolution failed for %s chunk #%d (%d records), falling back to deny-everyone ACL: %s",
                    object_type, chunk_index, batch_size, error,
                )
                with stats_lock:
                    stats.acl_fallback_used = True
                # Log every affected item so operators can identify which items
                # received deny-everyone ACLs and need re-ingestion.
                affected_ids = [str(r.get("Id", "unknown")) for r in records]
                acl_fallback_failures = [
                    (rid, f"[ACL] Resolution failed — deny-everyone ACL fallback applied: {error}")
                    for rid in affected_ids
                ]
                if dl_path:
                    append_failed_records(dl_path, acl_fallback_failures, object_type)
                logger.warning(
                    "[%s] %d item(s) in chunk #%d received deny-everyone ACL fallback due to ACL engine error",
                    object_type, len(affected_ids), chunk_index,
                )

            # ── Wait for previous chunk's Graph upload (pipeline) ─────────
            if _pending_graph_future is not None:
                _wait_t0 = time.monotonic()
                try:
                    ingested_count += _pending_graph_future.result()
                except Exception as exc:
                    logger.error(
                        "[%s] Graph upload of previous chunk failed — "
                        "continuing with next chunk: %s",
                        object_type, exc, exc_info=True,
                    )
                _wait_dur = time.monotonic() - _wait_t0
                if _wait_dur > 0.5:
                    logger.info(
                        "[TIMING] Pipeline wait %s chunk #%d: %.1fs (ACL was faster than Graph push)",
                        object_type, chunk_index, _wait_dur,
                    )
                _pending_graph_future = None

            # ── Transform + push this chunk via Graph JSON batching ───────
            if dashboard:
                dashboard.set_activity(f"[{object_type}] chunk #{chunk_index} -- Pushing to Graph API")

            _chunk_records = records
            _chunk_acl = acl_map_for_chunk
            _chunk_ot = object_type
            _chunk_ci = chunk_index

            def _upload_chunk(
                recs=_chunk_records, acl=_chunk_acl, ot=_chunk_ot, ci=_chunk_ci,
            ) -> int:
                submitted = _ingest_chunk_graph_batch(
                    config, client, transformer, recs, acl, stats, graph_batch_size,
                    dl_path=dl_path, object_type=ot, dashboard=dashboard,
                    concurrency=concurrency, stats_lock=stats_lock,
                )
                write_checkpoint(_connector_id, since_iso, ot, ci)
                acl.clear()
                return submitted

            _pending_graph_future = _pipeline_pool.submit(_upload_chunk)
            del records
            del acl_map_for_chunk
            _next_chunk_t0 = time.monotonic()  # reset for next SF fetch measurement

        # ── Drain last pending upload ─────────────────────────────────────
        if _pending_graph_future is not None:
            try:
                ingested_count += _pending_graph_future.result()
            except Exception as exc:
                logger.error(
                    "[%s] Graph upload of final chunk failed: %s",
                    object_type, exc, exc_info=True,
                )
            _pending_graph_future = None
    finally:
        _pipeline_pool.shutdown(wait=True)

    if dashboard:
        dashboard.object_done(object_type)

    logger.info("[%s] Object ingestion complete — %d items submitted", object_type, ingested_count)
    return ingested_count


def ingest_content(config: AppConfig, client: GraphClient, since: datetime | None = None, dashboard=None) -> IngestionStats:
    """
    Ingest content from Salesforce — **parallel by object type**.

    Each Salesforce object type (User, Account, Contact, …) is ingested by
    an independent worker thread.  Workers share the Graph API concurrency
    budget (``_AdaptiveConcurrency``), the ACL resolver caches, and the
    dashboard.

    The number of concurrent object workers is controlled by the
    ``PARALLEL_OBJECT_WORKERS`` environment variable (default 3).
    """
    stats = IngestionStats()
    stats_lock = threading.Lock()
    progress = logging.getLogger("progress")
    logger.info("Starting ingestion process...")

    if since:
        logger.info("Incremental sync from: %s", since.isoformat())
    else:
        logger.info("Full sync (all items)")

    _USE_NEW_ACL_ENGINE: bool = config.use_new_acl_engine
    _USE_GROUP_ACL: bool = config.use_group_acl
    if _USE_GROUP_ACL:
        stats.acl_engine = "GROUP (group_acl_builder)"
    elif _USE_NEW_ACL_ENGINE:
        stats.acl_engine = "NEW (acl_engine)"
    else:
        stats.acl_engine = "LEGACY"

    chunk_size = config.tuning.ingest_chunk_size
    graph_batch_size = config.tuning.ingest_graph_batch_size
    max_workers = config.tuning.parallel_object_workers
    _concurrency = _AdaptiveConcurrency(config.tuning.graph_concurrent_batches)
    logger.info(
        "Batching config — chunk_size=%d, graph_batch_size=%d (max 20 per $batch), "
        "concurrent_batches=%d, parallel_objects=%d, acl_engine=%s",
        chunk_size, graph_batch_size, _concurrency.current, max_workers, stats.acl_engine,
    )

    transformer = SalesforceItemTransformer(
        config.connector.salesforce.instance_url,
        config.connector.schema,
        tenant_id=config.tenant_id,
    )

    # ── New ACL engine: initialise once ──────────────────────────────────────
    _new_acl_resolver = None
    _new_acl_mapper = None
    if _USE_NEW_ACL_ENGINE:
        from acl_engine import AclResolver as NewAclResolver, PrincipalMapper
        from acl_engine.salesforce_client import SalesforceClient
        from salesforce.api_client import get_salesforce_access_token

        _acl_sf_client = SalesforceClient(
            instance_url=config.connector.salesforce.instance_url,
            api_version=config.connector.salesforce.api_version,
            access_token=get_salesforce_access_token(config),
            token_refresher=lambda: get_salesforce_access_token(config),
        )
        _new_acl_resolver = NewAclResolver(
            _acl_sf_client,
            owd_field_map=config.owd_field_map,
            parent_map=config.parent_map,
            owd_overrides=config.owd_overrides,
            max_parent_depth=config.tuning.acl_max_parent_depth,
            use_entity_definition_owd=config.use_entity_definition_owd,
            object_names=config.object_names,
        )
        _new_acl_mapper = PrincipalMapper(
            sf_client=_acl_sf_client,
            graph_client=client,
            tenant_id=config.tenant_id,
            batch_size=config.tuning.salesforce_batch_size,
        )
        logger.info("New ACL engine initialised (identity cache persists across chunks)")
        # Pre-fetch OWD for each object so the dashboard can show ACL types
        _owd_fetcher = _new_acl_resolver._owd_fetcher
        _new_owd_labels: dict[str, str] = {}
        _OWD_LABELS = {
            "Private": "Private",
            "Read": "Public Read",
            "Edit": "Public Read/Write",
            "ReadEditTransfer": "Public Read/Write/Transfer",
            "All": "Public Full Access",
            "ControlledByParent": "ControlledByParent",
            "ControlledByCampaign": "ControlledByParent",
            "ControlledByLeadOrContact": "ControlledByParent",
        }
        for obj_name in config.object_names:
            raw = asyncio.run(_owd_fetcher.get_owd(obj_name))
            _new_owd_labels[obj_name] = _OWD_LABELS.get(raw, raw)
        if dashboard:
            dashboard.set_acl_types(_new_owd_labels)

    # Group ACL builder — initialise once (when USE_GROUP_ACL=true)
    _group_acl_builder = None
    if _USE_GROUP_ACL:
        from acl_engine.group_acl_builder import GroupAclBuilder
        from acl_engine.salesforce_client import SalesforceClient as _GrpSfClient
        from acl_engine import PrincipalMapper as _PrincipalMapper
        from salesforce.api_client import get_salesforce_access_token as _grp_get_token

        _grp_sf_client = _GrpSfClient(
            instance_url=config.connector.salesforce.instance_url,
            api_version=config.connector.salesforce.api_version,
            access_token=_grp_get_token(config),
            token_refresher=lambda: _grp_get_token(config),
        )
        _grp_principal_mapper = _PrincipalMapper(
            sf_client=_grp_sf_client,
            graph_client=client,
            tenant_id=config.tenant_id,
            batch_size=config.tuning.salesforce_batch_size,
        )
        _group_acl_builder = GroupAclBuilder(
            sf_client=_grp_sf_client,
            owd_overrides=config.owd_overrides,
            parent_map=config.parent_map,
            owd_field_map=config.owd_field_map,
            principal_mapper=_grp_principal_mapper,
            use_entity_definition_owd=config.use_entity_definition_owd,
            object_names=config.object_names,
        )
        logger.info("Group ACL builder initialised")
        # Pre-fetch OWD map so the dashboard can show ACL types
        _owd_labels = asyncio.run(_group_acl_builder.prewarm_owd())
        if dashboard:
            dashboard.set_acl_types(_owd_labels)

    # Legacy resolver — initialise once and pre-warm caches before workers start
    legacy_resolver = (
        None
        if _USE_NEW_ACL_ENGINE or _USE_GROUP_ACL
        else LegacyAclResolver(config, transformer.handlers, graph_client=client)
    )
    if legacy_resolver:
        logger.info("Pre-warming ACL caches before starting parallel object workers...")
        _pw = legacy_resolver.prewarm_caches()
        if asyncio.iscoroutine(_pw):
            asyncio.run(_pw)

    # ── Checkpointing & dead-letter setup ────────────────────────────────────
    _connector_id = config.connector.id
    _since_iso = since.isoformat() if since else None
    # Skip checkpoint for single-item ingestion — always re-ingest the requested record
    if config.debug_item_id:
        _checkpoint = None
    else:
        _checkpoint = read_checkpoint(_connector_id)
    if _checkpoint and _checkpoint.get("since") == _since_iso:
        logger.info("Checkpoint found — resuming from: %s", _checkpoint["completed"])
    else:
        _checkpoint = None
    _dl_path = failed_records_path(_connector_id)

    # ── Determine active object types ────────────────────────────────────────
    if config.debug_object_type:
        active_types = [config.debug_object_type]
    else:
        active_types = [c.object_type for c in OBJECT_CONFIGS]

    # Pre-populate dashboard with known object types and record counts
    if dashboard:
        dashboard.set_object_types(active_types)
        dashboard.set_activity("Querying Salesforce record counts...")
        from salesforce.api_client import get_object_counts
        total_counts = get_object_counts(config, since)
        dashboard.set_total_counts(total_counts)
        logger.info("Record counts: %s (total: %d)", total_counts, sum(total_counts.values()))

    # ── Launch parallel object workers ───────────────────────────────────────
    effective_workers = min(max_workers, len(active_types))
    logger.info(
        "Launching %d parallel object worker(s) for %d object type(s): %s",
        effective_workers, len(active_types), ", ".join(active_types),
    )
    progress.info(
        "  Ingesting %d object types in parallel (%d workers)",
        len(active_types), effective_workers,
    )

    total_ingested = 0

    with ThreadPoolExecutor(max_workers=effective_workers, thread_name_prefix="obj-ingest") as pool:
        futures = {
            pool.submit(
                _ingest_single_object_type,
                obj_type,
                config,
                client,
                transformer,
                legacy_resolver,
                _new_acl_resolver,
                _new_acl_mapper,
                _group_acl_builder,
                stats,
                _concurrency,
                since,
                _checkpoint,
                _since_iso,
                _dl_path,
                dashboard,
                stats_lock,
            ): obj_type
            for obj_type in active_types
        }

        for future in as_completed(futures):
            obj_type = futures[future]
            try:
                count = future.result()
                total_ingested += count
                logger.info("[%s] completed — %d items", obj_type, count)
            except _SkipObjectError as exc:
                logger.warning("[%s] skipped — %s", obj_type, exc)
            except Exception as exc:
                logger.exception("[%s] worker failed with an exception", obj_type)
                # Track the failure so it surfaces in the final stats
                with stats_lock:
                    obj_fetched = stats.object_type_counts.get(obj_type, 0)
                    if obj_fetched > 0:
                        stats.failed_count += obj_fetched
                        logger.error(
                            "[%s] Worker crash — %d fetched record(s) for this object type "
                            "may not have been ingested",
                            obj_type, obj_fetched,
                        )
                if _dl_path:
                    append_failed_records(
                        _dl_path,
                        [("WORKER_CRASH", f"[Worker] {obj_type} worker thread failed: {exc}")],
                        obj_type,
                    )

    # ── Finalise ─────────────────────────────────────────────────────────────
    if dashboard:
        dashboard.finish()

    progress.info(
        "  Ingestion complete: %d succeeded, %d failed, %d deleted",
        stats.success_count, stats.failed_count, stats.deleted_count,
    )
    logger.info("Ingestion complete. Total items ingested: %d", total_ingested)

    # ── Count reconciliation — detect silent drops ───────────────────────────
    accounted = stats.success_count + stats.failed_count + stats.deleted_count + stats.skipped_count
    if stats.total_fetched > 0 and accounted < stats.total_fetched:
        gap = stats.total_fetched - accounted
        logger.error(
            "COUNT MISMATCH — %d item(s) unaccounted for! "
            "fetched=%d, success=%d, failed=%d, deleted=%d, skipped=%d, accounted=%d",
            gap, stats.total_fetched, stats.success_count,
            stats.failed_count, stats.deleted_count, stats.skipped_count, accounted,
        )
    elif stats.total_fetched > 0:
        logger.info(
            "Count reconciliation OK — fetched=%d, accounted=%d "
            "(success=%d + failed=%d + deleted=%d + skipped=%d)",
            stats.total_fetched, accounted, stats.success_count,
            stats.failed_count, stats.deleted_count, stats.skipped_count,
        )

    _was_stopped = dashboard and dashboard.stop_requested
    if not _was_stopped:
        clear_checkpoint(_connector_id)
    if stats.failed_count and _dl_path.exists():
        logger.info("Failed items written to dead-letter file: %s", _dl_path)

    return stats

