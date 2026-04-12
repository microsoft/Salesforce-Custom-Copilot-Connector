# Salesforce CRM Custom Connector for Microsoft Graph

A Python-based connector that syncs Salesforce CRM data into **Microsoft Search** via the [Microsoft Graph External Connectors API](https://learn.microsoft.com/en-us/graph/connecting-external-content-connectors-overview). It fetches Salesforce objects (Accounts, Contacts, Leads, Opportunities, Cases, and custom objects), resolves record-level ACLs from Salesforce's sharing model, and ingests them as searchable items with proper access control.

---

## Features+

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

# Full deployment with continuous re-ingestion every 24 hours
python run.py full-deployment --continuous --hours 24

# Re-ingest items only (connection & schema must already exist)
python run.py ingest

# Continuous re-ingestion every 12 hours
python run.py ingest --continuous --hours 12

# Debug: ingest a single record by Salesforce ID
python run.py ingest-item --id 500f6000008iCNYAA2

# Debug: ingest all records of one object type
python run.py ingest-object --type Case
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
| `USE_NEW_ACL_ENGINE` | false | Use the new ACL engine instead of legacy |
| `OWD_OVERRIDES` | *(empty)* | Force OWD values for testing, JSON object e.g. `{"Account":"Private"}` |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Known Limitations

This connector is a **reference implementation / starter project**. It demonstrates how to bridge Salesforce CRM data into Microsoft Search but has several limitations you should be aware of before running it in production.

### Full Sync Only (No Incremental / Delta Sync)

Every run re-fetches and re-ingests **all records** from Salesforce. The codebase has infrastructure for a `since` parameter (the SOQL `WHERE LastModifiedDate >=` clause is already wired in `salesforce/api_client.py`), but all commands currently pass `since=None`. There is no persistent state store to track the last sync timestamp.

**Impact:** For orgs with thousands of records, each run is slow and consumes API quota unnecessarily.

### No Built-In Scheduling or Automation

The connector is a CLI tool (`python run.py …`) that must be triggered manually. There is no timer, cron, Azure Functions wrapper, Dockerfile, or Kubernetes manifest included.

### No Resumability or Checkpointing

If ingestion fails midway (e.g., at record 500 of 1,000), the entire run must be restarted from the beginning. Failed items are logged in the summary file but there is no dead-letter queue or selective retry mechanism.

### Orphaned Items from Hard Deletes

Records soft-deleted in Salesforce (still in the Recycle Bin) are detected via `IsDeleted` and removed from Graph. However, records that are **permanently deleted** (hard-deleted or purged past the 15-day window) will remain in Microsoft Search indefinitely because there is no reconciliation between items currently in Graph and items currently in Salesforce.

### All Records Buffered in Memory

`graph/ingest.py` loads all converted items into a list before processing. For very large orgs this can exhaust available memory.

### Debug Commands Fetch All Objects Before Filtering

The `single-object` and `single-item` commands query **all** object types from Salesforce and then filter to the requested type/ID in memory. This means you will see query warnings and retries for unrelated objects (e.g. Account, Lead, Contact) even when you only asked for `Case`. The filtering should be pushed upstream into `get_all_items_from_api` before using this in production so that only the requested object type is queried from Salesforce.

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

### 1. Enable Incremental (Delta) Sync

The SOQL infrastructure already exists — you just need a state store and a small code change.

1. **Create a persistent state store** — Azure Blob Storage, Table Storage, or a simple file in a mounted volume.
2. **Modify the ingest command** — In `commands/ingest.py` and `commands/deploy.py`, read the last sync timestamp from the store and pass it as the `since` argument:
   ```python
   since = read_last_sync_timestamp()          # from your state store
   stats = ingest_content(config, client, since=since)
   save_last_sync_timestamp(datetime.utcnow()) # persist for next run
   ```
3. **Test with `single-object`** first to verify the `WHERE LastModifiedDate >=` clause returns expected results.

### 2. Add Scheduling

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

### 3. Handle Orphaned / Hard-Deleted Items

Implement a **reconciliation pass** after each ingestion:

1. Fetch the full set of item IDs currently in the Graph connection (via `GET /external/connections/{id}/items`).
2. Compare against the set of IDs returned from Salesforce in the current run.
3. DELETE any Graph items whose IDs are no longer present in Salesforce.

This ensures that hard-deleted records don't remain searchable indefinitely.

### 4. Add Checkpointing and Resumability

- Persist a checkpoint (e.g., last successfully ingested item ID or offset) to your state store after each batch.
- On failure, read the checkpoint and skip already-ingested items.
- Optionally implement a dead-letter queue (Azure Queue Storage or Service Bus) for failed items so they can be retried independently.

### 5. Stream Instead of Buffer

Replace the full-list materialisation in `graph/ingest.py` with a streaming approach:

```python
# Instead of:
raw_items = list(get_all_items_from_api(config, since))

# Process one at a time:
for raw_item in get_all_items_from_api(config, since):
    process_and_ingest(raw_item)
```

This keeps memory usage constant regardless of org size.

### 6. Add New Salesforce Objects

1. Add an entry to the `objectList` array in `config/schema.json` with `objectName`, `selectedFields`, `owdField`, and optional `parentObjectName` / `filterCondition`.
2. Add corresponding property definitions in `config/graph-schema.json`.
3. If the object has a custom sharing model, update `salesforce/sharing_model.py` with the appropriate `ORDERED_OBJECT_NAMES` entry.
4. Test with `python run.py single-object YourObject__c`.

### 7. Integrate Secrets Management

Replace plaintext `.env.local.user` secrets with a secure store:

- **Azure Key Vault** — Use `azure-identity` + `azure-keyvault-secrets` to fetch secrets at startup.
- **Azure Function App Settings** — Reference Key Vault secrets via `@Microsoft.KeyVault(SecretUri=...)`.
- **Managed Identity** — Eliminate client secrets for Graph auth entirely by deploying to an Azure resource with a system-assigned identity.

### 8. Add Observability

- **Application Insights** — Add the `opencensus-ext-azure` or `azure-monitor-opentelemetry` package and instrument the ingestion pipeline for traces, metrics, and exceptions.
- **Structured logging** — Switch to JSON-formatted logs so they can be queried in Log Analytics.
- **Alerts** — Set up Azure Monitor alerts on ingestion failure count or run duration thresholds.

### 9. Multi-Org Deployment

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
A: Depends on record count, Salesforce API limits, and network latency. A typical org with ~1,000 records across 5 objects completes in 1–3 minutes. For 100K+ records, expect 30–60 minutes and consider enabling incremental sync.

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

This project is provided as-is for demonstration and internal use. See your organisation's licensing guidelines.
