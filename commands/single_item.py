"""
single-item command — ingest one Salesforce record by its ID.

Useful for debugging a specific record's ingestion, ACL resolution, or
adaptive-card rendering without re-ingesting the entire dataset.

Sets the ``DEBUG_ITEM_ID`` environment variable so downstream code can
scope its SOQL queries to a single record.

Usage::

    python run.py single-item 500f6000008iCNYAA2
    python run.py single-item 500f6000008iCNYAA2 --verbose
"""

import logging
import time
from dataclasses import replace

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config


def cmd_single_item(args) -> None:
    """Ingest a single Salesforce record by ID."""
    from commands import setup_logging, write_summary

    log_file, summary_file = setup_logging("single_item", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("single_item")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = None
    config = None

    item_id: str = args.item_id

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("SINGLE ITEM INGESTION: %s", item_id)
        logger.info("=" * 70)

        config = load_config()
        progress.info("Starting single item ingestion for '%s'...", item_id)
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        config = replace(config, debug_item_id=item_id)
        logger.info("  Debug Item ID: %s", item_id)

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
            logger.error("❌ Connection is not ready. Please run connection setup first.")
            return
        logger.info("✓ Connection is ready: %s", config.connector.id)
        progress.info("  Connection '%s' verified (existing)", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Single Item with ACL")
        logger.info("=" * 70)
        logger.info("  Item ID: %s", item_id)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        progress.info("  Starting ingestion...")
        stats = ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)", config.connector.id, elapsed, f"SINGLE ITEM ({item_id})")

    except Exception as error:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, "existing (verified)",
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, f"SINGLE ITEM ({item_id}) (CRASHED)")
        logging.getLogger("single_item").exception("❌ Fatal error during ingestion: %s", error)
        raise
