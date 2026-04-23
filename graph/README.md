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
| `identity.py` | Top-level orchestrator for identity crawl + publish pipeline. Provides `run_identity_sync()`, `record_content_crawl()`, and `get_last_content_crawl_time()`. |
| `identity_store.py` | SQLite-backed state store for identity crawl group membership, content crawl stats, and sync session history. Computes diffs to minimize Graph API calls. |
| `identity_publisher.py` | Publishes identity crawl results to Microsoft Graph. Uses SQLite diff to only create/update/delete changed groups and members. |

## Architecture

```
run.py → commands/deploy.py
           │
           ├─ connection.py          → Create/verify external connection
           ├─ schema.py              → Register connector schema
           ├─ connection.py          → Configure search result template
           ├─ identity.py            → Identity crawl (when USE_GROUP_ACL=true)
           │    ├─ identity_store.py → SQLite diff (minimize Graph calls)
           │    └─ identity_publisher.py → PUT/POST/DELETE groups + members
           └─ ingest.py              → Fetch SF records → Resolve ACLs → Transform → PUT items
                │
                ├─ legacy_acl_resolver.py  (default ACL engine)
                ├─ acl_engine/             (new user-only engine, USE_NEW_ACL_ENGINE=true)
                └─ acl_engine/group_acl_builder.py  (group ACL, USE_GROUP_ACL=true)
```

## Key Classes

- **`GraphClient`** (`client.py`) — Authenticated HTTP client for all Graph API calls.
- **`GraphApiError`** (`client.py`) — Exception with `status_code` and parsed error body.
- **`IngestionStats`** (`ingest.py`) — Dataclass tracking ingestion outcomes for summary reporting.
