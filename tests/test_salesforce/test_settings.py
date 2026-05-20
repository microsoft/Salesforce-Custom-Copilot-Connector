from __future__ import annotations

from salesforce.settings import (
    load_local_environment, load_config, validate_connector_id,
    build_object_name_list,
    LOCAL_ENV_FILES,
)
import salesforce.settings as settings


# ── build_object_name_list ────────────────────────────────────────────────────


def test_build_object_name_list_extracts_all_names():
    """build_object_name_list returns every objectName from the schema."""
    schema = {
        "objectList": [
            {"objectName": "Account", "owdField": "DefaultAccountAccess"},
            {"objectName": "Contact"},
            {"objectName": "Case", "owdField": "DefaultCaseAccess"},
        ]
    }
    result = build_object_name_list(schema)
    assert result == ["Account", "Contact", "Case"]


def test_build_object_name_list_skips_entries_without_object_name():
    """Entries missing 'objectName' should be silently skipped."""
    schema = {
        "objectList": [
            {"objectName": "Account"},
            {"description": "orphan entry with no objectName"},
            {"objectName": "Case"},
        ]
    }
    result = build_object_name_list(schema)
    assert result == ["Account", "Case"]


def test_build_object_name_list_empty_schema():
    """Empty objectList should return an empty list."""
    assert build_object_name_list({"objectList": []}) == []
    assert build_object_name_list({}) == []


# ── load_config ───────────────────────────────────────────────────────────────


def test_load_config_does_not_read_example_env_files(monkeypatch, tmp_path) -> None:
    example_env = tmp_path / ".env.local.example"
    local_env = tmp_path / ".env.local"

    example_env.write_text("CONNECTOR_ID=ignored\n", encoding="utf-8")
    local_env.write_text(
        "\n".join(
            [
                "CONNECTOR_ID=ACL22032026",
                "CONNECTOR_NAME=ACL22032026",
                "CONNECTOR_DESCRIPTION=Test connector",
                "AAD_APP_CLIENT_ID=00000000-0000-0000-0000-000000000000",
                "AAD_APP_TENANT_ID=00000000-0000-0000-0000-000000000000",
                "SALESFORCE_INSTANCE_URL=https://example.my.salesforce.com",
                "SALESFORCE_API_VERSION=v48.0",
                "SALESFORCE_CLIENT_ID=test-salesforce-client-id",
                "SECRET_SALESFORCE_CLIENT_SECRET=test-salesforce-client-secret",
                "GRAPH_MAX_RETRIES=4",
                "GRAPH_RETRY_BACKOFF_BASE=2",
                "CONNECTION_TIMEOUT_SECONDS=600",
                "CONNECTION_RETRY_INTERVAL_SECONDS=15",
                "SCHEMA_RETRY_INTERVAL_SECONDS=15",
                "SALESFORCE_BATCH_SIZE=100",
                "ACL_MAX_PARENT_DEPTH=5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    for name in (
        "CONNECTOR_ID",
        "CONNECTOR_NAME",
        "CONNECTOR_DESCRIPTION",
        "AAD_APP_CLIENT_ID",
        "AAD_APP_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_SECRET",
        "SALESFORCE_INSTANCE_URL",
        "SALESFORCE_API_VERSION",
        "SALESFORCE_CLIENT_ID",
        "SECRET_SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_CLIENT_SECRET",
        "GRAPH_MAX_RETRIES",
        "GRAPH_RETRY_BACKOFF_BASE",
        "CONNECTION_TIMEOUT_SECONDS",
        "CONNECTION_RETRY_INTERVAL_SECONDS",
        "SCHEMA_RETRY_INTERVAL_SECONDS",
        "SALESFORCE_BATCH_SIZE",
        "ACL_MAX_PARENT_DEPTH",
        "GRAPH_API_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        settings,
        "LOCAL_ENV_FILES",
        (
            local_env,
            tmp_path / ".env.local.user",
            tmp_path / "python_connector.env.local",
            tmp_path / "python_connector.env.local.user",
        ),
    )

    config = settings.load_config()
    # New EntityDefinition fields should have correct defaults
    assert config.use_entity_definition_owd is False
    assert isinstance(config.object_names, list)
    assert len(config.object_names) > 0  # schema.json has objects


def test_load_config_use_entity_definition_owd_true(monkeypatch, tmp_path) -> None:
    """USE_ENTITY_DEFINITION_OWD=true should be parsed into config."""
    local_env = tmp_path / ".env.local"
    local_env.write_text(
        "\n".join([
            "CONNECTOR_ID=ACL22032026",
            "CONNECTOR_NAME=ACL22032026",
            "CONNECTOR_DESCRIPTION=Test connector",
            "AAD_APP_CLIENT_ID=00000000-0000-0000-0000-000000000000",
            "AAD_APP_TENANT_ID=00000000-0000-0000-0000-000000000000",
            "SALESFORCE_INSTANCE_URL=https://example.my.salesforce.com",
            "SALESFORCE_API_VERSION=v48.0",
            "SALESFORCE_CLIENT_ID=test-salesforce-client-id",
            "SECRET_SALESFORCE_CLIENT_SECRET=test-salesforce-client-secret",
            "GRAPH_MAX_RETRIES=4",
            "GRAPH_RETRY_BACKOFF_BASE=2",
            "CONNECTION_TIMEOUT_SECONDS=600",
            "CONNECTION_RETRY_INTERVAL_SECONDS=15",
            "SCHEMA_RETRY_INTERVAL_SECONDS=15",
            "SALESFORCE_BATCH_SIZE=100",
            "ACL_MAX_PARENT_DEPTH=5",
            "USE_ENTITY_DEFINITION_OWD=true",
        ]) + "\n",
        encoding="utf-8",
    )
    for name in (
        "CONNECTOR_ID", "CONNECTOR_NAME", "CONNECTOR_DESCRIPTION",
        "AAD_APP_CLIENT_ID", "AAD_APP_TENANT_ID",
        "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_SECRET",
        "SALESFORCE_INSTANCE_URL", "SALESFORCE_API_VERSION",
        "SALESFORCE_CLIENT_ID", "SECRET_SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_CLIENT_SECRET",
        "GRAPH_MAX_RETRIES", "GRAPH_RETRY_BACKOFF_BASE",
        "CONNECTION_TIMEOUT_SECONDS", "CONNECTION_RETRY_INTERVAL_SECONDS",
        "SCHEMA_RETRY_INTERVAL_SECONDS", "SALESFORCE_BATCH_SIZE",
        "ACL_MAX_PARENT_DEPTH", "GRAPH_API_VERSION",
        "USE_ENTITY_DEFINITION_OWD",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        settings, "LOCAL_ENV_FILES",
        (local_env, tmp_path / ".env.local.user",
         tmp_path / "python_connector.env.local",
         tmp_path / "python_connector.env.local.user"),
    )
    config = settings.load_config()
    assert config.use_entity_definition_owd is True
