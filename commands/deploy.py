"""
full-deployment command — complete end-to-end connector setup.

Performs the following steps in order:

1. Load configuration from environment variables and config files.
2. Initialise the Microsoft Graph API client.
3. Create or verify the external connection.
4. Register the Graph connector schema.
5. Configure search display settings (result type / adaptive card).
6. Wait for the connection to reach the ``ready`` state.
7. Ingest all Salesforce items with ACL resolution.

Usage::

    python run.py full-deployment           # quiet console
    python run.py full-deployment --verbose  # detailed console output

Returns ``True`` on success, ``False`` on failure (exit code 1).
"""
import logging
import time

from graph.connection import ensure_connection, is_connection_ready, set_search_settings
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from graph.schema import ensure_schema
from salesforce.settings import load_config


def _clamp_hours(hours: int) -> int:
    """Clamp hours to the valid range [12, 168]."""
    return max(12, min(168, hours))


def _run_full_deployment(args) -> bool:
    """Execute a single full-deployment run. Returns True on success."""
    from commands import setup_logging, write_summary

    log_file, summary_file = setup_logging("deployment", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("deployment")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    connection_status = None
    stats = None
    config = None

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("FULL DEPLOYMENT: Connection → Schema → Ingestion with ACLs")
        logger.info("=" * 70)

        config = load_config()
        progress.info("Starting full deployment for connector '%s'...", config.connector.id)
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Connector Name: %s", config.connector.name)
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
        logger.info("STEP 2: Create/Ensure Connection")
        logger.info("=" * 70)
        initial_timestamp = time.monotonic()
        connection_status = ensure_connection(config, client, initial_timestamp)
        if connection_status is None:
            logger.error("❌ Failed to create/ensure connection")
            return False
        logger.info("✓ Connection ready: %s", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Register Schema")
        logger.info("=" * 70)
        ensure_schema(config, client)
        logger.info("✓ Schema registered")
        progress.info("  Schema registered")

        logger.info("\n" + "=" * 70)
        logger.info("STEP 4: Configure Search Settings")
        logger.info("=" * 70)
        set_search_settings(config, client)
        logger.info("✓ Search settings configured")
        progress.info("  Search settings configured")

        logger.info("\n" + "=" * 70)
        logger.info("STEP 5: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.warning("⚠ Connection not ready yet, waiting...")
            time.sleep(5)
            if not is_connection_ready(config, client):
                logger.error("❌ Connection still not ready")
                return False
        logger.info("✓ Connection is ready for ingestion")
        progress.info("  Connection ready")

        logger.info("\n" + "=" * 70)
        logger.info("STEP 6: Ingest Items with ACLs")
        logger.info("=" * 70)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        progress.info("  Starting ingestion...")
        stats = ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, connection_status, config.connector.id, elapsed, "FULL DEPLOYMENT")
        return stats.failed_count == 0

    except Exception as e:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, connection_status,
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, "FULL DEPLOYMENT (CRASHED)")
        logging.getLogger("deployment").exception("❌ Fatal error during deployment: %s", e)
        return False


def cmd_full_deployment(args) -> bool:
    """Deploy connection → schema → ingest items with ACLs.

    When ``--continuous`` is passed, the first iteration performs the full
    deployment and subsequent iterations re-ingest on a fixed schedule.
    """
    success = _run_full_deployment(args)

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
        _run_full_deployment(args)
