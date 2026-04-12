from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from salesforce.settings import (
    AppConfig, ConnectorSettings, SalesforceSettings, TuningSettings,
    load_graph_schema, load_schema_config, build_owd_field_map, build_parent_map,
)

API_VERSION = "v60.0"
INSTANCE_URL = "https://test.my.salesforce.com"
TENANT_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def test_config() -> AppConfig:
    """Build a fully populated AppConfig using real schema files but mock credentials."""
    schema = load_schema_config()
    return AppConfig(
        client_id="00000000-0000-0000-0000-000000000000",
        tenant_id=TENANT_ID,
        repo_root=PROJECT_ROOT,
        schema_config=schema,
        owd_field_map=build_owd_field_map(schema),
        parent_map=build_parent_map(schema),
        owd_overrides={},
        use_new_acl_engine=False,
        debug_object_type=None,
        debug_item_id=None,
        tuning=TuningSettings(
            graph_api_version="v1.0",
            graph_max_retries=4,
            graph_retry_backoff_base=2,
            connection_timeout_seconds=600,
            connection_retry_interval_seconds=15,
            schema_retry_interval_seconds=15,
            salesforce_query_limit=10,
            salesforce_batch_size=100,
            acl_max_parent_depth=5,
        ),
        connector=ConnectorSettings(
            id="SalesforceCRMTestAutomation",
            name="Salesforce CRM Test Automation",
            description="Mock connector configuration for automated tests.",
            schema=load_graph_schema(),
            template={"id": "display"},
            salesforce=SalesforceSettings(
                instance_url=INSTANCE_URL,
                api_version=API_VERSION,
                client_id="mock-salesforce-client-id",
                client_secret="mock-salesforce-client-secret",
            ),
        ),
    )


@pytest.fixture
def tenant_id() -> str:
    """Return a deterministic tenant GUID for test assertions."""
    return TENANT_ID