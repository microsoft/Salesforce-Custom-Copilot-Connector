# Salesforce CRM Custom Connector Demo Script

## Quick Overview

- Connector Name: `Salesforce-CRM-1803`
- Architecture: Python + Azure Functions
- Source system: Salesforce CRM
- Indexed objects: Account, Lead, Contact, Opportunity, Case, and `Customer_Project__c`
- Incremental sync: Every 12 hours

## Demo Goal

Show that the Salesforce CRM Custom connector can pull Salesforce CRM data into Microsoft Graph, publish it as an external connection, and make it searchable in Microsoft 365 using the Python implementation in [python_connector/](python_connector/README.md).

## 1. Show The Configuration

Open these files first:

- [env/.env.local.example](env/.env.local.example)
- [env/.env.local.user.example](env/.env.local.user.example)

Talking points:

- `CONNECTOR_NAME` is `Salesforce-CRM-1803` in [env/.env.local.example](env/.env.local.example#L6)
- `CONNECTOR_DESCRIPTION` identifies this as a Salesforce CRM Custom connector in [env/.env.local.example](env/.env.local.example#L7)
- Salesforce URL, API version, and client ID come from [env/.env.local.example](env/.env.local.example)
- Secrets are documented in [env/.env.local.user.example](env/.env.local.user.example)
- The Python app maps the existing `AAD_APP_*` and `SECRET_*` values into runtime credentials automatically in [python_connector/connector/settings.py](python_connector/connector/settings.py)

## 2. Show The Python Entry Point

Open [python_connector/function_app.py](python_connector/function_app.py).

Talking points:

- `deployConnection` runs on startup
- `fullCrawl` is scheduled daily
- `incrementalCrawl` is scheduled every 12 hours
- Local helper routes `clear` and `retract` are exposed for Development mode only

## 3. Show The Equivalent Python Modules

Open these files in order:

- [python_connector/connector/settings.py](python_connector/connector/settings.py)
- [python_connector/connector/connection.py](python_connector/connector/connection.py)
- [python_connector/connector/schema.py](python_connector/connector/schema.py)
- [python_connector/connector/salesforce.py](python_connector/connector/salesforce.py)
- [python_connector/connector/transform.py](python_connector/connector/transform.py)
- [python_connector/connector/ingest.py](python_connector/connector/ingest.py)

Talking points:

- `settings.py` validates connector settings and loads the schema/template JSON
- `connection.py` manages Graph connection creation, readiness checks, and deletion
- `schema.py` deploys the external connection schema
- `salesforce.py` authenticates with Salesforce and runs SOQL queries
- `transform.py` converts Salesforce records to Graph external items
- `ingest.py` uploads the external items into Microsoft Graph

## 4. Show The Object Configuration

Open [python_connector/connector/salesforce.py](python_connector/connector/salesforce.py).

Talking points:

- The object list is defined in `OBJECT_CONFIGS`
- Adding a new object means adding a new `SalesforceObjectConfig`
- The current demo keeps the `LIMIT 10` safety cap for development runs
- Incremental crawl uses `LastModifiedDate` filtering when a last-crawl timestamp is available

## 5. Show The Transformation Layer

Open [python_connector/connector/transform.py](python_connector/connector/transform.py).

Talking points:

- Titles and content are customized by Salesforce object type
- `FIELD_NAME_MAP` handles Graph-safe property names for custom fields
- ACLs are currently set to `everyone`, matching the existing project behavior

## 6. Run The Python Project Live

Use one terminal for Azurite:

```powershell
azurite --silent --location .azurite --debug .azurite/debug.log
```

Use a second terminal for the app:

```powershell
cd python_connector
Copy-Item local.settings.example.json local.settings.json
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
func start
```

What to call out while it runs:

- The app starts with `deployConnection`
- If Graph permissions still need tenant-wide admin consent, the console prints the consent URL
- The function host then creates the schema, applies the result template, and begins ingestion

## 7. Optional Local Operations

Open [python_connector/api.http](python_connector/api.http).

You can demonstrate:

- `POST /api/clear` to remove ingested items while keeping the connection
- `POST /api/retract` to delete the connection and reset crawl state

## 8. Validate In Microsoft 365

After the crawl completes:

1. Open the Microsoft 365 Admin Center Search and Intelligence connector page
2. Find the `Salesforce-CRM-1803` connection
3. Confirm connector results are enabled
4. Search in Microsoft 365 or Copilot for a known Salesforce account, lead, or opportunity

## Key Files To Keep Open During The Demo

| File | Why it matters |
| --- | --- |
| [env/.env.local.example](env/.env.local.example) | Main connector and Salesforce configuration template |
| [env/.env.local.user.example](env/.env.local.user.example) | Secrets template |
| [python_connector/function_app.py](python_connector/function_app.py) | Timer triggers and HTTP routes |
| [python_connector/connector/salesforce.py](python_connector/connector/salesforce.py) | Salesforce query logic |
| [python_connector/connector/transform.py](python_connector/connector/transform.py) | Graph item mapping |
| [python_connector/connector/connection.py](python_connector/connector/connection.py) | Graph connection lifecycle |
| [python_connector/connector/schema.py](python_connector/connector/schema.py) | Schema deployment |
| [python_connector/connector/ingest.py](python_connector/connector/ingest.py) | Item ingestion |

## Short Closing

This repository now has a Python Azure Functions implementation of a Salesforce CRM Custom connector that is functionally equivalent to the original TypeScript connector structure, while preserving the same Salesforce source, Graph schema, and result template behavior.
