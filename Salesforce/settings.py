from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
import json
import os

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = REPO_ROOT / "config"

LOCAL_ENV_FILES = (
    REPO_ROOT / "env" / ".env.local",
    REPO_ROOT / "env" / ".env.local.user",
    REPO_ROOT / ".env.local",
    REPO_ROOT / ".env.local.user",
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


# ── JSON config loaders (cached for the process lifetime) ─────────────────

def _load_config_json(filename: str) -> dict[str, Any]:
    with (_CONFIG_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


@cache
def load_schema_config() -> dict[str, Any]:
    """Load and return the parsed ``config/schema.json``."""
    return _load_config_json("schema.json")


@cache
def load_graph_schema() -> dict[str, Any]:
    """Load and return the parsed ``config/graph-schema.json``."""
    return _load_config_json("graph-schema.json")


@cache
def load_template() -> dict[str, Any]:
    """Load and return the parsed ``config/template.json``."""
    return _load_config_json("template.json")


def build_owd_field_map(schema: dict[str, Any] | None = None) -> dict[str, str]:
    """``{objectName: owdField}`` for every object that declares one."""
    data = schema if schema is not None else load_schema_config()
    return {
        obj["objectName"]: obj["owdField"]
        for obj in data.get("objectList", [])
        if "owdField" in obj
    }


def build_parent_map(schema: dict[str, Any] | None = None) -> dict[str, tuple[str, str]]:
    """``{objectName: (parentFieldName, parentObjectName)}``."""
    data = schema if schema is not None else load_schema_config()
    result: dict[str, tuple[str, str]] = {}
    for obj in data.get("objectList", []):
        obj_name: str = obj.get("objectName", "")
        parent_obj: str = obj.get("parentObjectName", "")
        if obj_name and parent_obj:
            parent_field: str = obj.get("parentFieldName") or f"{parent_obj}Id"
            result[obj_name] = (parent_field, parent_obj)
    return result


@dataclass(frozen=True)
class ConnectorSettings:
    id: str
    name: str
    description: str
    schema: list[dict[str, Any]]
    template: dict[str, Any]
    salesforce: SalesforceSettings


@dataclass(frozen=True)
class TuningSettings:
    graph_api_version: str
    graph_max_retries: int
    graph_retry_backoff_base: int
    connection_timeout_seconds: int
    connection_retry_interval_seconds: int
    schema_retry_interval_seconds: int
    salesforce_query_limit: int
    salesforce_batch_size: int
    acl_max_parent_depth: int


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    connector: ConnectorSettings
    repo_root: Path
    tuning: TuningSettings
    schema_config: dict[str, Any]
    owd_field_map: dict[str, str]
    parent_map: dict[str, tuple[str, str]]


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

    cfg = load_schema_config()

    def _require_int_env(name: str) -> int:
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Invalid configuration: Missing {name}")
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"Invalid configuration: {name} must be an integer, got '{value}'")

    return AppConfig(
        client_id=_require_env("AZURE_CLIENT_ID"),
        repo_root=REPO_ROOT,
        schema_config=cfg,
        owd_field_map=build_owd_field_map(cfg),
        parent_map=build_parent_map(cfg),
        tuning=TuningSettings(
            graph_api_version=os.getenv("GRAPH_API_VERSION", "v1.0"),
            graph_max_retries=_require_int_env("GRAPH_MAX_RETRIES"),
            graph_retry_backoff_base=_require_int_env("GRAPH_RETRY_BACKOFF_BASE"),
            connection_timeout_seconds=_require_int_env("CONNECTION_TIMEOUT_SECONDS"),
            connection_retry_interval_seconds=_require_int_env("CONNECTION_RETRY_INTERVAL_SECONDS"),
            schema_retry_interval_seconds=_require_int_env("SCHEMA_RETRY_INTERVAL_SECONDS"),
            salesforce_query_limit=_require_int_env("SALESFORCE_QUERY_LIMIT"),
            salesforce_batch_size=_require_int_env("SALESFORCE_BATCH_SIZE"),
            acl_max_parent_depth=_require_int_env("ACL_MAX_PARENT_DEPTH"),
        ),
        connector=ConnectorSettings(
            id=connector_id,
            name=_require_env("CONNECTOR_NAME"),
            description=_require_env("CONNECTOR_DESCRIPTION"),
            schema=load_graph_schema(),
            template=load_template(),
            salesforce=SalesforceSettings(
                instance_url=_require_env("SALESFORCE_INSTANCE_URL"),
                api_version=_require_env("SALESFORCE_API_VERSION"),
                client_id=_require_env("SALESFORCE_CLIENT_ID"),
                client_secret=_require_env("SALESFORCE_CLIENT_SECRET"),
            ),
        ),
    )
