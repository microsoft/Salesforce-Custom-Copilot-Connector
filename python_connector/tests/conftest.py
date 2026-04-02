from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from connector.settings import AppConfig, ConnectorSettings, SalesforceSettings

API_VERSION = "v60.0"
INSTANCE_URL = "https://test.my.salesforce.com"
TENANT_ID = "00000000-0000-0000-0000-000000000001"


def load_graph_schema() -> dict:
    schema_path = PROJECT_ROOT / "connector" / "references" / "graph-schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def test_config() -> AppConfig:
    return AppConfig(
        client_id="00000000-0000-0000-0000-000000000000",
        repo_root=PROJECT_ROOT.parent,
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
    return TENANT_ID