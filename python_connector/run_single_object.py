"""
Single Object Type Ingestion: Ingest all items of a specific Salesforce object type

This script ingests all records of a single Salesforce object type with ACL.

Usage:
    python run_single_object.py <object_type>
    
Example:
    python run_single_object.py Case
    python run_single_object.py Account
    python run_single_object.py Opportunity
    python run_single_object.py Customer_Project__c
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
log_file = Path(__file__).parent / f"object_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("single_object")
logger.info(f"📄 Logging to: {log_file}")


def run_single_object_ingestion(object_type: str):
    """
    Run ingestion for all records of a specific object type
    
    Args:
        object_type: The Salesforce object type (e.g., 'Case', 'Account', 'Opportunity')
    """
    try:
        # Step 0: Load configuration
        logger.info("=" * 70)
        logger.info("SINGLE OBJECT TYPE INGESTION: %s", object_type)
        logger.info("=" * 70)
        
        config = load_config()
        logger.info("Configuration loaded:")
        logger.info("  Connector ID: %s", config.connector.id)
        logger.info("  Salesforce Instance: %s", config.connector.salesforce.instance_url)
        
        # Set the debug object type in environment
        os.environ['DEBUG_OBJECT_TYPE'] = object_type
        logger.info("  Debug Object Type: %s", object_type)
        
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
        
        # Step 3: Ingest objects
        logger.info("")
        logger.info("=" * 70)
        logger.info("STEP 3: Ingest Object Type with ACL")
        logger.info("=" * 70)
        logger.info("Object Type: %s", object_type)
        logger.info("Using real Salesforce API:")
        logger.info("  - Instance: %s", config.connector.salesforce.instance_url)
        logger.info("  - API Version: %s", config.connector.salesforce.api_version)
        
        ingest_content(config, client, since=None)
        
        logger.info("✓ Ingestion completed")
        
        # Summary
        logger.info("")
        logger.info("=" * 70)
        logger.info("✅ SINGLE OBJECT TYPE INGESTION COMPLETE")
        logger.info("=" * 70)
        logger.info("Summary:")
        logger.info("  ✓ Object Type: %s", object_type)
        logger.info("  ✓ Connection: %s", config.connector.id)
        logger.info("  📄 Full log: %s", log_file)
        logger.info("=" * 70)
        
    except Exception as error:
        logger.exception("❌ Fatal error during ingestion: %s", error)
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Error: Object type is required")
        print("")
        print("Usage:")
        print("  python run_single_object.py <object_type>")
        print("")
        print("Examples:")
        print("  python run_single_object.py Case")
        print("  python run_single_object.py Account")
        print("  python run_single_object.py Opportunity")
        print("  python run_single_object.py Contact")
        print("  python run_single_object.py Lead")
        print("  python run_single_object.py Customer_Project__c")
        print("")
        print("Available object types:")
        print("  - Account")
        print("  - Case")
        print("  - Contact")
        print("  - Lead")
        print("  - Opportunity")
        print("  - Customer_Project__c")
        print("")
        sys.exit(1)
    
    object_type = sys.argv[1].strip()
    
    if not object_type:
        print("❌ Error: Object type cannot be empty")
        sys.exit(1)
    
    print(f"\n🔍 Running ingestion for object type: {object_type}")
    print("=" * 70)
    
    run_single_object_ingestion(object_type)
