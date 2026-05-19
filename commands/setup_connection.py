"""
setup-connection command — create/verify connection and register schema.

Performs the following steps in order:

1. Load configuration from environment variables and config files.
2. Initialise the Microsoft Graph API client.
3. Create or verify the external connection.
4. Register the Graph connector schema.
5. Configure search display settings (result type / adaptive card).
6. Wait for the connection to reach the ``ready`` state.

Stops after the connection is ready — does **not** ingest any content.
Useful for initial setup or re-registering an updated schema.

Usage::

    python run.py setup-connection           # quiet console
    python run.py setup-connection --verbose  # detailed console output

Returns ``True`` on success, ``False`` on failure (exit code 1).
"""
import logging
import time

from graph.connection import ensure_connection, is_connection_ready, set_search_settings
from graph.client import GraphClient
from graph.schema import ensure_schema
from salesforce.settings import load_config


def cmd_setup_connection(args) -> bool:
    """Create/verify the external connection and register the schema."""
    from commands import setup_logging

    verbose = getattr(args, "verbose", False)
    log_file, summary_file = setup_logging("setup_connection", verbose=verbose)
    logger = logging.getLogger("setup_connection")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()

    try:
        logger.info("📄 Logging to: %s", log_file)
        logger.info("=" * 70)
        logger.info("SETUP CONNECTION: Connection → Schema → Search Settings")
        logger.info("=" * 70)

        config = load_config()
        progress.info("Starting setup for connector '%s'...", config.connector.id)
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Connector Name: %s", config.connector.name)

        # ── Step 1: Graph client ──────────────────────────────────────────
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

        # ── Step 2: Connection ────────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Create/Ensure Connection")
        logger.info("=" * 70)
        initial_timestamp = time.monotonic()
        connection_status = ensure_connection(config, client, initial_timestamp)
        if connection_status is None:
            logger.error("❌ Failed to create/ensure connection")
            progress.info("❌ Connection creation failed")
            return False
        logger.info("✓ Connection ready: %s", config.connector.id)
        progress.info("  Connection ensured")

        # ── Step 3: Schema ────────────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Register Schema")
        logger.info("=" * 70)
        ensure_schema(config, client)
        logger.info("✓ Schema registered")
        progress.info("  Schema registered")

        # ── Step 4: Search settings ───────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 4: Configure Search Settings")
        logger.info("=" * 70)
        set_search_settings(config, client)
        logger.info("✓ Search settings configured")
        progress.info("  Search settings configured")

        # ── Step 5: Verify ready ──────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("STEP 5: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.warning("⚠ Connection not ready yet, waiting...")
            time.sleep(5)
            if not is_connection_ready(config, client):
                logger.error("❌ Connection still not ready")
                progress.info("❌ Connection not ready after wait")
                return False
        logger.info("✓ Connection is ready")
        progress.info("  Connection ready")

        elapsed = time.monotonic() - start_time
        logger.info("=" * 70)
        logger.info("SETUP COMPLETE in %.1fs", elapsed)
        logger.info("=" * 70)
        progress.info("✅ Setup complete in %.1fs — connection & schema are ready.", elapsed)
        progress.info("   Run 'python run.py full-deployment' or 'python run.py ingest' to ingest content.")
        return True

    except Exception:
        logger.exception("❌ Setup failed with an unexpected error")
        progress.info("❌ Setup failed — check %s for details.", log_file)
        return False
