# Salesforce CRM Custom Connector for Microsoft 365

This repository now includes a Python Azure Functions implementation of the Salesforce CRM Custom connector for Microsoft 365 in [python_connector/README.md](python_connector/README.md).

The original TypeScript implementation is still present in the root of the repository as a reference during the migration. The new Python project is the runnable path documented in this repository.

## What The Python Project Does

- Reads connector and credential settings from local env files created from [env/.env.local.example](env/.env.local.example) and [env/.env.local.user.example](env/.env.local.user.example)
- Authenticates to Microsoft Graph with `DefaultAzureCredential`
- Authenticates to Salesforce with OAuth 2.0 client credentials
- Creates the Salesforce CRM Custom connector external connection, deploys the schema, applies the result template, and ingests Salesforce items
- Runs a full crawl on startup and an incremental crawl every 12 hours
- Exposes local-only helper endpoints to clear or retract the connection

## Equivalent Python Project

The Python app lives in [python_connector/](python_connector/README.md) and maps to the original TypeScript structure like this:

| TypeScript | Python | Responsibility |
| --- | --- | --- |
| [src/functions/connections.ts](src/functions/connections.ts) | [python_connector/function_app.py](python_connector/function_app.py) | Azure Functions triggers and routes |
| [src/config.ts](src/config.ts) | [python_connector/connector/settings.py](python_connector/connector/settings.py) | Config loading and validation |
| [src/graphClient.ts](src/graphClient.ts) | [python_connector/connector/graph.py](python_connector/connector/graph.py) | Microsoft Graph client |
| [src/connection.ts](src/connection.ts) | [python_connector/connector/connection.py](python_connector/connector/connection.py) | Connection lifecycle |
| [src/schema.ts](src/schema.ts) | [python_connector/connector/schema.py](python_connector/connector/schema.py) | Schema deployment |
| [src/custom/getAllItemsFromAPI.ts](src/custom/getAllItemsFromAPI.ts) | [python_connector/connector/salesforce.py](python_connector/connector/salesforce.py) | Salesforce fetch logic |
| [src/custom/getExternalItemFromItem.ts](src/custom/getExternalItemFromItem.ts) | [python_connector/connector/transform.py](python_connector/connector/transform.py) | Item transformation |
| [src/ingest.ts](src/ingest.ts) | [python_connector/connector/ingest.py](python_connector/connector/ingest.py) | Graph ingestion |
| [src/services/crawlService.ts](src/services/crawlService.ts) | [python_connector/connector/crawl_state.py](python_connector/connector/crawl_state.py) | Last crawl persistence |

## Prerequisites

- Python 3.11 or later
- Azure Functions Core Tools v4
- Azurite or an Azure Storage connection string for timer-trigger state
- A Microsoft 365 tenant where Microsoft Graph connectors are allowed
- An Entra application with the required Microsoft Graph permissions
- A Salesforce Connected App configured for client credentials flow

## Configure The Python App

Create local env files from the committed examples:

- Copy [env/.env.local.example](env/.env.local.example) to `env/.env.local`
- Copy [env/.env.local.user.example](env/.env.local.user.example) to `env/.env.local.user`

The Python implementation loads those local files automatically and maps the existing `AAD_APP_*` and `SECRET_*` values into the `AZURE_*` and `SALESFORCE_*` environment variables that the runtime needs.

The example connector name is already set to `Salesforce-CRM-1803` in [env/.env.local.example](env/.env.local.example#L6), and the description identifies it as a Salesforce CRM Custom connector.

## Run Locally

From the repository root, start local storage in one terminal:

```powershell
azurite --silent --location .azurite --debug .azurite/debug.log
```

Then start the Python Functions app in a second terminal:

```powershell
cd python_connector
Copy-Item local.settings.example.json local.settings.json
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
func start
```

If you already use the original workspace tooling for Azurite, `npm run storage` from the repository root is still fine.

## What Happens On Startup

When you run [python_connector/function_app.py](python_connector/function_app.py), the app:

1. Loads environment settings from local files copied from [env/.env.local.example](env/.env.local.example) and [env/.env.local.user.example](env/.env.local.user.example)
2. Creates or validates the external connection
3. Creates the Graph schema from [python_connector/connector/references/graph-schema.json](python_connector/connector/references/graph-schema.json)
4. Applies the adaptive card result template from [python_connector/connector/references/template.json](python_connector/connector/references/template.json)
5. Starts a full crawl immediately

Incremental crawl is scheduled every 12 hours in [python_connector/function_app.py](python_connector/function_app.py).

## Local Helper Endpoints

The Python project includes [python_connector/api.http](python_connector/api.http) for local helper routes:

- `POST /api/clear`
- `POST /api/retract`

These routes return `404` unless the app is running in Development mode.

## Project Notes

- The Python app keeps the connector schema and result template behavior equivalent to the TypeScript project by copying the same JSON assets into [python_connector/connector/references/](python_connector/connector/references).
- Crawl state is still stored in [tmp/lastCrawl.json](tmp/lastCrawl.json) so repeated local runs behave similarly to the original project.
- The existing Teams Toolkit and Bicep files at the repository root were not rewired to deploy the Python app. The new Python project is the equivalent runtime implementation; deployment automation can be updated separately if needed.

## Next Reference

For the Python runbook and file structure, use [python_connector/README.md](python_connector/README.md).
