# Item

Transforms raw Salesforce SOQL query results into **Microsoft Graph `externalItem`** JSON payloads ready for ingestion.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package documentation and exports. |
| `models.py` | Data classes for Graph external items: `Content`, `AccessControlEntry`, `SearchableItem`, `DeletedItem`. |
| `converter.py` | Conversion engine: `SalesforceObjectHandler` maps Salesforce fields to Graph properties; `SalesforceConverter` facade; `build_handlers_from_config()` factory. |

## Data Flow

```
Salesforce SOQL Record
  → SalesforceObjectHandler (field mapping per object type)
    → SearchableItem / DeletedItem (structured model)
      → JSON payload for PUT /external/connections/{id}/items/{itemId}
```

## Key Classes

- **`SearchableItem`** — Represents an item to upsert: ID, properties, content, and ACL entries.
- **`DeletedItem`** — Represents an item to remove from the index.
- **`SalesforceObjectHandler`** — Per-object-type field mapper built from `config/schema.json`.
- **`SalesforceConverter`** — Facade wrapping all handlers for easy transform calls.
