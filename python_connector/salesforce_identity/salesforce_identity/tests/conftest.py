"""
Pytest configuration for salesforce_identity tests.
"""

import pytest
from unittest.mock import AsyncMock, Mock


@pytest.fixture
def mock_sf_client():
    """Create a mock Salesforce client."""
    client = Mock()
    client.query = AsyncMock()
    client.query_all = AsyncMock()
    return client


@pytest.fixture
def instance_url():
    """Return test instance URL."""
    return "http://na172.salesforce.com"


@pytest.fixture
def access_token():
    """Return test access token."""
    return "BogusToken"
