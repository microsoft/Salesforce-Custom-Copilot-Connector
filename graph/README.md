# Graph

Handles all interactions with the **Microsoft Graph External Connectors API** — connection lifecycle, schema registration, search settings, and item ingestion.

## Files

| File | Description |
|------|-------------|
| `client.py` | Graph API client with retry/exponential back-off, MSAL token acquisition, long-running operation polling, and pagination. |
| `connection.py` | External connection lifecycle: create/verify, readiness check, search settings configuration, and cleanup (delete). |
| `schema.py` | Schema registration via `PATCH /external/connections/{id}/schema` with idempotent creation and retry logic. |
| `ingest.py` | Main ingestion orchestrator: fetches Salesforce records, resolves ACLs, transforms items, and upserts/deletes via Graph API. Returns `IngestionStats` with success/fail/delete counts. |
| `legacy_acl_resolver.py` | Legacy ACL resolution pipeline (default) handling OWD, ownership, role hierarchy, sharing rules, groups, territories, and parent inheritance. |

## Architecture

```
run.py → commands/deploy.py
           │
           ├─ connection.py   → Create/verify external connection
           ├─ schema.py       → Register connector schema
           ├─ connection.py   → Configure search result template
           └─ ingest.py       → Fetch SF records → Resolve ACLs → Transform → PUT items
                │
                ├─ legacy_acl_resolver.py  (default ACL engine)
                └─ acl_engine/             (new engine, opt-in via USE_NEW_ACL_ENGINE=true)
```

## Key Classes

- **`GraphClient`** (`client.py`) — Authenticated HTTP client for all Graph API calls.
- **`GraphApiError`** (`client.py`) — Exception with `status_code` and parsed error body.
- **`IngestionStats`** (`ingest.py`) — Dataclass tracking ingestion outcomes for summary reporting.
