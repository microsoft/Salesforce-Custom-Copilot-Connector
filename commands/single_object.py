"""
single-object command — ingest all records of one Salesforce object type.

Useful for selectively syncing a single object (e.g. ``Case``, ``Account``,
``Opportunity``, ``Customer_Project__c``) without running a full ingestion.

Sets the ``DEBUG_OBJECT_TYPE`` environment variable so downstream code can
scope its SOQL queries to the specified object type.

Usage::

    python run.py single-object Case
    python run.py single-object Account --verbose
"""

import logging
import time
from dataclasses import replace

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config


def cmd_single_object(args) -> None:
    """Ingest all records of a specific Salesforce object type."""
    from commands import setup_logging, write_summary

    log_file, summary_file = setup_logging("object", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("single_object")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = None
    config = None

    object_type: str = args.object_type

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("SINGLE OBJECT TYPE INGESTION: %s", object_type)
        logger.info("=" * 70)

        config = load_config()
        progress.info("Starting single object ingestion for '%s'...", object_type)
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        config = replace(config, debug_object_type=object_type)
        logger.info("  Debug Object Type: %s", object_type)

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
        logger.info("STEP 3: Ingest Object Type with ACL")
        logger.info("=" * 70)
        logger.info("  Object Type: %s", object_type)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        progress.info("  Starting ingestion...")
        stats = ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)", config.connector.id, elapsed, f"SINGLE OBJECT ({object_type})")

    except Exception as error:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, "existing (verified)",
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, f"SINGLE OBJECT ({object_type}) (CRASHED)")
        logging.getLogger("single_object").exception("❌ Fatal error during ingestion: %s", error)
        raise
