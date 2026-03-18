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

## Operational Notes

- The committed runtime template is [local.settings.example.json](local.settings.example.json)
- Crawl state is stored in [../tmp/lastCrawl.json](../tmp/lastCrawl.json)
- Deployment templates live in [../infra/azure.bicep](../infra/azure.bicep), [../infra/azure.parameters.json](../infra/azure.parameters.json), [../m365agents.local.yml](../m365agents.local.yml), and [../m365agents.yml](../m365agents.yml)
