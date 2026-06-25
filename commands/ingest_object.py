# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ingest-object command — ingest all records of one Salesforce object type.

Useful for selectively syncing a single object (e.g. ``Case``, ``Account``,
``Opportunity``) without running a full ingestion.

Usage::

    python run.py ingest-object --type Case
    python run.py ingest-object --type Account --verbose
"""

import logging
import time
from dataclasses import replace

from graph.connection import is_connection_ready
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from salesforce.settings import load_config


def cmd_ingest_object(args) -> None:
    """Ingest all records of a specific Salesforce object type."""
    from commands import setup_logging, write_summary

    object_type: str = args.type
    label = f"INGEST OBJECT ({object_type})"

    log_file, summary_file = setup_logging(f"ingest_object_{object_type}", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("ingest_object")
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
        progress.info("Starting ingestion for object type '%s'...", object_type)
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        config = replace(config, debug_object_type=object_type)
        logger.info("  Object Type: %s", object_type)

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
        logger.info("STEP 3: Ingest Object Type with ACL")
        logger.info("=" * 70)
        logger.info("  Object Type: %s", object_type)
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
        logging.getLogger("ingest_object").exception("❌ Fatal error during ingestion: %s", error)
        raise
