# Python Salesforce CRM Custom Connector

This directory contains the Azure Functions application that builds, updates, and maintains the Salesforce CRM external connection in Microsoft Graph.

## Application Structure

- [function_app.py](function_app.py): Timer triggers and HTTP routes
- [connector/settings.py](connector/settings.py): Environment loading, aliasing, and validation
- [connector/graph.py](connector/graph.py): Microsoft Graph client and long-running operation polling
- [connector/connection.py](connector/connection.py): External connection lifecycle
- [connector/schema.py](connector/schema.py): Schema deployment
- [connector/salesforce.py](connector/salesforce.py): Salesforce authentication and SOQL queries
- [connector/transform.py](connector/transform.py): Graph external item mapping
- [connector/ingest.py](connector/ingest.py): Content ingestion
- [connector/crawl_state.py](connector/crawl_state.py): Last crawl persistence
- [connector/references/graph-schema.json](connector/references/graph-schema.json): External connection schema
- [connector/references/template.json](connector/references/template.json): Search result template

## Configuration Sources

The app loads configuration from these files when they exist:

- [../env/.env.local.example](../env/.env.local.example) copied to `../env/.env.local`
- [../env/.env.local.user.example](../env/.env.local.user.example) copied to `../env/.env.local.user`
- Optional local overrides in `python_connector/.env.local`
- Optional local overrides in `python_connector/.env.local.user`
- [local.settings.example.json](local.settings.example.json) copied to `local.settings.json`

At startup, [connector/settings.py](connector/settings.py) maps:

- `AAD_APP_CLIENT_ID` -> `AZURE_CLIENT_ID`
- `AAD_APP_TENANT_ID` -> `AZURE_TENANT_ID`
- `SECRET_AAD_APP_CLIENT_SECRET` -> `AZURE_CLIENT_SECRET`
- `SECRET_SALESFORCE_CLIENT_SECRET` -> `SALESFORCE_CLIENT_SECRET`

Required values:

- Connector: `CONNECTOR_ID`, `CONNECTOR_NAME`, `CONNECTOR_DESCRIPTION`
- Microsoft Graph: `AAD_APP_CLIENT_ID`, `AAD_APP_TENANT_ID`, `SECRET_AAD_APP_CLIENT_SECRET`
- Salesforce: `SALESFORCE_INSTANCE_URL`, `SALESFORCE_API_VERSION`, `SALESFORCE_CLIENT_ID`, `SECRET_SALESFORCE_CLIENT_SECRET`

Use the Entra client secret value for `SECRET_AAD_APP_CLIENT_SECRET`. The secret ID will fail authentication.

## Local Run Steps

1. Start Azurite from the repository root:

```powershell
azurite --silent --location .azurite --debug .azurite/debug.log
```

2. Start the Function app:

```powershell
cd python_connector
Copy-Item local.settings.example.json local.settings.json
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
func start
```

If `python` is not on your Windows PATH, use `py -3` instead.

3. Watch the logs for:

- Environment loading
- Graph connection creation or validation
- Admin consent prompt, if required
- Schema deployment
- Salesforce queries
- Item ingestion progress

## Runtime Behavior

- `deployConnection` runs on startup and then yearly
- `fullCrawl` runs daily
- `incrementalCrawl` runs every 12 hours
- `clear` and `retract` routes are available only in Development mode

## Local HTTP Helpers

Use [api.http](api.http) against the local host:

- `POST /api/clear`
- `POST /api/retract`

## Executable Smoke Test

Run the connector end to end without waiting for the timer triggers:

```powershell
cd python_connector
.venv\Scripts\python.exe test_flow.py --check-acl --show-items 5
```

Demo-friendly example:

```powershell
cd python_connector
.venv\Scripts\python.exe test_flow.py --check-acl --show-items 3 --show-item-json
```

Useful options:

- `--clear-first`: delete existing external items before the test ingest
- `--retract-first`: delete the external connection and recreate it from scratch
- `--skip-ingest`: validate connection, schema, and readiness without uploading items
- `--since 2026-03-19T00:00:00Z`: run the ingest path as an incremental test
- `--show-item-json`: print the full JSON payload for the first verified Graph item
- `--trace-connector`: include the connector's internal INFO logs in the demo output

Expected markers in the output:

- `CONFIG_OK`
- `SALESFORCE_AUTH_OK`
- `READY_OK`
- `INGEST_OK`
- `ITEM_COUNT=<n>`
- `TEST_FLOW_OK`

## Team Notes

- [ACL_PARENT_CHILD_INHERITANCE.md](ACL_PARENT_CHILD_INHERITANCE.md): shareable explanation of schema-driven ACL parent-child inheritance and how it now follows `schema.json`

## Operational Notes

- The committed runtime template is [local.settings.example.json](local.settings.example.json)
- Crawl state is stored in [../tmp/lastCrawl.json](../tmp/lastCrawl.json)
- Deployment templates live in [../infra/azure.bicep](../infra/azure.bicep), [../infra/azure.parameters.json](../infra/azure.parameters.json), [../m365agents.local.yml](../m365agents.local.yml), and [../m365agents.yml](../m365agents.yml)
