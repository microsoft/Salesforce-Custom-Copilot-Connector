# Salesforce CRM Custom Connector for Microsoft 365

This repository contains a Python Azure Functions connector that pulls Salesforce CRM data into a Microsoft Graph external connection for Microsoft 365 search and Copilot experiences.

## What The Connector Does

- Reads connector settings from local environment files created from committed templates
- Authenticates to Microsoft Graph with `DefaultAzureCredential`
- Authenticates to Salesforce with OAuth 2.0 client credentials
- Creates or validates the external connection
- Deploys the external connection schema and result template
- Ingests Salesforce Account, Lead, Contact, Opportunity, Case, and `Customer_Project__c` records
- Runs a full crawl on startup and daily, plus an incremental crawl every 12 hours
- Exposes Development-only helper routes to clear items or retract the connection

## Repository Layout

- [python_connector/README.md](python_connector/README.md): Azure Functions application and module-level runbook
- [env/.env.local.example](env/.env.local.example): Local non-secret configuration template
- [env/.env.local.user.example](env/.env.local.user.example): Local secret configuration template
- [env/.env.dev.example](env/.env.dev.example): Shared dev environment template
- [env/.env.dev.user.example](env/.env.dev.user.example): Shared dev secret template
- [infra/azure.bicep](infra/azure.bicep): Azure deployment template
- [infra/azure.parameters.json](infra/azure.parameters.json): Deployment parameter mapping
- [DEMO_SCRIPT.md](DEMO_SCRIPT.md): Demo walkthrough

## Prerequisites

- Python 3.11 or later
- Azure Functions Core Tools v4
- Azurite or an Azure Storage connection string for timer-trigger state
- A Microsoft 365 tenant where Microsoft Graph connectors are allowed
- An Entra application with the required Microsoft Graph permissions
- A Salesforce Connected App configured for client credentials flow

## Configure The Connector

For local runs:

1. Copy [env/.env.local.example](env/.env.local.example) to `env/.env.local`
2. Copy [env/.env.local.user.example](env/.env.local.user.example) to `env/.env.local.user`
3. Copy [python_connector/local.settings.example.json](python_connector/local.settings.example.json) to `python_connector/local.settings.json`

For provisioned dev environments:

1. Copy [env/.env.dev.example](env/.env.dev.example) to `env/.env.dev`
2. Copy [env/.env.dev.user.example](env/.env.dev.user.example) to `env/.env.dev.user`

Important local values:

- [env/.env.local.example](env/.env.local.example): `CONNECTOR_ID`, `CONNECTOR_NAME`, `CONNECTOR_DESCRIPTION`, `SALESFORCE_INSTANCE_URL`, `SALESFORCE_API_VERSION`, `SALESFORCE_CLIENT_ID`, `AAD_APP_CLIENT_ID`, `AAD_APP_TENANT_ID`
- [env/.env.local.user.example](env/.env.local.user.example): `SECRET_SALESFORCE_CLIENT_SECRET`, `SECRET_AAD_APP_CLIENT_SECRET`

At startup, the app maps `AAD_APP_*` and `SECRET_*` values into the `AZURE_*` and `SALESFORCE_*` variables used by the runtime. The local copies of these files are ignored by Git.

For `SECRET_AAD_APP_CLIENT_SECRET`, use the client secret value from the Entra app registration. Do not use the secret ID.

## Run Locally

Start local storage from the repository root:

```powershell
azurite --silent --location .azurite --debug .azurite/debug.log
```

Start the Functions app in another terminal:

```powershell
cd python_connector
Copy-Item local.settings.example.json local.settings.json
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
func start
```

If `python` is not on your Windows PATH, use `py -3` instead.

## Runtime Flow

When the app starts, it:

1. Loads environment settings from local files
2. Creates or validates the external connection
3. Deploys the schema from [python_connector/connector/references/graph-schema.json](python_connector/connector/references/graph-schema.json)
4. Applies the result template from [python_connector/connector/references/template.json](python_connector/connector/references/template.json)
5. Starts ingestion

Scheduled operations:

- `deployConnection`: startup and yearly
- `fullCrawl`: daily
- `incrementalCrawl`: every 12 hours

## Local Helper Endpoints

Use [python_connector/api.http](python_connector/api.http) for local helper routes:

- `POST /api/clear`
- `POST /api/retract`

These routes are available only when the app is running in Development mode.

## Deployment Notes

- [m365agents.local.yml](m365agents.local.yml) and [m365agents.yml](m365agents.yml) define Microsoft 365 Agents Toolkit workflows
- [infra/azure.parameters.json](infra/azure.parameters.json) maps environment variables into deployment parameters
- [infra/azure.bicep](infra/azure.bicep) stores the Azure AD client secret in Key Vault and injects it into the Function App settings during Azure deployment

## Further Reading

See [python_connector/README.md](python_connector/README.md) for the application structure, configuration model, and runtime details.
