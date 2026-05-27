"""
retry-failed command — re-ingest every item recorded in the dead-letter file.

Reads ``logs/failed_records_<connector_id>.jsonl``, groups the entries by
Salesforce object type (using the ``object_type`` field written when the
failure was first recorded), then re-ingests each item via the normal
``ingest_content`` path.  Successfully re-ingested items are NOT removed
from the JSONL automatically — use ``--clear-on-success`` to wipe the file
once all retries pass.

Usage::

    python run.py retry-failed
    python run.py retry-failed --file logs/failed_records_MyConnector.jsonl
    python run.py retry-failed --verbose
    python run.py retry-failed --clear-on-success
"""

import logging
import time
from dataclasses import replace
from pathlib import Path

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config
from config.sync_state import read_failed_records, clear_failed_records, failed_records_path


def cmd_retry_failed(args) -> None:
    """Re-ingest every item recorded in the dead-letter JSONL file."""
    from commands import setup_logging, write_summary

    label = "RETRY FAILED RECORDS"
    log_file, summary_file = setup_logging("retry_failed", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("retry_failed")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = IngestionStats()
    config = None

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("%s", label)
        logger.info("=" * 70)

        config = load_config()
        connector_id = config.connector.id

        # ── Resolve dead-letter file path ────────────────────────────────────
        explicit_file = getattr(args, "file", None)
        if explicit_file:
            dl_path = Path(explicit_file)
            if not dl_path.is_absolute():
                dl_path = Path.cwd() / dl_path
        else:
            dl_path = failed_records_path(connector_id)

        logger.info("  Dead-letter file: %s", dl_path)

        # ── Read dead-letter file ────────────────────────────────────────────
        if not dl_path.exists():
            logger.info("✓ No dead-letter file found at %s — nothing to retry.", dl_path)
            progress.info("No failed records to retry.")
            elapsed = time.monotonic() - start_time
            write_summary(summary_file, log_file, stats, "N/A", connector_id, elapsed, label)
            return

        import json
        failed_entries: list[dict] = []
        with open(dl_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    failed_entries.append(json.loads(line))
        if not failed_entries:
            logger.info("✓ Dead-letter file is empty — nothing to retry.")
            progress.info("No failed records to retry.")
            elapsed = time.monotonic() - start_time
            write_summary(summary_file, log_file, stats, "N/A", connector_id, elapsed, label)
            return

        # Deduplicate by item_id (keep last occurrence — most recent failure).
        seen: dict[str, dict] = {}
        for entry in failed_entries:
            item_id = entry.get("item_id", "")
            if item_id:
                seen[item_id] = entry
        unique_entries = list(seen.values())

        logger.info("  Connector ID: %s", connector_id)
        logger.info("  Items to retry: %d (%d total entries, %d duplicates removed)",
                    len(unique_entries), len(failed_entries), len(failed_entries) - len(unique_entries))
        progress.info("Found %d unique item(s) to retry.", len(unique_entries))

        # ── Initialise Graph client ──────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 1: Initialize Graph API Client")
        logger.info("=" * 70)
        client = GraphClient(
            api_version=config.tuning.graph_api_version,
            max_retries=config.tuning.graph_max_retries,
            retry_backoff_base=config.tuning.graph_retry_backoff_base,
        )
        logger.info("✓ Graph client initialized")

        # ── Verify connection ────────────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection is not ready. Please run 'full-deployment' first.")
            return
        logger.info("✓ Connection is ready: %s", connector_id)

        # ── Re-ingest each item ──────────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Re-ingest Failed Items")
        logger.info("=" * 70)

        all_success = True
        still_failing: list[dict] = []  # entries that failed again — written to a new file at the end
        for idx, entry in enumerate(unique_entries, start=1):
            item_id = entry["item_id"]
            object_type = entry.get("object_type") or None
            original_error = entry.get("error", "")

            logger.info("[%d/%d] Retrying %s (object_type=%s, original_error=%s)",
                        idx, len(unique_entries), item_id, object_type or "auto-detect", original_error[:120])
            progress.info("  [%d/%d] Retrying %s…", idx, len(unique_entries), item_id)

            item_config = replace(config, debug_item_id=item_id)
            if object_type:
                item_config = replace(item_config, debug_object_type=object_type)

            try:
                item_stats = ingest_content(item_config, client, since=None)
                stats.success_count += item_stats.success_count
                stats.failed_count += item_stats.failed_count
                stats.deleted_count += item_stats.deleted_count
                stats.total_fetched += item_stats.total_fetched
                # Reflect the actual ACL engine used (set from the first item that runs).
                if stats.acl_engine == "LEGACY" and item_stats.acl_engine != "LEGACY":
                    stats.acl_engine = item_stats.acl_engine
                if item_stats.failed_count:
                    all_success = False
                    still_failing.append(entry)
                    logger.warning("  ✗ %s still failed after retry", item_id)
                else:
                    logger.info("  ✓ %s ingested successfully", item_id)
            except Exception as exc:
                all_success = False
                still_failing.append(entry)
                stats.failed_count += 1
                logger.exception("  ✗ Exception while retrying %s: %s", item_id, exc)

        # ── Write still-failing items to a new file ────────────────────────
        # These can be targeted directly on the next retry-failed run via --file.
        if still_failing:
            import json as _json
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            pending_path = dl_path.parent / f"retry_pending_{connector_id}_{ts}.jsonl"
            with open(pending_path, "w", encoding="utf-8") as fh:
                for rec in still_failing:
                    fh.write(_json.dumps(rec, default=str) + "\n")
            logger.warning(
                "  %d item(s) still failing — written to: %s",
                len(still_failing), pending_path,
            )
            logger.warning(
                "  To retry only these items run: python run.py retry-failed --file %s",
                pending_path,
            )

        # ── Optionally clear the dead-letter file ────────────────────────────
        if all_success and getattr(args, "clear_on_success", False):
            dl_path.unlink(missing_ok=True)
            logger.info("✓ Dead-letter file cleared (all retries succeeded).")
        elif getattr(args, "clear_on_success", False) and not all_success:
            logger.warning("Some items still failed — dead-letter file NOT cleared.")

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)", connector_id, elapsed, label)
        logger.info("✓ Retry run complete")

    except Exception as error:
        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)",
                      getattr(config, "connector", None) and config.connector.id or "unknown",
                      elapsed, f"{label} (CRASHED)")
        logging.getLogger("retry_failed").exception("❌ Fatal error during retry: %s", error)
