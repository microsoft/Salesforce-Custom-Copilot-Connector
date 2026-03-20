from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = PROJECT_ROOT / "connector" / "references"

LOCAL_ENV_FILES = (
    REPO_ROOT / "env" / ".env.local.example",  # Changed to use .example file
    REPO_ROOT / "env" / ".env.local",
    REPO_ROOT / "env" / ".env.local.user",
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / ".env.local.user",
)

DISALLOWED_CONNECTOR_PREFIXES = (
    "Microsoft",
    "None",
    "Directory",
    "Exchange",
    "ExchangeArchive",
    "LinkedIn",
    "Mailbox",
    "OneDriveBusiness",
    "SharePoint",
    "Teams",
    "Yammer",
    "Connectors",
    "TaskFabric",
    "PowerBI",
    "Assistant",
    "TopicEngine",
    "MSFT_All_Connectors",
)


@dataclass(frozen=True)
class SalesforceSettings:
    instance_url: str
    api_version: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class ConnectorSettings:
    id: str
    name: str
    description: str
    schema: list[dict[str, Any]]
    template: dict[str, Any]
    salesforce: SalesforceSettings


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    connector: ConnectorSettings
    repo_root: Path
    use_mock_data: bool = True  # Enable mock data for testing


def _load_json(file_name: str) -> Any:
    with (REFERENCE_ROOT / file_name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _alias_env(target: str, source: str) -> None:
    if not os.getenv(target) and os.getenv(source):
        os.environ[target] = os.environ[source]


def load_local_environment() -> None:
    for env_file in LOCAL_ENV_FILES:
        if env_file.exists():
            load_dotenv(env_file, override=True)

    _alias_env("AZURE_CLIENT_ID", "AAD_APP_CLIENT_ID")
    _alias_env("AZURE_CLIENT_SECRET", "SECRET_AAD_APP_CLIENT_SECRET")
    _alias_env("AZURE_TENANT_ID", "AAD_APP_TENANT_ID")
    _alias_env("SALESFORCE_CLIENT_SECRET", "SECRET_SALESFORCE_CLIENT_SECRET")

    if os.getenv("TEAMSFX_ENV", "").lower() == "local":
        os.environ.setdefault("AZURE_FUNCTIONS_ENVIRONMENT", "Development")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"Invalid configuration: Missing {name}")


def validate_connector_id(connector_id: str) -> None:
    if not connector_id:
        raise ValueError("Connector ID is required.")
    if len(connector_id) < 3 or len(connector_id) > 32:
        raise ValueError("Connector ID must be between 3 and 32 characters long.")
    if not connector_id.isalnum():
        raise ValueError("Connector ID must contain only alphanumeric characters.")

    if any(connector_id.lower().startswith(prefix.lower()) for prefix in DISALLOWED_CONNECTOR_PREFIXES):
        disallowed = ", ".join(DISALLOWED_CONNECTOR_PREFIXES)
        raise ValueError(f"Connector ID cannot start with: {disallowed}.")


def load_config() -> AppConfig:
    load_local_environment()

    connector_id = _require_env("CONNECTOR_ID")
    validate_connector_id(connector_id)
    
    # Check if mock data mode is enabled
    use_mock_data = os.getenv("USE_MOCK_DATA", "false").lower() in ("true", "1", "yes")

    return AppConfig(
        client_id=_require_env("AZURE_CLIENT_ID"),
        repo_root=REPO_ROOT,
        use_mock_data=use_mock_data,
        connector=ConnectorSettings(
            id=connector_id,
            name=_require_env("CONNECTOR_NAME"),
            description=_require_env("CONNECTOR_DESCRIPTION"),
            schema=_load_json("graph-schema.json"),
            template=_load_json("template.json"),
            salesforce=SalesforceSettings(
                instance_url=_require_env("SALESFORCE_INSTANCE_URL"),
                api_version=_require_env("SALESFORCE_API_VERSION"),
                client_id=_require_env("SALESFORCE_CLIENT_ID"),
                client_secret=_require_env("SALESFORCE_CLIENT_SECRET"),
            ),
        ),
    )
