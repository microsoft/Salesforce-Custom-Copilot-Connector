# Salesforce

Handles all **Salesforce REST API** communication, configuration loading, data transformation, and sharing model definitions.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker. |
| `api_client.py` | Salesforce REST API client: SOQL query execution with pagination, self-healing field validation (retries queries after removing unsupported fields), and error handling. |
| `settings.py` | Configuration loading: reads environment variables and JSON config files, validates required fields, builds `AppConfig` with schema metadata. |
| `item_transformer.py` | Transforms Salesforce records into Graph external items with ACL attachment, property conversion, and nested relationship handling. |
| `sharing_model.py` | Salesforce sharing model enums (`EntityVisibility`, `UserOrGroupType`) and constants for OWD field mapping. |
| `utils.py` | Utility functions: datetime normalisation, ISO-Z formatting, UTC conversions, epoch helpers. |

## Key Classes

- **`AppConfig`** (`settings.py`) — Central configuration object passed to all modules. Built by `load_config()`.
- **`SalesforceItemTransformer`** (`item_transformer.py`) — Converts raw API records into Graph-ready payloads with ACLs.

## Self-Healing Queries

`api_client.py` automatically detects when a Salesforce org doesn't support certain standard fields (e.g. `AccountNumber`, `Site`) and retries the query with those fields removed. This is logged as a WARNING — it's expected behaviour, not an error.
