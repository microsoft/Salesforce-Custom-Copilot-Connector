# Mock Salesforce Data Integration

This module provides mock Salesforce data and a mock client for testing the transformation pipeline without requiring a live Salesforce connection.

## Quick Start

```python
from connector.item_converter import SalesforceObjectHandler
from tests.mock_salesforce_client import transform_mock_records_to_items

# Configure handler
config = {
    "objectName": "Account",
    "selectedFields": {
        "Id": "Id",
        "Name": "Name",
        "Description": "Description",
    },
}
handler = SalesforceObjectHandler(config)

# Define schema
schema_properties = {"Id", "Name", "Description", "Url", "ObjectName"}

# Transform mock data → items
items = transform_mock_records_to_items(
    "Account",
    handler,
    schema_properties,
    limit=5,
)

# Use items for testing
assert len(items) == 5
assert items[0]["type"] == "searchable"
```

## Components

### 1. MockSalesforceClient

Simulates a Salesforce API client that returns predefined test data.

```python
from tests.mock_salesforce_client import MockSalesforceClient

client = MockSalesforceClient()

# Get records for a specific object type
result = client.get_records("Account", limit=10)
# Returns: {"totalSize": 10, "done": True, "records": [...]}

# Query with SOQL (simplified)
result = client.query("SELECT Id, Name FROM Contact")

# Get supported object types
object_types = client.get_all_object_types()
# Returns: ["Account", "Lead", "Contact", "Opportunity", "Case", "Customer_Project__c"]
```

### 2. Helper Functions

#### get_mock_query_result()

Quick way to get a Salesforce query result dict:

```python
from tests.mock_salesforce_client import get_mock_query_result

query_result = get_mock_query_result("Lead", limit=3)
# Ready to pass to handler.construct_ingestion_items()
```

#### transform_mock_records_to_items()

Complete pipeline in one function:

```python
from tests.mock_salesforce_client import transform_mock_records_to_items

items = transform_mock_records_to_items(
    object_name="Contact",
    handler=handler,
    schema_properties=schema_properties,
    limit=5,
)
```

## Available Mock Data

The following Salesforce object types have mock data:

- **Account**: Enterprise customer records with billing/shipping addresses
- **Contact**: Individual contact records with names, emails, phones
- **Lead**: Sales lead records with status and conversion data
- **Opportunity**: Sales opportunity records with amounts and stages
- **Case**: Support case records with priorities and statuses
- **Customer_Project__c**: Custom object records

## Testing Non-Schema Fields

Test the content enrichment feature (fields not in schema go to content):

```python
config = {
    "objectName": "Account",
    "selectedFields": {
        "Id": "Id",
        "Name": "Name",  # In schema
        "CustomField__c": "CustomLabel",  # NOT in schema
    },
}

schema_properties = {"Id", "Name", "Url", "ObjectName"}
# CustomLabel is NOT in schema

items = transform_mock_records_to_items("Account", handler, schema_properties)

# CustomField__c value will be in content, not properties
assert "CustomLabel" not in items[0]["properties"]
assert "CustomLabel:" in items[0]["content"]["parsedData"]
```

## Running Examples

Interactive examples showing various use cases:

```bash
# Run the example script
python -m tests.examples.mock_data_example

# Or run specific examples
python -c "from tests.examples.mock_data_example import example_basic_transformation; example_basic_transformation()"
```

## Running Tests

```bash
# Run all connector tests
pytest tests/ -v

# Run specific test file
pytest tests/test_connector_flow.py -v
pytest tests/test_mock_acl_flow.py -v

# Run with coverage
pytest tests/ --cov=connector --cov=tests.mock_salesforce_client -v
```

## Example: Complete Integration Test

```python
def test_complete_pipeline():
    """Complete test: mock data → transform → validate."""
    # 1. Setup
    from connector.item_converter import SalesforceObjectHandler
    from tests.mock_salesforce_client import MockSalesforceClient
    
    client = MockSalesforceClient()
    config = {
        "objectName": "Account",
        "selectedFields": {
            "Id": "Id",
            "Name": "Name",
            "Description": "Description",
            "Industry": "Industry",
        },
    }
    handler = SalesforceObjectHandler(config)
    schema_properties = {"Id", "Name", "Description", "Industry", "Url", "ObjectName"}
    
    # 2. Get mock data
    query_result = client.get_records("Account", limit=5)
    
    # 3. Transform
    items = handler.construct_ingestion_items(
        query_result,
        client.instance_url,
        schema_properties,
    )
    
    # 4. Validate
    assert len(items) == 5
    for item in items:
        assert item["type"] == "searchable"
        assert "Id" in item["properties"]
        assert "Name" in item["properties"]
        assert item["properties"]["ObjectName"] == "Account"
    
    # 5. Use items (e.g., send to Graph API in real scenario)
    print(f"✓ Successfully transformed {len(items)} items")
```

## Mock Data Structure

Each mock record includes:
- Standard Salesforce fields: `Id`, `IsDeleted`, `CreatedDate`, `LastModifiedDate`, etc.
- Object-specific fields: `Name`, `Description`, `Email`, `Phone`, etc.
- Nested objects: `Owner`, `CreatedBy`, `LastModifiedBy`
- Complex fields: Address objects, etc.

Example Account record:
```python
{
    "attributes": {"type": "Account"},
    "Id": "001000000000001AAA",
    "Name": "Acme Corporation 01",
    "Description": "Enterprise customer account sample 01 for connector tests.",
    "Industry": "Technology",
    "Website": "https://acme01.example.com",
    "BillingAddress": {
        "street": "1 Main Street",
        "city": "Seattle",
        "state": "WA",
        "postalCode": "98101",
        "country": "United States"
    },
    "OwnerId": "005000000000001AAA",
    "Owner": {"Name": "Owner User", "Id": "005000000000001AAA"},
    "IsDeleted": False,
    "CreatedDate": "2026-03-19T08:00:00.000+0000",
    ...
}
```

## Benefits

✅ **No Salesforce Connection Required**: Test transformation logic offline
✅ **Fast Tests**: No API latency, instant results
✅ **Predictable Data**: Consistent test data for reliable tests
✅ **Complete Coverage**: Test all object types and edge cases
✅ **Easy Debugging**: Simple, readable mock data structure
✅ **Flexible**: Customize mock data for specific test scenarios

## Next Steps

1. **Add Custom Mock Data**: Extend mock data generators for your specific use cases
2. **Test with ACLs**: Add ACL parameters when testing permission logic
3. **Integration Testing**: Use mock data to test complete ingestion pipeline
4. **Performance Testing**: Generate large volumes of mock data for load testing
