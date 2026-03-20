"""
Mock Salesforce Client for Testing Item Transformation Pipeline

This module provides a mock Salesforce client that returns pre-defined mock data
instead of making actual API calls. Useful for testing the transformation logic
from Salesforce records to Graph Connector items without a live Salesforce instance.

Usage:
    from tests.mock_salesforce_client import MockSalesforceClient
    
    client = MockSalesforceClient()
    records = client.get_records("Account", limit=5)
    # Returns mock Account records ready for transformation
"""

from __future__ import annotations

from typing import Any, Optional

from mock_data.salesforce_records import (
    get_account_records,
    get_case_records,
    get_contact_records,
    get_customer_project_records,
    get_lead_records,
    get_opportunity_records,
)
from mock_data.common import INSTANCE_URL, DEFAULT_RECORD_LIMIT


class MockSalesforceClient:
    """
    Mock Salesforce client that returns predefined test data.
    
    Simulates a Salesforce API client for testing without live connections.
    """
    
    def __init__(self, instance_url: str = INSTANCE_URL):
        """
        Initialize mock client.
        
        Args:
            instance_url: Salesforce instance URL for mock data
        """
        self.instance_url = instance_url
        self._record_generators = {
            "Account": get_account_records,
            "Lead": get_lead_records,
            "Contact": get_contact_records,
            "Opportunity": get_opportunity_records,
            "Case": get_case_records,
            "Customer_Project__c": get_customer_project_records,
        }
    
    def get_records(
        self,
        object_name: str,
        limit: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Get mock Salesforce records for a given object type.
        
        Args:
            object_name: Salesforce object type (e.g., "Account", "Contact")
            limit: Maximum number of records to return
            **kwargs: Additional query parameters (ignored in mock)
        
        Returns:
            Salesforce query result dict with "records" list
        
        Raises:
            ValueError: If object_name is not supported
        """
        if object_name not in self._record_generators:
            raise ValueError(
                f"Unsupported object type: {object_name}. "
                f"Available: {list(self._record_generators.keys())}"
            )
        
        generator = self._record_generators[object_name]
        record_limit = limit if limit is not None else DEFAULT_RECORD_LIMIT
        records = generator(record_limit)
        
        return {
            "totalSize": len(records),
            "done": True,
            "records": records,
        }
    
    def get_all_object_types(self) -> list[str]:
        """Get list of supported mock object types."""
        return list(self._record_generators.keys())
    
    def query(self, soql: str) -> dict[str, Any]:
        """
        Execute a mock SOQL query.
        
        This is a simplified implementation that extracts the object type
        from the SOQL and returns mock data. Does not actually parse/execute SOQL.
        
        Args:
            soql: SOQL query string
        
        Returns:
            Query result dict
        """
        # Simple object type extraction from "FROM ObjectName"
        soql_upper = soql.upper()
        from_idx = soql_upper.find("FROM ")
        if from_idx == -1:
            return {"totalSize": 0, "done": True, "records": []}
        
        object_part = soql[from_idx + 5:].strip().split()[0]
        
        # Try to match against known object types
        for obj_name in self._record_generators:
            if obj_name.upper() == object_part.upper():
                return self.get_records(obj_name)
        
        # Default: return empty result
        return {"totalSize": 0, "done": True, "records": []}


def get_mock_query_result(object_name: str, limit: int = 5) -> dict[str, Any]:
    """
    Convenience function to get a mock Salesforce query result.
    
    Args:
        object_name: Salesforce object type
        limit: Number of records to generate
    
    Returns:
        Mock query result ready for handler.construct_ingestion_items()
    
    Example:
        >>> query_result = get_mock_query_result("Account", limit=3)
        >>> items = handler.construct_ingestion_items(
        ...     query_result,
        ...     "https://example.salesforce.com",
        ...     schema_properties
        ... )
    """
    client = MockSalesforceClient()
    return client.get_records(object_name, limit)


def transform_mock_records_to_items(
    object_name: str,
    handler,
    schema_properties: set[str],
    limit: int = 5,
    instance_url: str = INSTANCE_URL,
    **handler_kwargs,
) -> list[dict]:
    """
    Complete pipeline: mock data → transformation → items.
    
    Args:
        object_name: Salesforce object type
        handler: SalesforceObjectHandler instance
        schema_properties: Set of registered schema property names
        limit: Number of records to generate
        instance_url: Salesforce instance URL
        **handler_kwargs: Additional kwargs for construct_ingestion_items
    
    Returns:
        List of transformed item dicts ready for Graph API
    
    Example:
        >>> from connector.item_converter import SalesforceObjectHandler
        >>> 
        >>> config = {
        ...     "objectName": "Account",
        ...     "selectedFields": {"Id": "Id", "Name": "Name"},
        ... }
        >>> handler = SalesforceObjectHandler(config)
        >>> schema_properties = {"Id", "Name", "Url", "ObjectName"}
        >>> 
        >>> items = transform_mock_records_to_items(
        ...     "Account",
        ...     handler,
        ...     schema_properties,
        ...     limit=3,
        ... )
        >>> 
        >>> assert len(items) == 3
        >>> assert all(item["type"] == "searchable" for item in items)
    """
    query_result = get_mock_query_result(object_name, limit)
    return handler.construct_ingestion_items(
        query_result,
        instance_url,
        schema_properties,
        **handler_kwargs,
    )


__all__ = [
    "MockSalesforceClient",
    "get_mock_query_result",
    "transform_mock_records_to_items",
]
