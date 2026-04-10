"""
Salesforce CRM Custom Connector - Unified CLI

All operations are available as subcommands of this single entry point.

Usage:
    python run.py <command> [options]

Commands:
    full-deployment          Deploy connection → schema → ingest items with ACLs
    ingest                   Ingest items only (connection & schema must already exist)
    single-item <item_id>    Ingest a single Salesforce record by ID
    single-object <type>     Ingest all records of a specific Salesforce object type

Examples:
    python run.py full-deployment
    python run.py ingest
    python run.py single-item 500f6000008iCNYAA2
    python run.py single-object Case
    python run.py single-object Account
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from Graph.connection import ensure_connection, is_connection_ready, set_search_settings
from Graph.graph import GraphClient
from Graph.ingest import ingest_content
from Graph.schema import ensure_schema
from Salesforce.settings import load_config


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging(prefix: str) -> Path:
    """Configure logging to both console and a timestamped file in logs/."""
    log_file = LOGS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_full_deployment(_args) -> bool:
    """Deploy connection → schema → ingest items with ACLs."""
    log_file = _setup_logging("deployment")
    logger = logging.getLogger("deployment")
    logger.info("📄 Logging to: %s", log_file)

    try:
        logger.info("=" * 70)
        logger.info("FULL DEPLOYMENT: Connection → Schema → Ingestion with ACLs")
        logger.info("=" * 70)

        config = load_config()
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

        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Create/Ensure Connection")
        logger.info("=" * 70)
        initial_timestamp = time.monotonic()
        if not ensure_connection(config, client, initial_timestamp):
            logger.error("❌ Failed to create/ensure connection")
            return False
        logger.info("✓ Connection ready: %s", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Register Schema")
        logger.info("=" * 70)
        ensure_schema(config, client)
        logger.info("✓ Schema registered")

        logger.info("\n" + "=" * 70)
        logger.info("STEP 4: Configure Search Settings")
        logger.info("=" * 70)
        set_search_settings(config, client)
        logger.info("✓ Search settings configured")

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

        logger.info("\n" + "=" * 70)
        logger.info("STEP 6: Ingest Items with ACLs")
        logger.info("=" * 70)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        logger.info("\n" + "=" * 70)
        logger.info("✅ DEPLOYMENT COMPLETE")
        logger.info("=" * 70)
        logger.info("  ✓ Connection created/verified: %s", config.connector.id)
        logger.info("  ✓ Schema registered")
        logger.info("  ✓ Search settings configured")
        logger.info("  ✓ Items ingested with ACLs")
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        return True

    except Exception as e:
        logging.getLogger("deployment").exception("❌ Fatal error during deployment: %s", e)
        return False


def cmd_ingest(_args) -> bool:
    """Ingest items only — connection & schema must already exist."""
    log_file = _setup_logging("ingestion")
    logger = logging.getLogger("ingestion_only")
    logger.info("📄 Logging to: %s", log_file)

    try:
        logger.info("=" * 70)
        logger.info("INGESTION ONLY: Ingest Items with ACLs")
        logger.info("=" * 70)

        config = load_config()
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

        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection not ready! Run 'python run.py full-deployment' first.")
            return False
        logger.info("✓ Connection is ready: %s", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Items with ACLs")
        logger.info("=" * 70)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        logger.info("\n" + "=" * 70)
        logger.info("✅ INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("  ✓ Connection verified (existing)")
        logger.info("  ✓ Schema verified (existing)")
        logger.info("  ✓ Items ingested with ACLs")
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        return True

    except Exception as e:
        logging.getLogger("ingestion_only").exception("❌ Fatal error during ingestion: %s", e)
        return False


def cmd_single_item(args) -> None:
    """Ingest a single Salesforce record by ID."""
    log_file = _setup_logging("single_item")
    logger = logging.getLogger("single_item")
    logger.info("📄 Logging to: %s", log_file)

    item_id: str = args.item_id

    try:
        logger.info("=" * 70)
        logger.info("SINGLE ITEM INGESTION: %s", item_id)
        logger.info("=" * 70)

        config = load_config()
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        os.environ["DEBUG_ITEM_ID"] = item_id
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

        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection is not ready. Please run connection setup first.")
            return
        logger.info("✓ Connection is ready: %s", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Single Item with ACL")
        logger.info("=" * 70)
        logger.info("  Item ID: %s", item_id)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        logger.info("\n" + "=" * 70)
        logger.info("✅ SINGLE ITEM INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("  ✓ Item ID: %s", item_id)
        logger.info("  ✓ Connection: %s", config.connector.id)
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)

    except Exception as error:
        logging.getLogger("single_item").exception("❌ Fatal error during ingestion: %s", error)
        raise


def cmd_single_object(args) -> None:
    """Ingest all records of a specific Salesforce object type."""
    log_file = _setup_logging("object")
    logger = logging.getLogger("single_object")
    logger.info("📄 Logging to: %s", log_file)

    object_type: str = args.object_type

    try:
        logger.info("=" * 70)
        logger.info("SINGLE OBJECT TYPE INGESTION: %s", object_type)
        logger.info("=" * 70)

        config = load_config()
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)

        os.environ["DEBUG_OBJECT_TYPE"] = object_type
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

        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection is not ready. Please run connection setup first.")
            return
        logger.info("✓ Connection is ready: %s", config.connector.id)

        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Object Type with ACL")
        logger.info("=" * 70)
        logger.info("  Object Type: %s", object_type)
        logger.info("  Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  API Version: %s", config.connector.salesforce.api_version)
        ingest_content(config, client, since=None)
        logger.info("✓ Ingestion completed")

        logger.info("\n" + "=" * 70)
        logger.info("✅ SINGLE OBJECT TYPE INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("  ✓ Object Type: %s", object_type)
        logger.info("  ✓ Connection: %s", config.connector.id)
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)

    except Exception as error:
        logging.getLogger("single_object").exception("❌ Fatal error during ingestion: %s", error)
        raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Salesforce CRM Custom Connector - Unified CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py full-deployment\n"
            "  python run.py ingest\n"
            "  python run.py single-item 500f6000008iCNYAA2\n"
            "  python run.py single-object Case\n"
            "  python run.py single-object Account\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = True

    # full-deployment
    subparsers.add_parser(
        "full-deployment",
        help="Deploy connection → schema → ingest items with ACLs",
    ).set_defaults(func=cmd_full_deployment)

    # ingest
    subparsers.add_parser(
        "ingest",
        help="Ingest items only (connection & schema must already exist)",
    ).set_defaults(func=cmd_ingest)

    # single-item
    p_item = subparsers.add_parser(
        "single-item",
        help="Ingest a single Salesforce record by ID",
    )
    p_item.add_argument("item_id", help="Salesforce record ID (e.g. 500f6000008iCNYAA2)")
    p_item.set_defaults(func=cmd_single_item)

    # single-object
    p_obj = subparsers.add_parser(
        "single-object",
        help="Ingest all records of a specific Salesforce object type",
    )
    p_obj.add_argument(
        "object_type",
        help="Salesforce object type (e.g. Case, Account, Opportunity, Customer_Project__c)",
    )
    p_obj.set_defaults(func=cmd_single_object)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    result = args.func(args)
    if isinstance(result, bool):
        sys.exit(0 if result else 1)
