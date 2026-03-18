# Python Salesforce CRM Custom Connector

This folder contains the Python Azure Functions implementation of the Salesforce CRM Custom connector for Microsoft 365.

## Files

- [function_app.py](function_app.py): Azure Functions timers and HTTP routes
- [connector/settings.py](connector/settings.py): Environment loading, aliasing, and validation
- [connector/graph.py](connector/graph.py): Microsoft Graph client with long-running operation polling
- [connector/connection.py](connector/connection.py): External connection lifecycle
- [connector/schema.py](connector/schema.py): Schema deployment
- [connector/salesforce.py](connector/salesforce.py): Salesforce authentication and SOQL queries
- [connector/transform.py](connector/transform.py): Graph external item mapping
- [connector/ingest.py](connector/ingest.py): Content ingestion
- [connector/crawl_state.py](connector/crawl_state.py): Local crawl timestamp persistence
- [connector/references/graph-schema.json](connector/references/graph-schema.json): External connection schema
- [connector/references/template.json](connector/references/template.json): Search result template

## Configuration Model

The Python app reuses repository-level env files created from committed examples instead of introducing a second copy of all secrets:

- [../env/.env.local.example](../env/.env.local.example)
- [../env/.env.local.user.example](../env/.env.local.user.example)

Before the first run:

- Copy [../env/.env.local.example](../env/.env.local.example) to `../env/.env.local`
- Copy [../env/.env.local.user.example](../env/.env.local.user.example) to `../env/.env.local.user`
- Copy [local.settings.example.json](local.settings.example.json) to `local.settings.json`

At startup, [connector/settings.py](connector/settings.py) loads those files and maps:

- `AAD_APP_CLIENT_ID` -> `AZURE_CLIENT_ID`
- `AAD_APP_TENANT_ID` -> `AZURE_TENANT_ID`
- `SECRET_AAD_APP_CLIENT_SECRET` -> `AZURE_CLIENT_SECRET`
- `SECRET_SALESFORCE_CLIENT_SECRET` -> `SALESFORCE_CLIENT_SECRET`

That keeps the Python app compatible with the existing repository configuration for the Salesforce CRM Custom connector.

## Local Run Steps

1. Start Azurite in one terminal:

```powershell
azurite --silent --location .azurite --debug .azurite/debug.log
```

2. Start the Function app in another terminal:

```powershell
cd python_connector
Copy-Item local.settings.example.json local.settings.json
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
func start
```

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

## Notes

- The committed runtime template is [local.settings.example.json](local.settings.example.json), which should be copied to `local.settings.json` before running locally.
- Crawl state is stored in [../tmp/lastCrawl.json](../tmp/lastCrawl.json).
- The repository-level Teams Toolkit and Bicep deployment files still reflect the original project layout and are not switched over to the Python app in this change.
