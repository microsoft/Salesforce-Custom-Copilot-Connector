# Python Salesforce CRM Custom Connector

Azure Functions application that creates and maintains a Salesforce CRM external connection in Microsoft Graph, with support for ACL-based security and mock data testing.

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

## Automated Mock Tests

Use the connector-level pytest suite when you want repeatable offline coverage for object transformation, ACL resolution, and ingestion uploads without calling live Salesforce or Microsoft Graph.

For real Graph calls with mocked Salesforce sources, use the mock-mode flow documented in [Mock Data Testing](#mock-data-testing) below.

Install the test dependencies:

```powershell
cd python_connector
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the connector mock-data tests:

```powershell
cd python_connector
.venv\Scripts\python.exe -m pytest tests/test_connector_flow.py -v
```

The reusable fixture and mock-data helpers live under [tests/mock_data](tests/mock_data):

- [tests/mock_data/salesforce_records](tests/mock_data/salesforce_records): separate files for `Account`, `Lead`, `Contact`, `Opportunity`, `Case`, and `Customer_Project__c`, with up to 10 samples per object type
- [tests/mock_data/permissions](tests/mock_data/permissions): org defaults, share rows, users, groups, roles, and ACL helpers attached to the sample records
- [tests/test_connector_flow.py](tests/test_connector_flow.py): focused regression tests for the live `connector/*` path
- [tests/README.md](tests/README.md): quick reference for extending the suite

## Operational Notes

- The committed runtime template is [local.settings.example.json](local.settings.example.json)
- Crawl state is stored in [../tmp/lastCrawl.json](../tmp/lastCrawl.json)
- Deployment templates live in [../infra/azure.bicep](../infra/azure.bicep), [../infra/azure.parameters.json](../infra/azure.parameters.json), [../m365agents.local.yml](../m365agents.local.yml), and [../m365agents.yml](../m365agents.yml)

---

## Mock Data Testing

Test the complete connector flow without a live Salesforce connection using mock data mode.

### Quick Start: Enable Mock Mode

1. **Set the flag** in `env/.env.local`:
   ```bash
   USE_MOCK_DATA=true
   ```

2. **Configure Azure AD credentials** (required even in mock mode):
   ```bash
   # Azure AD App - REQUIRED for Graph API
   AAD_APP_CLIENT_ID=your_client_id
   AAD_APP_TENANT_ID=your_tenant_id
   SECRET_AAD_APP_CLIENT_SECRET=your_client_secret
   
   # Connector Info - REQUIRED
   CONNECTOR_ID=SFCRMDemoConnector
   CONNECTOR_NAME=Salesforce-CRM-Mock
   CONNECTOR_DESCRIPTION=Salesforce connector with mock data
   
   # Salesforce - CAN BE DUMMY VALUES in mock mode
   SALESFORCE_INSTANCE_URL=https://mock-org.salesforce.com
   SALESFORCE_CLIENT_ID=mock_client_id
   SECRET_SALESFORCE_CLIENT_SECRET=mock_secret
   ```

3. **Run the flow**:
   ```powershell
   # Full deployment with mock data
   python run_full_deployment.py
   
   # Or just ingestion (skip connection/schema)
   python run_ingestion_only.py
   
   # Verify ACL wiring
   python tests/test_acl_wiring.py
   ```

### What Gets Mocked

| Component | Mock Mode | Real Mode | Auth Required? |
|-----------|-----------|-----------|----------------|
| Salesforce Records | ✅ Mock | ❌ Real API | Mock: No / Real: Yes |
| Identity/Permissions | ✅ Mock | ❌ Real API | Mock: No / Real: Yes |
| Graph Connection | ❌ Real API | ❌ Real API | **Always Yes** |
| Graph Schema | ❌ Real API | ❌ Real API | **Always Yes** |
| Graph Ingestion | ❌ Real API | ❌ Real API | **Always Yes** |

**Important:** Mock mode only mocks Salesforce data sources. All Microsoft Graph API operations are real and require Azure AD authentication.

### Mock Data Files

- **[tests/mock_salesforce_client.py](tests/mock_salesforce_client.py)** - Replaces Salesforce API
- **[tests/mock_identity_sync_client.py](tests/mock_identity_sync_client.py)** - Replaces identity/permissions API
- **[tests/mock_data/](tests/mock_data/)** - Pre-defined records, users, groups, shares

Mock records include:
- 10 Accounts
- 10 Contacts  
- 10 Leads
- 10 Opportunities
- 10 Cases
- 5 Customer Projects

### Retrieve Ingested Items

After ingestion, verify items in Graph:

```powershell
# Get a specific item
python get_item.py 006000000000002

# This will show:
# - Item ID and properties
# - ACL entries with GUIDs
# - Content (searchable text)
```

---

## Access Control Lists (ACLs)

Items are ingested with ACLs based on Salesforce sharing rules and organizational defaults.

### ACL Structure

Each item includes an `acl` array with entries like:

```json
{
  "id": "006000000000002",
  "properties": { "Name": "Acme Renewal", ... },
  "acl": [
    {
      "type": "user",
      "value": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      "accessType": "grant"
    }
  ]
}
```

**Note:** ACL `value` must be:
- **Azure AD GUID** when using Azure Active Directory identity
- **Email address** when using external identity source

### ACL Building Logic

The [connector/acl.py](connector/acl.py) builds ACLs based on:

1. **Organization-wide Defaults** - Base visibility (Public, Private, Controlled by Parent)
2. **Record Ownership** - Owner always gets access
3. **Sharing Rules** - Explicit shares from UserShare, AccountShare, etc.
4. **Role Hierarchy** - Manager access based on roles

### Identity Mapping

When `aad_identity_mapping_enabled=True`:
- Uses `FederationIdentifier` (Azure AD GUID) from user records
- Example: `"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"`

When `external_identity_mapping_enabled=True`:
- Uses `Email` from user records
- Example: `"user@example.com"`

### Testing ACLs

1. **Verify ACL wiring**:
   ```powershell
   python tests/test_acl_wiring.py
   ```

2. **Check ingested item ACLs**:
   ```powershell
   python get_item.py <ITEM_ID>
   ```

3. **Review ingestion logs** for ACL statistics:
   ```
   ACL Statistics:
     Items with ACLs: 20/20
     Total ACL entries: 45
     Average per item: 2.25
   ```

---

## Project Structure

### Core Modules

- **[connector/](connector/)** - Main connector logic
  - [ingest.py](connector/ingest.py) - Routes to mock/real flow based on `USE_MOCK_DATA`
  - [graph.py](connector/graph.py) - Graph API client with authentication
  - [settings.py](connector/settings.py) - Configuration loading and validation
  - [acl.py](connector/acl.py) - ACL resolution using AclResolver
  - [item_converter.py](connector/item_converter.py) - Salesforce-to-Graph transformation
  - [transform.py](connector/transform.py) - Item transformation orchestration

- **[tests/](tests/)** - Mock data and tests
  - [mock_salesforce_client.py](tests/mock_salesforce_client.py) - Mock Salesforce API
  - [mock_identity_sync_client.py](tests/mock_identity_sync_client.py) - Mock identity API
  - [mock_data/](tests/mock_data/) - Pre-defined test data

### Scripts

- **[run_full_deployment.py](run_full_deployment.py)** - Complete flow: connection → schema → ingestion
- **[run_ingestion_only.py](run_ingestion_only.py)** - Ingestion only (assumes connection exists)
- **[get_item.py](get_item.py)** - Retrieve and display ingested items from Graph
- **[test_flow.py](test_flow.py)** - Legacy test script (use pytest instead)

---

## Development

### Adding New Salesforce Objects

1. Add field mappings to [SalesforceConfiguration.json](SalesforceConfiguration.json)
2. Add handler config in [connector/references/schema.json](connector/references/schema.json)
3. Add mock records to [tests/mock_data/salesforce_records/](tests/mock_data/salesforce_records/)
4. Update schema in [connector/references/graph-schema.json](connector/references/graph-schema.json)

### Modifying ACL Logic

1. Edit [connector/acl.py](connector/acl.py)
2. Update tests in [tests/](tests/)
3. Verify with `pytest tests/test_mock_acl_flow.py -v`

### Testing Changes

```powershell
# Unit tests (connector modules)
pytest tests/ -v

# Integration tests (with mock data)
pytest tests/test_connector_flow.py -v
python tests/test_acl_wiring.py

# End-to-end (requires Azure AD credentials)
python run_full_deployment.py
```
