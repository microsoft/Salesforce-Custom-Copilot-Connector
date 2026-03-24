from __future__ import annotations

from pathlib import Path

from connector import settings


def test_load_config_does_not_read_example_env_files(monkeypatch, tmp_path: Path) -> None:
    example_env = tmp_path / ".env.local.example"
    local_env = tmp_path / ".env.local"

    example_env.write_text("USE_MOCK_DATA=true\n", encoding="utf-8")
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
        "SALESFORCE_INSTANCE_URL",
        "SALESFORCE_API_VERSION",
        "SALESFORCE_CLIENT_ID",
        "SECRET_SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_CLIENT_SECRET",
        "USE_MOCK_DATA",
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

    assert config.use_mock_data is False
