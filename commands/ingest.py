"""
ingest command — re-ingest items into an existing connection.

Assumes that ``full-deployment`` has already been run at least once so that
the Graph external connection and schema exist.  Steps:

1. Load configuration.
2. Initialise the Graph API client.
3. Verify the connection is in the ``ready`` state.
4. Ingest all Salesforce items with ACL resolution.

Usage::

    python run.py ingest
    python run.py ingest --verbose

Returns ``True`` on success, ``False`` on failure (exit code 1).
"""

import logging
import time
from datetime import datetime, timezone

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config
from config.sync_state import clear_failed_records, clear_checkpoint, failed_records_path
from dashboard import IngestionDashboard, HAS_RICH

# Identity crawl imports (used when USE_GROUP_ACL=true)
try:
    from graph.identity import run_identity_sync, record_content_crawl, get_last_content_crawl_time
except ImportError:
    run_identity_sync = None  # type: ignore[assignment]
    record_content_crawl = None  # type: ignore[assignment]
    get_last_content_crawl_time = None  # type: ignore[assignment]


def _clamp_hours(hours: int) -> int:
    """Clamp hours to the valid range [12, 168]."""
    return max(12, min(168, hours))


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _run_ingest(args, since: datetime | None = None) -> bool:
    """Execute a single ingestion run.

    Parameters
    ----------
    args  : CLI arguments.
    since : If set, only fetch SF records modified after this time (incremental).
            None means full crawl.
    """
    from commands import setup_logging, write_summary, restore_console_logging

    sync_type = "incremental" if since else "full"

    verbose = getattr(args, "verbose", False)
    use_dashboard = not verbose and HAS_RICH
    prefix = "ingestion" if sync_type == "full" else "incremental"
    log_file, summary_file = setup_logging(prefix, verbose=verbose, dashboard_mode=use_dashboard)
    logger = logging.getLogger("ingestion_only")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = None
    config = None

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("INGESTION ONLY: Ingest Items with ACLs")
        logger.info("=" * 70)

        config = load_config()

        # Full or incremental based on 'since' parameter
        if since is None:
            clear_failed_records(config.connector.id)
            clear_checkpoint(config.connector.id)

        progress.info("Starting %s ingestion for connector '%s'...", sync_type, config.connector.id)
        if since:
            progress.info("  Incremental sync (since %s)", since.isoformat())
        else:
            progress.info("  Full sync (all records)")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 1: Initialize Graph API Client")
        logger.info("=" * 70)
        client = GraphClient(
            api_version=config.tuning.graph_api_version,
            max_retries=config.tuning.graph_max_retries,
            retry_backoff_base=config.tuning.graph_retry_backoff_base,
        )
        logger.info("✓ Graph client initialized")
        progress.info("  Graph client initialized")

        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection not ready! Run 'python run.py full-deployment' first.")
            return False
        logger.info("✓ Connection is ready: %s", config.connector.id)
        progress.info("  Connection '%s' verified (existing)", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Items with ACLs")
        logger.info("=" * 70)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        progress.info("  Starting ingestion...")

        dashboard = None
        if use_dashboard:
            sync_label = f"Incremental (since {since.isoformat()})" if since else "Full sync"
            acl_label = "GROUP" if config.use_group_acl else ("NEW" if config.use_new_acl_engine else "LEGACY")
            try:
                rel_log = log_file.relative_to(config.repo_root)
            except (AttributeError, ValueError):
                rel_log = log_file
            dl_rel = failed_records_path(config.connector.id)
            try:
                dl_rel = dl_rel.relative_to(config.repo_root)
            except (AttributeError, ValueError):
                pass
            dashboard = IngestionDashboard(config.connector.id, sync_label, acl_label, rel_log, str(dl_rel))
            dashboard.start()

        try:
            # Identity Crawl: only on full sync, not incremental
            if sync_type == "full" and config.use_group_acl and run_identity_sync is not None:
                progress.info("  Running identity sync (group-based ACL)...")
                identity_stats = run_identity_sync(config, client)
                logger.info(
                    "Identity sync: created=%d updated=%d deleted=%d unchanged=%d",
                    identity_stats.groups_created, identity_stats.groups_updated,
                    identity_stats.groups_deleted, identity_stats.groups_unchanged,
                )

            stats = ingest_content(config, client, since=since, dashboard=dashboard)
        finally:
            if dashboard:
                dashboard.stop()
                restore_console_logging()

        logger.info("Ingestion completed (%s)", sync_type)

        # Record content crawl stats in SQLite
        if record_content_crawl is not None:
            try:
                record_content_crawl(config, stats, sync_type=sync_type)
            except Exception as rec_err:
                logger.warning("Could not record content crawl stats: %s", rec_err)

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)", config.connector.id, elapsed, "INGESTION")
        return stats.failed_count == 0

    except Exception as e:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, "existing (verified)",
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, "INGESTION (CRASHED)")
        logging.getLogger("ingestion_only").exception("❌ Fatal error during ingestion: %s", e)
        return False


def cmd_ingest(args) -> bool:
    """Ingest items only — connection & schema must already exist.

    When ``--incremental`` is passed, the first run uses the last successful
    content crawl timestamp from SQLite so only changed records are fetched.
    Falls back to a full crawl when no prior run is found.

    When ``--continuous`` is passed, ingestion repeats on a fixed schedule.
    """
    since = None
    if getattr(args, "incremental", False) and get_last_content_crawl_time is not None:
        try:
            config = load_config()
            since = get_last_content_crawl_time(config)
        except Exception:
            pass
        if since is not None:
            logging.getLogger("progress").info(
                "--incremental: resuming from %s", since.isoformat()
            )
        else:
            logging.getLogger("progress").info(
                "--incremental: no previous crawl found, running full crawl"
            )
    success = _run_ingest(args, since=since)

    continuous = getattr(args, "continuous", False)
    if not continuous:
        return success

    from commands import reset_logging

    full_hours = _clamp(getattr(args, "full_crawl_hours", 24), 12, 168)
    incr_hours = _clamp(getattr(args, "incremental_hours", 4), 1, 168)
    incr_interval = incr_hours * 3600
    full_interval = full_hours * 3600

    progress = logging.getLogger("progress")
    progress.info(
        "\n🔁 Continuous mode enabled:\n"
        "   Full crawl every %d hour(s)\n"
        "   Incremental crawl every %d hour(s)\n"
        "   Press Ctrl+C to stop.\n",
        full_hours, incr_hours,
    )

    last_full_time = time.monotonic()

    while True:
        progress.info("⏳ Next incremental crawl in %d hour(s)...", incr_hours)
        time.sleep(incr_interval)

        reset_logging()

        elapsed_since_full = time.monotonic() - last_full_time
        if elapsed_since_full >= full_interval:
            progress.info("🔄 Starting scheduled FULL crawl...")
            _run_ingest(args, since=None)
            last_full_time = time.monotonic()
        else:
            progress.info("🔄 Starting scheduled INCREMENTAL crawl...")
            since = None
            if get_last_content_crawl_time is not None:
                try:
                    config = load_config()
                    since = get_last_content_crawl_time(config)
                except Exception:
                    pass
            _run_ingest(args, since=since)
