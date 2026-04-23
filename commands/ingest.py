"""
ingest command — re-ingest items into an existing connection.

Assumes that ``full-deployment`` has already been run at least once so that
the Graph external connection and schema exist.  Steps:

1. Load configuration.
2. Initialise the Graph API client.
3. Verify the connection is in the ``ready`` state.
4. (If USE_GROUP_ACL) Run identity crawl to create/update external groups.
5. Ingest Salesforce items with ACL resolution.

Continuous mode
---------------
When ``--continuous`` is passed, iterations alternate between full and
incremental content crawls:

* ``--full-crawl-hours N``  — full crawl every N hours (default 24, min 12).
* ``--incremental-hours N`` — incremental crawl every N hours (default 4, min 1).

**Identity crawl** always runs as full (no incremental for groups).

**Incremental content crawl** only fetches Salesforce records modified since
the last successful content crawl (``LastModifiedDate >= since``).

Usage::

    python run.py ingest
    python run.py ingest --verbose
    python run.py ingest --continuous
    python run.py ingest --continuous --full-crawl-hours 24 --incremental-hours 4

Returns ``True`` on success, ``False`` on failure (exit code 1).
"""

import logging
import time
from datetime import datetime

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from graph.identity import run_identity_sync, record_content_crawl, get_last_content_crawl_time
from salesforce.settings import load_config


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _run_ingest(args, *, since: datetime | None = None, sync_type: str = "full") -> bool:
    """Execute a single ingestion run.

    Parameters
    ----------
    args      : Parsed CLI arguments.
    since     : If set, only fetch Salesforce records modified after this time
                (incremental content crawl).  ``None`` means full crawl.
    sync_type : ``"full"`` or ``"incremental"`` — recorded in the sync session.

    Returns ``True`` on success, ``False`` on failure.
    """
    from commands import setup_logging, write_summary

    prefix = "ingestion" if sync_type == "full" else "incremental"
    log_file, summary_file = setup_logging(prefix, verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("ingestion_only")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = None
    config = None

    try:
        config = load_config()
        progress.info(
            "Starting %s ingestion for connector '%s'...",
            sync_type, config.connector.id,
        )

        client = GraphClient(
            api_version=config.tuning.graph_api_version,
            max_retries=config.tuning.graph_max_retries,
            retry_backoff_base=config.tuning.graph_retry_backoff_base,
        )
        progress.info("  Graph client initialized")

        if not is_connection_ready(config, client):
            logger.error("Connection not ready! Run 'python run.py full-deployment' first.")
            return False
        progress.info("  Connection '%s' verified", config.connector.id)

        # ── Identity Crawl (group-based ACL only, always full) ────────────
        if config.use_group_acl:
            progress.info("  Running identity sync (group-based ACL)...")
            identity_stats = run_identity_sync(config, client)
            logger.info(
                "Identity sync: created=%d updated=%d deleted=%d unchanged=%d",
                identity_stats.groups_created, identity_stats.groups_updated,
                identity_stats.groups_deleted, identity_stats.groups_unchanged,
            )

        # ── Content Ingestion ─────────────────────────────────────────────
        if since:
            progress.info("  Starting incremental ingestion (since %s)...", since.isoformat())
        else:
            progress.info("  Starting full ingestion...")

        stats = ingest_content(config, client, since=since)
        logger.info("Ingestion completed (%s)", sync_type)

        try:
            record_content_crawl(config, stats, sync_type=sync_type)
        except Exception as rec_err:
            logger.warning("Could not record content crawl stats: %s", rec_err)

        elapsed = time.monotonic() - start_time
        label = f"INGESTION ({sync_type.upper()})"
        write_summary(summary_file, log_file, stats, "existing (verified)", config.connector.id, elapsed, label)
        return stats.failed_count == 0

    except Exception as e:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, "existing (verified)",
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, "INGESTION (CRASHED)")
        logging.getLogger("ingestion_only").exception("Fatal error during ingestion: %s", e)
        return False


def cmd_ingest(args) -> bool:
    """Ingest items only — connection & schema must already exist.

    When ``--continuous`` is passed, iterations alternate between full and
    incremental content crawls:

    * ``--full-crawl-hours N``  — full content crawl every N hours (default 24).
    * ``--incremental-hours N`` — incremental content crawl every N hours (default 4).

    Identity crawl always runs as full.  Incremental content crawl reads the
    last successful content crawl timestamp from the SQLite DB and passes it
    as ``since`` to ``ingest_content()``.
    """
    # First run: always full
    success = _run_ingest(args, since=None, sync_type="full")

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
            _run_ingest(args, since=None, sync_type="full")
            last_full_time = time.monotonic()
        else:
            progress.info("🔄 Starting scheduled INCREMENTAL crawl...")
            try:
                config = load_config()
                since = get_last_content_crawl_time(config)
            except Exception:
                since = None
            _run_ingest(args, since=since, sync_type="incremental")
