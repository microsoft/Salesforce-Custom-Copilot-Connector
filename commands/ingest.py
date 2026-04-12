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

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config


def _clamp_hours(hours: int) -> int:
    """Clamp hours to the valid range [12, 168]."""
    return max(12, min(168, hours))


def _run_ingest(args) -> bool:
    """Execute a single ingestion run. Returns True on success."""
    from commands import setup_logging, write_summary

    log_file, summary_file = setup_logging("ingestion", verbose=getattr(args, "verbose", False))
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
        progress.info("Starting ingestion for connector '%s'...", config.connector.id)
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
        stats = ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

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

    When ``--continuous`` is passed, ingestion repeats on a fixed schedule.
    """
    success = _run_ingest(args)

    continuous = getattr(args, "continuous", False)
    if not continuous:
        return success

    from commands import reset_logging

    hours = _clamp_hours(getattr(args, "hours", 12))
    interval_seconds = hours * 3600
    progress = logging.getLogger("progress")
    progress.info("\n🔁 Continuous mode enabled — re-ingesting every %d hour(s). Press Ctrl+C to stop.\n", hours)

    while True:
        progress.info("⏳ Next ingestion in %d hour(s)...", hours)
        time.sleep(interval_seconds)

        reset_logging()
        progress.info("🔄 Starting scheduled re-ingestion...")
        _run_ingest(args)
