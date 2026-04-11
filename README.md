# Salesforce CRM Custom Connector for Microsoft Graph

A Python-based connector that syncs Salesforce CRM data into **Microsoft Search** via the [Microsoft Graph External Connectors API](https://learn.microsoft.com/en-us/graph/connecting-external-content-connectors-overview). It fetches Salesforce objects (Accounts, Contacts, Leads, Opportunities, Cases, and custom objects), resolves record-level ACLs from Salesforce's sharing model, and ingests them as searchable items with proper access control.

---

## Features

- **Multi-object sync** — Accounts, Contacts, Leads, Opportunities, Cases, and custom objects (e.g. `Customer_Project__c`)
- **Fine-grained ACL resolution** — Respects Salesforce Org-Wide Defaults, role hierarchies, sharing rules, territories, queues, and public groups
- **Adaptive Card search results** — Configurable result templates for Microsoft Search
- **CLI with multiple commands** — Full deployment, re-ingestion, single-item/object debugging
- **Progress and summary logging** — Real-time progress on console, detailed logs in files, run summary with success/failure counts
- **Self-healing queries** — Automatically retries Salesforce queries when fields aren't available in the target org

---

## Project Structure

```
├── run.py                  # CLI entry point
├── commands/               # CLI subcommand implementations
├── config/                 # JSON schema, Graph properties, and result templates
├── env/                    # Environment variable config files (.env.local)
├── graph/                  # Microsoft Graph API client and ingestion pipeline
├── item/                   # Salesforce → Graph external item conversion
├── salesforce/             # Salesforce REST API client and configuration
├── acl_engine/             # ACL resolution engine (OWD, shares, roles, territories)
├── logs/                   # Runtime-generated log and summary files
├── docs/                   # Architecture documentation (ACL data flow diagrams)
└── tests/                  # Automated tests and mock data fixtures
```

Each folder contains its own `README.md` with detailed documentation.

---

## Prerequisites

### 1. Python 3.10+

Download and install from [python.org](https://www.python.org/downloads/).

Verify your installation:

```bash
python --version   # Should be 3.10 or higher
pip --version
```

### 2. Salesforce Connected App

You need a Salesforce Connected App with OAuth 2.0 credentials to allow API access.

**Steps:**

1. Log in to your Salesforce org as an admin.
2. Navigate to **Setup → App Manager → New Connected App**.
3. Fill in:
   - **Connected App Name**: e.g. `Graph Connector`
   - **API (Enable OAuth Settings)**: ✅ Check
   - **Callback URL**: `https://login.salesforce.com/services/oauth2/callback`
   - **Selected OAuth Scopes**:
     - `Full access (full)`
     - `Perform requests at any time (refresh_token, offline_access)`
     - `Access and manage your data (api)`
4. Save and wait 2–10 minutes for activation.
5. Note down the **Consumer Key** (Client ID) and **Consumer Secret**.

**Salesforce documentation:**
- [Create a Connected App](https://help.salesforce.com/s/articleView?id=sf.connected_app_create.htm)
- [OAuth 2.0 Client Credentials Flow](https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_client_credentials_flow.htm)
- [Connected App Configuration](https://help.salesforce.com/s/articleView?id=sf.connected_app_overview.htm)

### 3. Microsoft Entra ID (Azure AD) App Registration

You need an app registration in Microsoft Entra ID with permissions for the Graph External Connectors API.

**Steps:**

1. Go to [Microsoft Entra admin center → App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**.
   - **Name**: e.g. `Salesforce Graph Connector`
   - **Supported account types**: Single tenant
   - **Redirect URI**: Leave blank (not needed for daemon app)
3. After creation, note the **Application (client) ID** and **Directory (tenant) ID**.
4. Go to **Certificates & secrets → New client secret** — note the secret value.
5. Go to **API permissions → Add a permission → Microsoft Graph → Application permissions** and add:
   - `ExternalConnection.ReadWrite.All`
   - `ExternalItem.ReadWrite.All`
6. Click **Grant admin consent** for your tenant.

**Microsoft documentation:**
- [Register an application in Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
- [Graph Connector permissions](https://learn.microsoft.com/en-us/graph/connecting-external-content-manage-connections#permissions)
- [Microsoft Graph External Connectors overview](https://learn.microsoft.com/en-us/graph/connecting-external-content-connectors-overview)
- [Admin consent for Graph permissions](https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent)

### 4. Azure CLI (optional, for authentication)

If using `DefaultAzureCredential` (recommended for local dev):

```bash
# Install Azure CLI: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli
az login
```

---

## Installation

```bash
# Clone the repository
git clone https://github.com/<your-org>/SaleforceCRMCustomConnector.git
cd SaleforceCRMCustomConnector

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

### 1. Create environment files

```bash
cp env/.env.local.example env/.env.local
```

Edit `env/.env.local` with your values:

```ini
# Connector identity
CONNECTOR_ID=salesforce-crm
CONNECTOR_NAME=Salesforce CRM
CONNECTOR_DESCRIPTION=Salesforce CRM data for Microsoft Search

# Salesforce
SALESFORCE_INSTANCE_URL=https://your-org.my.salesforce.com/
SALESFORCE_API_VERSION=v60.0
SALESFORCE_CLIENT_ID=<your_connected_app_consumer_key>

# Azure AD / Entra
AAD_APP_CLIENT_ID=<your_entra_app_client_id>
AAD_APP_TENANT_ID=<your_entra_tenant_id>
```

Create `env/.env.local.user` for secrets:

```ini
SECRET_SALESFORCE_CLIENT_SECRET=<your_connected_app_consumer_secret>
SECRET_AAD_APP_CLIENT_SECRET=<your_entra_app_client_secret>
```

### 2. Configure Salesforce objects

Edit `config/schema.json` to define which Salesforce objects and fields to sync. Edit `config/graph-schema.json` to control which fields are searchable/queryable/refinable in Microsoft Search.

### 3. Customise search result template

Edit `config/template.json` to change how results appear in Microsoft Search (Adaptive Card format).

---

## Usage

```bash
# Show the complete setup and usage guide
python run.py guide

# Full deployment: create connection → register schema → ingest items
python run.py full-deployment

# Full deployment with detailed console output
python run.py full-deployment --verbose

# Re-ingest items only (connection & schema must already exist)
python run.py ingest

# Debug: ingest a single record by Salesforce ID
python run.py single-item 500f6000008iCNYAA2

# Debug: ingest all records of one object type
python run.py single-object Case
```

### Console Output

In default (non-verbose) mode, the console shows progress milestones and a run summary:

```
Starting full deployment for connector 'salesforce-crm'...
  Graph client initialized
  Connection 'salesforce-crm' verified (existing)
  Schema registered
  Starting ingestion...
  Fetched 150 records from Salesforce
  Resolving ACLs for 6 object type(s)...
  Ingested 50 / 150 items...
  Ingested 100 / 150 items...
  Ingestion complete: 148 succeeded, 2 failed, 0 deleted

============================================================
  RUN SUMMARY — FULL DEPLOYMENT
============================================================
  Connector ID:     salesforce-crm
  Connection:       existing
  Records fetched:  150
  Ingested OK:      148
  Failed:           2
  Time elapsed:     42.3s
  Full log:         logs/deployment_20260411_124225.log
  Summary log:      logs/summary_deployment_20260411_124225.log
============================================================
```

Use `--verbose` to see all INFO-level log messages on the console.

---

## Logging

All runs produce two log files in `logs/`:

| File | Content |
|------|---------|
| `<command>_<timestamp>.log` | Full detail (all INFO+ messages) |
| `summary_<command>_<timestamp>.log` | Run summary with counts, failed IDs, and timing |

If items fail, check the summary log for failed item IDs, then search the full log for error details.

---

## ACL Resolution

The connector supports two ACL engines:

- **Legacy engine** (default) — `graph/legacy_acl_resolver.py`
- **New engine** (opt-in) — `acl_engine/` — Set `USE_NEW_ACL_ENGINE=true` in your env config

Both engines resolve Salesforce's sharing model into Microsoft Graph ACL entries:

1. Check **Org-Wide Defaults** (Public → everyone; Private → record-level ACLs)
2. Resolve **ControlledByParent** via recursive parent traversal
3. For Private objects: fetch **owner** + **share table entries**
4. Expand principals: **users**, **roles**, **territories**, **queues**, **public groups**, **managers**
5. Map Salesforce User IDs to **Azure AD identities**

See [`acl_engine/README.md`](acl_engine/README.md) for detailed flowcharts and sequence diagrams.

---

## Tuning Parameters

Set these in `env/.env.local` to adjust behaviour:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPH_MAX_RETRIES` | 4 | Max retries for Graph API calls |
| `GRAPH_RETRY_BACKOFF_BASE` | 2 | Exponential backoff base (seconds) |
| `CONNECTION_TIMEOUT_SECONDS` | 600 | Max wait time for connection creation |
| `CONNECTION_RETRY_INTERVAL_SECONDS` | 15 | Retry interval for connection checks |
| `SCHEMA_RETRY_INTERVAL_SECONDS` | 15 | Retry interval for schema registration |
| `SALESFORCE_QUERY_LIMIT` | 10 | Max records per SOQL page (increase for production) |
| `SALESFORCE_BATCH_SIZE` | 100 | Batch size for ingestion |
| `ACL_MAX_PARENT_DEPTH` | 5 | Max recursion depth for ControlledByParent ACLs |
| `USE_MOCK_DATA` | false | Use mock data instead of live Salesforce (for testing) |
| `USE_NEW_ACL_ENGINE` | false | Use the new ACL engine instead of legacy |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Contributing

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and add tests
3. Run tests: `pytest tests/ -v`
4. Push and open a pull request

---

## License

This project is provided as-is for demonstration and internal use. See your organisation's licensing guidelines.
