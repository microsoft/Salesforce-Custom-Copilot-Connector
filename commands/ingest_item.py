# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ingest-item command — ingest a single Salesforce record by its ID.

Useful for inspecting a specific record's ingestion, ACL resolution, or
adaptive-card rendering without re-ingesting the entire dataset.

Usage::

    python run.py ingest-item --id 500f6000008iCNYAA2
    python run.py ingest-item --id 500f6000008iCNYAA2 --verbose
"""

import logging
import time
from dataclasses import replace

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config


def cmd_ingest_item(args) -> None:
    """Ingest a single Salesforce record by its ID."""
    from commands import setup_logging, write_summary

    item_id: str = args.id
    label = f"INGEST ITEM ({item_id})"

    log_file, summary_file = setup_logging("ingest_item", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("ingest_item")
    # Enable DEBUG on the converter logger so the field-mapping table is captured in the log file
    _connector_logger = logging.getLogger("salesforce_connector")
    _connector_logger.setLevel(logging.DEBUG)
    for h in logging.getLogger().handlers:
        if hasattr(h, 'baseFilename'):  # file handler
            h.setLevel(logging.DEBUG)
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    stats = None
    config = None

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("%s", label)
        logger.info("=" * 70)

        config = load_config()
        progress.info("Starting ingestion for item '%s'...", item_id)
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        object_type = getattr(args, "object_type", None)
        config = replace(config, debug_item_id=item_id)
        if object_type:
            config = replace(config, debug_object_type=object_type)
            logger.info("  Object Type: %s (user-supplied)", object_type)
        logger.info("  Item ID: %s", item_id)

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
            logger.error("❌ Connection is not ready. Please run 'full-deployment' first.")
            return
        logger.info("✓ Connection is ready: %s", config.connector.id)
        progress.info("  Connection '%s' verified (existing)", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Item with ACL")
        logger.info("=" * 70)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        progress.info("  Starting ingestion...")
        stats = ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        elapsed = time.monotonic() - start_time
        write_summary(summary_file, log_file, stats, "existing (verified)", config.connector.id, elapsed, label)

    except Exception as error:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, "existing (verified)",
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, f"{label} (CRASHED)")
        logging.getLogger("ingest_item").exception("❌ Fatal error during ingestion: %s", error)
        raise
