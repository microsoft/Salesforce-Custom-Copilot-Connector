"""
Single Command: Deploy Connection → Register Schema → Ingest Items with ACLs

This script runs the complete end-to-end flow in one command.

Usage:
    python run_full_deployment.py
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from connector.connection import ensure_connection, is_connection_ready, set_search_settings
from connector.graph import GraphClient
from connector.ingest import ingest_content
from connector.schema import ensure_schema
from connector.settings import load_config


# Setup logging to both console and file
log_file = Path(__file__).parent / f"deployment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("deployment")
logger.info(f"📄 Logging to: {log_file}")


def run_full_deployment():
    """
    Run complete deployment: Connection → Schema → Ingestion with ACLs
    
    This is the single command that does everything.
    """
    try:
        # Step 0: Load configuration
        logger.info("=" * 70)
        logger.info("FULL DEPLOYMENT: Connection → Schema → Ingestion with ACLs")
        logger.info("=" * 70)
        
        config = load_config()
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Connector Name: %s", config.connector.name)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)
        
        # Step 1: Initialize Graph client
        logger.info("\n" + "=" * 70)
        logger.info("STEP 1: Initialize Graph API Client")
        logger.info("=" * 70)
        
        client = GraphClient(
            max_retries=config.tuning.graph_max_retries,
            retry_backoff_base=config.tuning.graph_retry_backoff_base,
        )
        logger.info("✓ Graph client initialized")
        
        # Step 2: Ensure connection exists
        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Create/Ensure Connection")
        logger.info("=" * 70)
        
        initial_timestamp = time.monotonic()
        if not ensure_connection(config, client, initial_timestamp):
            logger.error("❌ Failed to create/ensure connection")
            return False
        
        logger.info("✓ Connection ready: %s", config.connector.id)
        
        # Step 3: Register schema
        logger.info("\n" + "=" * 70)
        logger.info("STEP 3: Register Schema")
        logger.info("=" * 70)
        
        ensure_schema(config, client)
        logger.info("✓ Schema registered")
        
        # Step 4: Set search settings
        logger.info("\n" + "=" * 70)
        logger.info("STEP 4: Configure Search Settings")
        logger.info("=" * 70)
        
        set_search_settings(config, client)
        logger.info("✓ Search settings configured")
        
        # Step 5: Verify connection is ready
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
        
        # Step 6: Ingest content with ACLs
        logger.info("\n" + "=" * 70)
        logger.info("STEP 6: Ingest Items with ACLs")
        logger.info("=" * 70)
        
        logger.info("Using Salesforce API:")
        logger.info("  - Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  - API Version: %s", config.connector.salesforce.api_version)
        
        ingest_content(config, client, since=None)  # Full sync
        logger.info("✓ Ingestion completed")
        
        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("✅ DEPLOYMENT COMPLETE")
        logger.info("=" * 70)
        logger.info("Summary:")
        logger.info("  ✓ Connection created/verified: %s", config.connector.id)
        logger.info("  ✓ Schema registered")
        logger.info("  ✓ Search settings configured")
        logger.info("  ✓ Items ingested with ACLs")
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.exception("❌ Fatal error during deployment: %s", e)
        return False


if __name__ == "__main__":
    success = run_full_deployment()
    sys.exit(0 if success else 1)
