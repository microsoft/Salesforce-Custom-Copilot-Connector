"""
Single Item Ingestion: Ingest a specific item by ID

This script ingests a single Salesforce item with ACL debugging.

Usage:
    python run_single_item.py <item_id>
    
Example:
    python run_single_item.py 500f6000008iCNYAA2
"""

import logging
import os
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
log_file = Path(__file__).parent / f"single_item_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("single_item")
logger.info(f"📄 Logging to: {log_file}")


def run_single_item_ingestion(item_id: str):
    """
    Run ingestion for a single item by ID
    
    Args:
        item_id: The Salesforce record ID to ingest
    """
    try:
        # Step 0: Load configuration
        logger.info("=" * 70)
        logger.info("SINGLE ITEM INGESTION: %s", item_id)
        logger.info("=" * 70)
        
        config = load_config()
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)
        
        # Set the debug item ID in environment
        os.environ['DEBUG_ITEM_ID'] = item_id
        logger.info("  Debug Item ID: %s", item_id)
        
        # Step 1: Initialize Graph API client
        logger.info("")
        logger.info("=" * 70)
        logger.info("STEP 1: Initialize Graph API Client")
        logger.info("=" * 70)
        client = GraphClient()
        logger.info("✓ Graph client initialized")
        
        # Step 2: Verify connection
        logger.info("")
        logger.info("=" * 70)
        logger.info("STEP 2: Verify Connection Ready")
        logger.info("=" * 70)
        if not is_connection_ready(config, client):
            logger.error("❌ Connection is not ready. Please run connection setup first.")
            return
        logger.info("✓ Connection is ready: %s", config.connector.id)
        
        # Step 3: Ingest single item
        logger.info("")
        logger.info("=" * 70)
        logger.info("STEP 3: Ingest Single Item with ACL")
        logger.info("=" * 70)
        logger.info("Item ID: %s", item_id)
        logger.info("Using real Salesforce API:")
        logger.info("  - Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  - API Version: %s", config.connector.salesforce.api_version)
        
        ingest_content(config, client, since=None)
        
        logger.info("✓ Ingestion completed")
        
        # Summary
        logger.info("")
        logger.info("=" * 70)
        logger.info("✅ SINGLE ITEM INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("Summary:")
        logger.info("  ✓ Item ID: %s", item_id)
        logger.info("  ✓ Connection: %s", config.connector.id)
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        
    except Exception as error:
        logger.exception("❌ Fatal error during ingestion: %s", error)
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Error: Item ID is required")
        print("")
        print("Usage:")
        print("  python run_single_item.py <item_id>")
        print("")
        print("Example:")
        print("  python run_single_item.py 500f6000008iCNYAA2")
        print("  python run_single_item.py 500f6000008iCNbAAM")
        print("")
        sys.exit(1)
    
    item_id = sys.argv[1].strip()
    
    if not item_id:
        print("❌ Error: Item ID cannot be empty")
        sys.exit(1)
    
    print(f"\n🔍 Running ingestion for item: {item_id}")
    print("=" * 70)
    
    run_single_item_ingestion(item_id)
