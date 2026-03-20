"""
Retrieve an external item from Microsoft Graph API
"""
import logging
import sys
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from connector.graph import GraphClient

# Load environment variables
env_path = Path(__file__).parent / "env" / ".env.local"
if not env_path.exists():
    env_path = Path(__file__).parent / "env" / ".env.local.example"
load_dotenv(env_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CONNECTION_ID = "SFCRMDemoConnector2003"

def get_item(item_id: str):
    """Get an external item by ID"""
    # Use the same GraphClient as the rest of the codebase
    graph_client = GraphClient()
    
    try:
        logger.info(f"Retrieving item: {item_id}")
        
        # GET /external/connections/{connectionId}/items/{itemId}
        item = graph_client.get(f"/external/connections/{CONNECTION_ID}/items/{item_id}")
        
        logger.info(f"✓ Item retrieved successfully!")
        logger.info(f"\n{'='*70}")
        logger.info(f"Item Details:")
        logger.info(f"{'='*70}")
        logger.info(f"  ID: {item.get('id')}")
        
        if item.get('properties'):
            logger.info(f"\n  Properties:")
            for key, value in item['properties'].items():
                logger.info(f"    {key}: {value}")
        
        if item.get('acl'):
            logger.info(f"\n  ACL ({len(item['acl'])} entries):")
            for ace in item['acl']:
                logger.info(f"    - Type: {ace.get('type')}, Value: {ace.get('value')}, Access: {ace.get('accessType')}")
        
        if item.get('content'):
            content_value = item['content'].get('value', '')
            content_preview = content_value[:200] if content_value else ''
            logger.info(f"\n  Content: {content_preview}...")
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Full JSON Response:")
        logger.info(f"{'='*70}")
        logger.info(json.dumps(item, indent=2))
        
        return item
        
    except Exception as e:
        logger.error(f"✗ Error retrieving item: {e}")
        raise

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_item.py <ITEM_ID>")
        print("\nExample item IDs from ingestion:")
        print("  python get_item.py 006000000000002")
        print("  python get_item.py 006000000000003")
        sys.exit(1)
    
    item_id = sys.argv[1]
    get_item(item_id)
