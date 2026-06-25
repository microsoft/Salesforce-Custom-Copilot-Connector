# Salesforce CRM Custom Connector for Microsoft Graph

A Python-based connector that syncs Salesforce CRM data into **Microsoft Search** via the [Microsoft Graph External Connectors API](https://learn.microsoft.com/en-us/graph/connecting-external-content-connectors-overview). It fetches Salesforce objects (Accounts, Contacts, Leads, Opportunities, Cases, and custom objects), resolves record-level ACLs from Salesforce's sharing model, and ingests them as searchable items with proper access control.

---

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Logging](#logging)
- [ACL Resolution](#acl-resolution)
- [Tuning Parameters](#tuning-parameters)
- [Running Tests](#running-tests)
- [Validated Search Queries](#validated-search-queries)
- [Known Limitations](#known-limitations)
- [Extending for Production Use](#extending-for-production-use)
- [Security Considerations](#security-considerations)
- [FAQ](#faq)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Multi-object sync** — Accounts, Contacts, Leads, Opportunities, Cases, and custom objects (e.g. `Customer_Project__c`)
- **Fine-grained ACL resolution** — Respects Salesforce Org-Wide Defaults, role hierarchies, sharing rules, territories, queues, and public groups
- **Adaptive Card search results** — Configurable result templates for Microsoft Search
- **CLI with multiple commands** — Full deployment, re-ingestion, single-item/object debugging
- **Live dashboard** — Real-time per-object progress, throughput, ETA, and error display (powered by `rich`)
- **Delta (incremental) sync** — Only fetches records changed since the last successful run
- **Checkpointing & resume** — Crash-safe; restarts pick up where the previous run left off
- **Failed records log** — Per-item error details (HTTP status, error code, message) in a JSONL file for inspection and retry
- **Parallel Graph API calls** — Adaptive concurrency for `$batch` pushes (auto-dials down on 429 throttling)
- **Bulk identity resolution** — Batches Salesforce-to-AAD user lookups via Graph `$batch` (20x fewer HTTP calls)
- **Self-healing queries** — Automatically retries Salesforce queries when fields aren't available in the target org

---

## Project Structure

```
├── run.py                  # CLI entry point
├── commands/               # CLI subcommand implementations
├── config/                 # JSON schema, Graph properties, result templates, and sync state
├── dashboard.py            # Live console dashboard (rich-powered)
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
git clone https://github.com/<your-org>/SalesforceCRMCustomConnector.git
cd SalesforceCRMCustomConnector

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
# Local environment configuration

# Built-in environment variables
TEAMSFX_ENV=local
APP_NAME_SUFFIX=local
CONNECTOR_ID=your_connector_id
CONNECTOR_NAME=your_connector_name
CONNECTOR_DESCRIPTION=your_connection_description

# Salesforce Configuration
SALESFORCE_INSTANCE_URL=https://your-org.my.salesforce.com/
SALESFORCE_API_VERSION=v60.0
SALESFORCE_CLIENT_ID=your_salesforce_client_id/consumer_key
# Set SECRET_SALESFORCE_CLIENT_SECRET in env/.env.local.user

# Azure AD App Configuration
AAD_APP_CLIENT_ID=your_entra_app_client_id
AAD_APP_OBJECT_ID=your_entra_app_object_id
AAD_APP_TENANT_ID=your_entra_tenant_id
AAD_APP_OAUTH_AUTHORITY=https://login.microsoftonline.com/your_entra_tenant_id
AAD_APP_OAUTH_AUTHORITY_HOST=https://login.microsoftonline.com
# Set SECRET_AAD_APP_CLIENT_SECRET in env/.env.local.user

# Graph API settings
GRAPH_API_VERSION=v1.0 or beta
GRAPH_MAX_RETRIES=4
GRAPH_RETRY_BACKOFF_BASE=2

# Connection provisioning timeout and retry interval (seconds)
CONNECTION_TIMEOUT_SECONDS=600
CONNECTION_RETRY_INTERVAL_SECONDS=15

# Schema provisioning retry interval (seconds)
SCHEMA_RETRY_INTERVAL_SECONDS=15

# Salesforce SOQL LIMIT clause.
# Set to 0 (recommended for production) to let Salesforce paginate the full result
# set automatically at 2000 records/page via nextRecordsUrl — no artificial cap.
# Set to a small positive number (e.g. 5) only for local dev/debug runs.
SALESFORCE_QUERY_LIMIT=0

# Max IDs in a single SOQL IN clause
SALESFORCE_BATCH_SIZE=100

# Max depth when following ControlledByParent ACL chains
ACL_MAX_PARENT_DEPTH=5

# ── Batching / parallelism ──
# Number of Salesforce records processed per chunk (ACL resolve + Graph $batch cycle).
# Aligning to 2000 = one Salesforce page per cycle. Safe to increase up to ~5000.
INGEST_CHUNK_SIZE=2000

# Number of PUT/DELETE requests per Graph $batch POST.
# Hard-capped at 20 by the Graph API — do not set above 20.
INGEST_GRAPH_BATCH_SIZE=20

# Number of concurrent Graph $batch POST workers (1 = sequential, 4-8 recommended for large orgs).
# Higher values increase throughput but may trigger 429 throttling — start with 2 and tune.
GRAPH_BATCH_WORKERS=4

# ── ACL engine flags ──
# Set to true to use the new ACL engine instead of the legacy resolver
USE_NEW_ACL_ENGINE=true

# Enable group-based ACL (public groups, queues, territories)
USE_GROUP_ACL=true

# Force specific object OWD values for testing (JSON object, e.g. {"Account":"Private"})
# Leave empty or omit to use real Salesforce OWD values
OWD_OVERRIDES=

# Flag to enable retrieving OWD from Entity Definition table
USE_ENTITY_DEFINITION_OWD=true
```

Create `env/.env.local.user` for secrets:

```ini
SECRET_SALESFORCE_CLIENT_SECRET=<your_connected_app_consumer_secret>
SECRET_AAD_APP_CLIENT_SECRET=<your_entra_app_client_secret>
```

### 2. Configure Salesforce objects

The connector uses **two JSON config files** that work together. Both must be kept in sync — a field listed in one but missing from the other will either not be fetched from Salesforce or not be indexed in Microsoft Search.

#### `config/schema.json` — What to fetch from Salesforce

This file defines **which Salesforce objects and fields** to query via SOQL. The converter reads this at runtime to build queries and map Salesforce field names to Graph property names.

**Structure:**

```json
{
  "objectList": [
    {
      "objectName": "Account",
      "owdField": "DefaultAccountAccess",
      "selectedFields": {
        "Id": "Id",
        "Name": "Name",
        "Industry": "Industry",
        "Phone": "Phone",
        "Description": "Description"
      },
      "parentObjectName": null,
      "objectNameAsChild": null,
      "flsFields": [],
      "SfColumnTypes": {
        "Phone": "System.String, mscorlib",
        "NumberOfEmployees": "System.Int32, mscorlib"
      }
    }
  ]
}
```

| Key | Required | Description |
|-----|:--------:|-------------|
| `objectName` | Yes | Salesforce API object name (e.g. `Account`, `Case`, `Customer_Project__c`) |
| `owdField` | Yes | Salesforce Org-Wide Default field name for ACL resolution (e.g. `DefaultAccountAccess`) |
| `selectedFields` | Yes | Map of `"SalesforceFieldName": "GraphPropertyName"`. Only fields listed here are fetched via SOQL and mapped to Graph schema properties. |
| `parentObjectName` | No | For child objects (e.g. Opportunity under Account), the parent object's name. Enables parent-child hierarchy and `ControlledByParent` ACL resolution. |
| `objectNameAsChild` | No | The relationship name used when this object appears as an inline child in the parent's SOQL query (e.g. `Opportunities`). |
| `flsFields` | No | List of field-level security fields. These are always set to `null` in the Graph item (placeholder for FLS enforcement). |
| `SfColumnTypes` | No | Map of Salesforce field names to .NET type names for type coercion. Supported types: `System.Boolean`, `System.Double`, `System.DateTime`, `System.Int32`, `System.Int64`, `System.String`. |

#### `config/graph-schema.json` — What to index in Microsoft Search

This file defines the **Graph external connection schema** — the set of properties registered with Microsoft Search. It controls which fields are searchable, queryable, retrievable, and refinable.

**Structure:**

```json
[
  {
    "name": "Name",
    "type": "String",
    "isSearchable": true,
    "isQueryable": true,
    "isRetrievable": true,
    "isRefinable": false,
    "labels": ["title"]
  },
  {
    "name": "Industry",
    "type": "String",
    "isSearchable": true,
    "isQueryable": true,
    "isRetrievable": true,
    "isRefinable": true,
    "labels": []
  }
]
```

| Key | Description |
|-----|-------------|
| `name` | Must match the **Graph property name** (the right-hand value in `selectedFields`), not the Salesforce field name. |
| `type` | `String`, `Int64`, `Double`, `Boolean`, or `dateTime`. |
| `isSearchable` | Include in full-text search index. Only `String` properties can be searchable. |
| `isQueryable` | Allow filtering via KQL in search queries (e.g. `ObjectName:Account`). |
| `isRetrievable` | Return in search result payloads. |
| `isRefinable` | Allow use as a refiner/facet in search results. |
| `labels` | Microsoft Search semantic labels (e.g. `title`, `url`, `createdDateTime`, `lastModifiedDateTime`, `authors`, `iconUrl`). |

#### How the two files work together

```
schema.json                          graph-schema.json
┌──────────────────────┐             ┌──────────────────────┐
│ selectedFields:      │             │ name: "Industry"     │
│   "Industry":        │─── must ──▶│ type: "String"       │
│     "Industry"       │   match     │ isSearchable: true   │
└──────────────────────┘             └──────────────────────┘
   Salesforce SOQL ←─── fetches        Graph API ←─── registers
```

1. The **right-hand value** in `selectedFields` (e.g. `"Industry"`) must have a matching `name` entry in `graph-schema.json`.
2. If a field is in `schema.json` but **not** in `graph-schema.json`, the value is still fetched from Salesforce but gets pushed into the item's full-text `content` body instead of being a discrete searchable property.
3. If a field is in `graph-schema.json` but **not** in any `selectedFields`, it is registered in the Graph schema but never populated — it will always be empty.

#### Auto-generated (synthetic) properties

The converter automatically generates several properties that **do not come from Salesforce fields**. These must be present in `graph-schema.json` for the connector to work correctly:

| Property | Source | Purpose |
|----------|--------|---------|
| `ObjectName` | Set to the Salesforce object type (e.g. `Account`, `Case`) | Enables filtering by object type in search queries (`ObjectName:Account`). Must have `isQueryable: true` and `isRefinable: true`. Labelled `itemType`. |
| `url` | Constructed as `{SALESFORCE_INSTANCE_URL}/{RecordId}` | Direct link to the Salesforce record. Must be labelled `url`. |
| `IconUrl` | Set from the `iconUrl` value in `schema.json` per object | Icon displayed in search results. Optional — only set if present in the Graph schema. |
| `AccountUrl` | Constructed as `{SALESFORCE_INSTANCE_URL}/{AccountId}` when `AccountId` is present | Link to the parent account. Auto-generated when the item has an `AccountId` property. |
| `Authors` | Derived from `CreatedBy.Name` and `LastModifiedBy.Name` | Deduplicated list of author names. Must be labelled `authors` in the schema. |

Additionally, the converter injects metadata columns (`CreatedDate`, `LastModifiedDate`, `Owner.Name`, `CreatedBy.Name`, `LastModifiedBy.Name`, etc.) from every Salesforce record. These are mapped via the built-in `METADATA_COLUMN_SCHEMA_MAPPING` in `item/converter.py` — you do not need to add them to `selectedFields`, but their Graph property names **must** appear in `graph-schema.json` if you want them indexed.

#### Reserved field names in the converter

The following field names are treated specially during conversion and are **skipped** when building item properties:

| Field | Reason |
|-------|--------|
| `attributes` | Salesforce metadata envelope (contains `type` and `url`). Used internally to detect the object type but never indexed. |
| `Id` | Used as the external item ID (the Graph item key). Also mapped to the `Id` schema property if present in `graph-schema.json`. |
| `IsDeleted` | When `true`, the converter emits a delete operation instead of an upsert. |

#### No code changes needed for most configurations

For the majority of use cases, **no source code changes are required**. The connector is fully config-driven — you only need to:

1. Set up credentials in `env/.env.local` and `env/.env.local.user` (tenant ID, client IDs/secrets for both Graph and Salesforce)
2. Define your Salesforce objects and fields in `config/schema.json`
3. Register the corresponding Graph properties in `config/graph-schema.json`

The converter engine handles field mapping, type coercion, metadata injection, nested relationships, address serialisation, and content assembly automatically from these config files.

#### When code changes ARE needed — Item ingestion path

If you need **custom property transformation** beyond what the schema-driven engine provides (e.g. reformatting values, computing derived fields, combining multiple Salesforce fields into one Graph property), the changes are localised to the item ingestion pipeline. Here is the call chain and the specific files to modify:

```
run.py → commands/deploy.py or commands/ingest.py
  └── graph/ingest.py :: ingest_content()                    ← orchestrator (fetch → ACL → transform → push)
        └── salesforce/item_transformer.py :: transform_record()  ← per-record transform
              └── item/converter.py :: SalesforceConverter.convert()
                    └── SalesforceObjectHandler._build_item_properties_and_content()  ← core field mapping
```

| What you need to change | Where to do it |
|------------------------|----------------|
| **Custom per-property transforms** (reformat a date, truncate a string, combine fields) | `salesforce/item_transformer.py` → `_build_live_item()` — add logic in the property iteration loop after `normalized_value = self._normalize_schema_value(key, value)` |
| **New synthetic/computed properties** (a field that doesn't exist in Salesforce but should appear in Graph) | `salesforce/item_transformer.py` → `_build_live_item()` — add to the `properties` dict before the return statement |
| **Type coercion or field mapping changes** (across all objects) | `item/converter.py` → `SalesforceObjectHandler._build_item_properties_and_content()` — this is where `selectedFields` mappings and `_convert_value()` type coercion happen |
| **New metadata columns** (additional Salesforce system fields to always fetch) | `item/converter.py` → `METADATA_COLUMNS` list and `METADATA_COLUMN_SCHEMA_MAPPING` dict at the top of the file |
| **Principal / PrincipalCollection property with no 1-to-1 Salesforce mapping** | `item/converter.py` → `SalesforceObjectHandler._build_item_properties_and_content()` — explicitly construct the principal dict: `props["MyProperty"] = {"externalName": record.get("MyUser.Name") or "", "externalId": record.get("MyUserId") or ""}`. Ensure both Salesforce fields are in `selectedFields` in `config/schema.json`. The `@odata.type` annotation is injected automatically by `salesforce/item_transformer.py` → `_normalize_schema_value()` as long as `MyProperty` is declared as `Principal` or `PrincipalCollection` in `config/graph-schema.json` — no transformer change needed. |

> **Tip:** For most new Salesforce objects or fields, you should **never** need to touch these files. Only modify them when the standard `selectedFields` → `graph-schema.json` mapping is insufficient for your use case.

### 3. Customise search result template

Edit `config/template.json` to change how results appear in Microsoft Search (Adaptive Card format).

---

## Usage

```bash
# Show the complete setup and usage guide
python run.py guide

# Setup only: create connection → register schema → configure search settings (no ingestion)
python run.py setup-connection

# Setup with detailed console output
python run.py setup-connection --verbose

# Full deployment: create connection → register schema → ingest items
python run.py full-deployment

# Full deployment with detailed console output (no dashboard)
python run.py full-deployment --verbose

# Continuous mode (defaults: full every 24h, incremental every 4h)
python run.py full-deployment --continuous

# Continuous mode with custom schedule
python run.py full-deployment --continuous --full-crawl-hours 48 --incremental-hours 2

# Re-ingest items only (connection & schema must already exist)
python run.py ingest

# Continuous ingestion (defaults: full every 24h, incremental every 4h)
python run.py ingest --continuous

# Preview identity crawl changes without calling Graph APIs
python run.py identity-dry-run --verbose

# Preview and save crawl data to SQLite (no Graph calls)
python run.py identity-dry-run --save --verbose

# Debug: ingest a single record by Salesforce ID
python run.py ingest-item --id 500f6000008iCNYAA2

# Debug: ingest all records of one object type
python run.py ingest-object --type Case

# Ingest failed items only
python run.py retry-failed --file logs\failed_records_VerifyOdata2605.jsonl
```

### After a Crash

Just re-run the same command. The checkpoint file automatically resumes from the last completed chunk:

```bash
# Crashed at Account chunk #50? Re-run — chunks 1-50 are skipped
python run.py ingest
```

### Graceful Stop

Press **Ctrl+X** during ingestion to stop after the current chunk (progress is checkpointed). Press **Ctrl+X** again to exit immediately.

### Console Output

In default mode, a **live dashboard** displays real-time progress (powered by `rich`):

```
+----------------------  Salesforce >> Graph Ingestion  ----------------------+
| Connector: salesforce-crm  |  Mode: Full sync  |  ACL: LEGACY              |
| Log:     logs/ingestion_20260418.log                                        |
| Errors:  logs/failed_records_salesforce-crm.jsonl                           |
+-----------------------------------------------------------------------------+

 Object              Ingested / Total    Failed       ETA   Status
 Account                4,000 / 8,000         1         -   + Done
 Case                   2,000 / 25,000        0      ~13m   > Chunk #4
 Contact                    - / 12,000        -      ~10m   - Pending
 Total                  6,000 / 45,000        1

 ------                                          6,000 / 45,000  (13.3%)

 Elapsed: 5m 24s  |  Rate: 1,111/min  |  ETA: ~35m 08s
 Last error: Account/001ABC -- [Graph] HTTP 400: BadRequest -- value too long

 > [Case] chunk #4 -- Resolving ACLs (500 records)  (45s)  ETA ~2m 10s
 Press Ctrl+X to stop gracefully
```

The dashboard auto-refreshes 4 times per second. Elapsed time, rate, and ETA update in real-time even during long ACL resolution phases.

Use `--verbose` for traditional scrolling log output (disables the dashboard).

After the dashboard, a run summary is printed with totals and file paths.

---

## Logging

All runs produce files in `logs/`:

| File | Content |
|------|---------|
| `<command>_<timestamp>.log` | Full detail (all INFO+ messages) |
| `summary_<command>_<timestamp>.log` | Run summary with counts, failed IDs, and timing |
| `failed_records_<connector_id>.jsonl` | Per-item failure details (item ID, object type, HTTP status, error message, timestamp) |
| `sync_state.json` | Last successful sync timestamp per connector (for delta sync) |
| `checkpoint_<connector_id>.json` | In-progress chunk state for crash recovery (cleared on success) |

The **failed records** file is a JSONL file (one JSON object per line) with full error context:

```json
{"item_id": "001dN00000rKNCtQAO", "object_type": "Account", "error": "[Graph] HTTP 400: BadRequest -- Property 'Website' value cannot exceed 256 characters.", "timestamp": "2026-04-18T13:24:37+00:00"}
```

Each error is prefixed with `[Graph]` or `[Salesforce]` to identify which API failed.

---

## ACL Resolution

The connector supports three ACL resolution modes:

| Mode | Environment Variable | Description |
|------|---------------------|-------------|
| **Legacy** (default) | *(none)* | User-only ACL via `graph/legacy_acl_resolver.py` |
| **New user-only** | `USE_NEW_ACL_ENGINE=true` | Modular user-only ACL via `acl_engine/resolver.py` |
| **Group-based** | `USE_GROUP_ACL=true` | Group-reference ACLs via `acl_engine/group_acl_builder.py` (requires identity crawl) |

### User-only modes (Legacy / New)

Both engines resolve Salesforce's sharing model into Microsoft Graph ACL entries:

1. Check **Org-Wide Defaults** (Public → everyone; Private → record-level ACLs)
2. Resolve **ControlledByParent** via recursive parent traversal
3. For Private objects: fetch **owner** + **share table entries**
4. Expand principals: **users**, **roles**, **territories**, **queues**, **public groups**, **managers**
5. Map Salesforce User IDs to **Azure AD identities**

### Group-based mode

Instead of expanding groups into individual users, produces ACL entries referencing **external groups** created by the Identity Crawl. The identity crawl only runs on **full sync** cycles (not incremental). The SQLite diff engine ensures minimal Graph API calls.

See [`acl_engine/README.md`](acl_engine/README.md) for detailed flowcharts and sequence diagrams.

---

## Tuning Parameters

Set these in `env/.env.local` to adjust behaviour:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPH_API_VERSION` | v1.0 | Microsoft Graph API version |
| `GRAPH_MAX_RETRIES` | 4 | Max retries for Graph API calls |
| `GRAPH_RETRY_BACKOFF_BASE` | 2 | Exponential backoff base (seconds) |
| `GRAPH_CONCURRENT_BATCHES` | 4 | Parallel `$batch` calls to Graph API (auto-dials down on 429 throttling) |
| `CONNECTION_TIMEOUT_SECONDS` | 600 | Max wait time for connection creation |
| `CONNECTION_RETRY_INTERVAL_SECONDS` | 15 | Retry interval for connection checks |
| `SCHEMA_RETRY_INTERVAL_SECONDS` | 15 | Retry interval for schema registration |
| `SALESFORCE_QUERY_LIMIT` | 0 | SOQL LIMIT clause (0 = no limit, Salesforce auto-paginates at 2000/page) |
| `SALESFORCE_BATCH_SIZE` | 100 | Max IDs in a single SOQL IN clause |
| `INGEST_CHUNK_SIZE` | 500 | Records per processing chunk (higher = fewer round trips, more memory) |
| `INGEST_GRAPH_BATCH_SIZE` | 20 | Items per Graph `$batch` POST (max 20, per MS docs) |
| `ACL_MAX_PARENT_DEPTH` | 5 | Max recursion depth for ControlledByParent ACLs |
| `USE_NEW_ACL_ENGINE` | false | Use the new ACL engine instead of legacy |
| `OWD_OVERRIDES` | *(empty)* | Force OWD values for testing, JSON object e.g. `{"Account":"Private"}` |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Validated Search Queries

The following queries have been tested against the Salesforce sample data org and confirmed to return correct results through Microsoft Search via this connector. They cover a range of categories including simple lookups, field retrieval, filtered lists, date-based queries, numeric comparisons, aggregations, cross-object joins, and extended objects.

For the full list of 69 validated queries, see [Queries.md](Queries.md).

**Summary by category:**

| Category | Passing Queries |
|----------|:---------:|
| List | 5 |
| Lookup | 5 |
| Field | 10 |
| Filter | 13 |
| Date | 9 |
| Numeric | 7 |
| Aggregation | 11 |
| Cross-object | 8 |
| Extended object / FeedItem / OpportunityLineItem / Order / Quote | 5 |

**Example queries:**

```
Show me the top 50 open opportunities.
What stage is the Dickenson Mobile Generators opportunity in?
Which opportunities are in the Closed Won stage?
What opportunities are closing in April 2026?
Which opportunities have an amount over $200,000?
What is the total value of all closed won opportunities?
Give me a customer briefing for Grand Hotels — their open deals, cases, and contacts.
```

**Salesforce objects covered:**

| Object | Standard / Custom | Queries |
|--------|:-----------------:|:-------:|
| Account | Standard | 15 |
| Contact | Standard | 10 |
| Opportunity | Standard | 24 |
| Case | Standard | 7 |
| Lead | Standard | 7 |
| Campaign | Standard | 1 |
| FeedItem | Standard | 1 |
| OpportunityLineItem | Standard | 1 |
| Order | Standard | 1 |
| Quote | Standard | 1 |

---

## Known Limitations

This connector is a **reference implementation / starter project**. It demonstrates how to bridge Salesforce CRM data into Microsoft Search but has several limitations you should be aware of before running it in production.

### Orphaned Items from Hard Deletes

Records soft-deleted in Salesforce (still in the Recycle Bin) are detected via `IsDeleted` and removed from Graph. However, records that are **permanently deleted** (hard-deleted or purged past the 15-day window) will remain in Microsoft Search indefinitely because there is no reconciliation between items currently in Graph and items currently in Salesforce.

### No Built-In Scheduling or Automation

The connector is a CLI tool (`python run.py ...`) with `--continuous` mode for simple scheduling. For production, consider Azure Functions with a TimerTrigger, Kubernetes CronJob, or similar.

### Salesforce Objects Are Statically Configured

Only Account, Contact, Lead, Opportunity, and Case are pre-configured in `config/schema.json`. Custom objects are supported (an example `Customer_Project__c` is commented out in the source) but must be manually added to the JSON config — there is no dynamic object discovery.

### Limited Custom Object Depth

- **Polymorphic lookups** (e.g., `WhoId` referencing either Contact or Lead) are not handled.
- **Many-to-many junction objects** are not indexed.
- **Nested child chains** beyond one level are untested.

### Authentication Constraints

| Area | Current State |
|------|--------------|
| Salesforce auth | OAuth 2.0 Client Credentials flow only — no JWT bearer or X.509 certificate support |
| Graph auth | `DefaultAzureCredential` (supports Managed Identity, Azure CLI, etc.) |
| Secrets management | Environment variables via `.env.local.user` — no built-in Key Vault integration |

### Field-Level Security (FLS) Not Enforced at Runtime

`config/schema.json` supports an `flsFields` list per object, but the converter does **not** validate fields against the org's actual FLS settings. All configured fields are indexed regardless of profile-level FLS restrictions.

### Single Salesforce Org per Deployment

The connector is wired to a single `SALESFORCE_INSTANCE_URL`. Multi-org sync requires separate deployments with distinct connector IDs.

### No File / Attachment Sync

Salesforce ContentDocument, ContentVersion, and Attachment objects are not supported. The Graph `content` property only accepts plain text; binary document extraction would require an additional service.

### No Cloud Observability

Logs are written to local files only. There is no integration with Application Insights, Azure Monitor, or structured JSON logging for automated alerting or dashboarding.

### Salesforce API Rate Limits Not Fully Handled

The Graph API side handles 429 responses with exponential backoff, but the Salesforce API client has no throttle-aware retry logic for API quota exhaustion.

### ACL Uses Per-Item Members Instead of Groups

Microsoft Graph External Connectors support ACL resolution via **external groups** — you can create groups, add members to them, and reference the group in item ACLs. This connector does **not** use that approach. Instead, it resolves every principal (user, role, territory, queue, etc.) and attaches individual member entries directly to each item's ACL.

**Why:** Using external groups would require the connector to also **manage the full lifecycle** of those groups — creating, updating membership, and deleting them in sync with the source organisation's structure (role hierarchy changes, queue membership updates, group additions/removals). Correct group management depends on tight, ongoing integration with the organisation's identity and structure data. Since this is a reference implementation without that integration, per-item member ACLs are used to keep the connector self-contained and avoid stale or inconsistent group state in Graph.

**Trade-off:** Per-item ACLs are simpler to implement but result in larger payloads per item and require re-ingesting items when a user's access changes. A group-based approach would centralise access changes to the group membership, reducing re-ingestion scope.

---

## Extending for Production Use

The sections below outline what you need to add or change to make this connector production-ready.

### 1. Add Scheduling

**Azure Functions:**
- Create a `function_app.py` with a `TimerTrigger` that imports and calls the existing command functions.
- Store env vars in Function App Settings (reference Key Vault for secrets).
- Use a cron expression like `0 0 */6 * * *` (every 6 hours).

**Docker + Kubernetes CronJob:**
- Add a `Dockerfile` that installs dependencies and sets `ENTRYPOINT ["python", "run.py", "ingest"]`.
- Deploy as a Kubernetes CronJob with your preferred schedule.

**Azure Container Instances + Logic Apps:**
- Build and push a container image to ACR.
- Use a Logic App with a Recurrence trigger to start the container group on schedule.

### 2. Handle Orphaned / Hard-Deleted Items

Implement a **reconciliation pass** after each ingestion:

1. Fetch the full set of item IDs currently in the Graph connection (via `GET /external/connections/{id}/items`).
2. Compare against the set of IDs returned from Salesforce in the current run.
3. DELETE any Graph items whose IDs are no longer present in Salesforce.

This ensures that hard-deleted records don't remain searchable indefinitely.

### 3. Add New Salesforce Objects

See [Configure Salesforce objects](#2-configure-salesforce-objects) for the full walkthrough on editing `config/schema.json` and `config/graph-schema.json`. After adding the new object, test with `python run.py single-object YourObject__c`.

### 4. Integrate Secrets Management

Replace plaintext `.env.local.user` secrets with a secure store:

- **Azure Key Vault** — Use `azure-identity` + `azure-keyvault-secrets` to fetch secrets at startup.
- **Azure Function App Settings** — Reference Key Vault secrets via `@Microsoft.KeyVault(SecretUri=...)`.
- **Managed Identity** — Eliminate client secrets for Graph auth entirely by deploying to an Azure resource with a system-assigned identity.

### 5. Add Observability

- **Application Insights** — Add the `opencensus-ext-azure` or `azure-monitor-opentelemetry` package and instrument the ingestion pipeline for traces, metrics, and exceptions.
- **Structured logging** — Switch to JSON-formatted logs so they can be queried in Log Analytics.
- **Alerts** — Set up Azure Monitor alerts on ingestion failure count or run duration thresholds.

### 6. Multi-Org Deployment

For organisations with multiple Salesforce instances:

1. Use a distinct `CONNECTOR_ID` per org (e.g., `salesforce-crm-na`, `salesforce-crm-eu`).
2. Deploy separate instances of the connector, each with its own env config pointing to a different `SALESFORCE_INSTANCE_URL`.
3. Each instance creates its own Graph external connection and schema.

---

## Security Considerations

- **Never commit secrets** to source control. Use `.env.local.user` for local development only and a secure vault for production.
- **Rotate credentials regularly.** Salesforce Connected App secrets and Entra client secrets should be rotated on a schedule.
- **Principle of least privilege.** Grant the Salesforce Connected App only the OAuth scopes it needs. For the Entra app, only `ExternalConnection.ReadWrite.All` and `ExternalItem.ReadWrite.All` are required.
- **Audit ACL mappings.** Periodically verify that the identity mapping between Salesforce users and Azure AD accounts is accurate — unmapped users silently lose access to search results.
- **Network restrictions.** If possible, restrict the Salesforce Connected App's IP range and use Azure VNet integration for the compute hosting the connector.

---

## FAQ

**Q: How long does a full sync take?**
A: Depends on record count, ACL complexity, and network latency. A typical org with ~1,000 records across 5 objects completes in 1-3 minutes. For 100K+ records, expect 30-60 minutes. For 1M+, the first full sync may take several hours; subsequent runs use delta sync and complete in minutes. The live dashboard shows per-object ETA throughout the run.

**Q: Can I run this against a Salesforce sandbox?**
A: Yes. Point `SALESFORCE_INSTANCE_URL` to your sandbox URL (e.g., `https://your-org--dev.sandbox.my.salesforce.com/`) and use the sandbox's OAuth credentials.

**Q: Why are some Salesforce users missing from search results?**
A: The ACL engine maps Salesforce users to Azure AD via `FederationIdentifier`, `Username`, or `Email`. If none of these match a user in your tenant, that user's ACLs are silently skipped. Check the log for "unmapped user" warnings.

**Q: Does this support Salesforce Change Data Capture (CDC)?**
A: Not currently. The connector uses polling (SOQL queries). A CDC-based approach using Salesforce Platform Events → Azure Event Hub would require an architectural change but would enable near-real-time sync.

---

## Contributing

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and add tests
3. Run tests: `pytest tests/ -v`
4. Push and open a pull request

---

## License

Copyright (c) Microsoft Corporation. Licensed under the [MIT License](LICENSE.txt).

## Third-party notices

This project includes third-party open-source software. Attribution and license details are in [NOTICE](NOTICE).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.
