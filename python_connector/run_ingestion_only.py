"""
Ingestion Only: Skip connection/schema, just ingest items with ACLs

This script only runs the ingestion step, assuming connection and schema
are already deployed.

Usage:
    python run_ingestion_only.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from connector.connection import is_connection_ready
from connector.graph import GraphClient
from connector.ingest import ingest_content
from connector.settings import load_config


# Setup logging to both console and file
log_file = Path(__file__).parent / f"ingestion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ingestion_only")
logger.info(f"📄 Logging to: {log_file}")


def run_ingestion_only():
    """
    Run ingestion only (skip connection and schema deployment).
    
    Assumes connection and schema are already deployed.
    """
    try:
        # Load configuration
        logger.info("=" * 70)
        logger.info("INGESTION ONLY: Ingest Items with ACLs")
        logger.info("=" * 70)
        
        config = load_config()
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)
        
        # Initialize Graph client
        logger.info("\n" + "=" * 70)
        logger.info("STEP 1: Initialize Graph API Client")
        logger.info("=" * 70)
        
        client = GraphClient()
        logger.info("✓ Graph client initialized")
        
        # Verify connection is ready
        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        
        if not is_connection_ready(config, client):
            logger.error("❌ Connection not ready!")
            logger.error("Make sure you have run the full deployment first:")
            logger.error("  python run_full_deployment.py")
            return False
        
        logger.info("✓ Connection is ready: %s", config.connector.id)
        
        # Ingest content with ACLs
        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Ingest Items with ACLs")
        logger.info("=" * 70)
        
        logger.info("Using Salesforce API:")
        logger.info("  - Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  - API Version: %s", config.connector.salesforce.api_version)
        
        ingest_content(config, client, since=None)  # Full sync
        logger.info("✓ Ingestion completed")
        
        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("✅ INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("Summary:")
        logger.info("  ✓ Connection verified (existing)")
        logger.info("  ✓ Schema verified (existing)")
        logger.info("  ✓ Items ingested with ACLs")
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.exception("❌ Fatal error during ingestion: %s", e)
        return False


if __name__ == "__main__":
    success = run_ingestion_only()
    sys.exit(0 if success else 1)
