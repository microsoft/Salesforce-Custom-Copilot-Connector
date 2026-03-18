# salesforce_converter

A Python package that converts raw Salesforce SOQL API responses into Microsoft Graph Connector ingestion-ready items. It mirrors the conversion logic from the C# `SalesforceObjectHandler.cs` to ensure parity between the .NET and Python connector implementations.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Package Structure](#package-structure)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
  - [SalesforceConverter](#salesforceconverter)
  - [SalesforceObjectHandler](#salesforceobjecthandler)
  - [Models](#models)
  - [ID Helpers](#id-helpers)
- [Configuration](#configuration)
  - [SalesforceConfiguration.json](#salesforceconfigurationjson)
  - [Object Config Schema](#object-config-schema)
  - [Parent-Child Relationships](#parent-child-relationships)
  - [Typed Fields (SfColumnTypes)](#typed-fields-sfcolumntypes)
- [Conversion Pipeline](#conversion-pipeline)
  - [Property Mapping Priority](#property-mapping-priority)
  - [Address Serialization](#address-serialization)
  - [ID Truncation and Hashing](#id-truncation-and-hashing)
  - [Metadata & System Properties](#metadata--system-properties)
  - [Authors Derivation](#authors-derivation)
  - [Deleted Records](#deleted-records)
- [Output Format](#output-format)
- [Testing](#testing)
- [Running the Demo](#running-the-demo)

---

## Overview

```
┌─────────────────────┐         ┌──────────────────────┐         ┌──────────────────────────┐
│  Salesforce SOQL     │         │  salesforce_converter │         │  Graph Connector API     │
│  API Response        │────────▶│  (this package)       │────────▶│  PUT /items/{itemId}     │
│  (raw JSON)          │         │                       │         │  (ingestion-ready dicts) │
└─────────────────────┘         └──────────────────────┘         └──────────────────────────┘
```

The package takes a raw Salesforce query response and produces a list of dictionaries, each representing either a **searchable item** or a **deleted item**, formatted for the Microsoft Graph External Connector ingestion API.

**Key capabilities:**
- Automatic object type inference from `attributes.type` in the Salesforce response
- Parent-child relationship handling (e.g., Account → Contacts)
- Address object serialization
- Type-safe field conversion (Boolean, Double, DateTime, Int, String)
- ID truncation (15-char Salesforce Record ID) and SHA-512 hashing
- Metadata extraction (Owner, CreatedBy, LastModifiedBy, dates)
- Authors list derivation and deduplication
- FLS (Field-Level Security) restricted field nullification
- Schema property filtering

---

## Architecture

```
                        ┌─────────────────────────┐
                        │   SalesforceConverter    │  ◀── Facade (entry point)
                        │   converter.py           │
                        └────────────┬────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │ build_handlers_from  │
                          │ _config()            │  ◀── Config wiring
                          │ config.py            │
                          └──────────┬──────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │    SalesforceObjectHandler       │  ◀── Core conversion logic
                    │    handler.py                    │
                    │                                  │
                    │  ┌──────────────────────────┐   │
                    │  │ _build_item_properties    │   │
                    │  │ _and_content()            │   │
                    │  │                           │   │
                    │  │ Priority 1: selectedFields│   │
                    │  │ Priority 2: metadata cols │   │
                    │  │ Priority 3: object fields │   │
                    │  └──────────────────────────┘   │
                    └──┬──────────────┬───────────────┘
                       │              │
              ┌────────▼───┐   ┌──────▼──────┐
              │ models.py  │   │ id_helper.py│
              │            │   │             │
              │ Searchable │   │ Truncate    │
              │ Item       │   │ (15 chars)  │
              │ DeletedItem│   │ SHA-512     │
              │ Content    │   │ Hash        │
              └────────────┘   └─────────────┘
```

---

## Package Structure

```
salesforce_converter/
├── __init__.py          # Package exports
├── __main__.py          # CLI demo entry point
├── converter.py         # SalesforceConverter facade class
├── config.py            # Config loader & handler factory
├── handler.py           # SalesforceObjectHandler (core logic)
├── constants.py         # Constants mirroring SalesforceConstants.cs
├── models.py            # SearchableItem, DeletedItem, Content
├── id_helper.py         # ID truncation & SHA-512 hashing
├── README.md            # This file
└── tests/
    ├── __init__.py
    ├── conftest.py      # Path setup for pytest
    └── test_salesforce_item_converter.py  # 113 tests
```

| File | Mirrors (C#) | Purpose |
|---|---|---|
| `handler.py` | `SalesforceObjectHandler.cs` | Core field mapping, property building, item construction |
| `id_helper.py` | `ItemIdConstructionHelper.cs` + `IdGenerator.cs` | 15-char ID truncation, SHA-512 hashing |
| `constants.py` | `SalesforceConstants.cs` | Metadata columns, type converters, property names |
| `models.py` | `SearchableItem`, `DeletedItem`, `Content` | Output data classes |
| `config.py` | Configuration loading | JSON config → handler instances with parent-child wiring |
| `converter.py` | *(new)* | Simplified facade for callers |

---

## Quick Start

### Minimal usage — two lines of code

```python
from salesforce_converter import SalesforceConverter

# 1. Create converter (auto-loads SalesforceConfiguration.json)
converter = SalesforceConverter("https://your-org.salesforce.com")

# 2. Convert a Salesforce SOQL response
items = converter.convert(sf_query_result)

# Each item is ready for Graph connector ingestion
for item in items:
    if item["type"] == "deleted":
        graph_client.delete_item(item["id"])
    else:
        graph_client.put_item(item["id"], item)
```

### What the caller provides

| Param | When | Required | Description |
|---|---|---|---|
| `instance_url` | Init (once) | **Yes** | Salesforce org URL, e.g., `https://ap15.salesforce.com` |
| `sf_query_result` | Per call | **Yes** | Raw Salesforce SOQL JSON response with `"records"` list |

Everything else is **auto-inferred**:
- **Object name** — read from `records[0]["attributes"]["type"]`
- **Config** — loaded from `SalesforceConfiguration.json`
- **Schema properties** — derived from config + metadata constants

---

## API Reference

### SalesforceConverter

The primary entry point. Defined in `converter.py`.

```python
class SalesforceConverter:
    def __init__(
        self,
        instance_url: str,
        config: dict | None = None,            # Override: custom config dict
        schema_properties: set[str] | None = None,  # Override: limit emitted properties
        icon_url: str = "",                     # CDN icon URL for all objects
    )

    def convert(
        self,
        sf_query_result: dict,                  # Raw Salesforce SOQL response
        object_name: str | None = None,         # Override: skip auto-inference
    ) -> list[dict]

    # Properties
    object_names: list[str]          # All registered object names
    parent_object_names: list[str]   # Only top-level (non-child) objects
    schema_properties: set[str]      # Active schema property set
```

### SalesforceObjectHandler

Lower-level handler, one per Salesforce object type. Used internally by `SalesforceConverter`. Defined in `handler.py`.

```python
handler.construct_ingestion_items(
    sf_query_result: dict,       # Salesforce response with "records"
    instance_url: str,           # e.g., "https://ap15.salesforce.com"
    schema_properties: set[str], # Property names to emit
) -> list[dict]
```

### Models

Defined in `models.py`:

| Class | Description |
|---|---|
| `SearchableItem` | Represents an active record to be indexed. Has `id`, `properties`, `content`, `shouldHashId`. |
| `DeletedItem` | Represents a record marked for deletion. Has `id` only. |
| `Content` | Holds the `parsedData` string (typically the `Description` field). |

### ID Helpers

Defined in `id_helper.py`:

| Function | Description |
|---|---|
| `construct_item_id_without_hashing(id)` | Truncates Salesforce 18-char ID to 15 characters |
| `construct_item_id_with_hashing(id)` | Truncates + SHA-512 hash → 128-char uppercase hex |
| `generate_alphanumeric_128char_hash(id)` | SHA-512 of UTF-16LE bytes → 128-char hex string |

---

## Configuration

### SalesforceConfiguration.json

The default config file is located at `python_connector/SalesforceConfiguration.json`. It is auto-loaded by `SalesforceConverter` when no explicit config is passed.

### Object Config Schema

Each entry in `objectList` defines one Salesforce object:

```json
{
  "objectName": "Account",
  "selectedFields": {
    "SalesforceFieldName": "SchemaPropertyName",
    "Account.Id": "AccountId",
    "Account.Owner.Name": "AccountOwner"
  },
  "SfColumnTypes": {
    "Amount": "System.Double, mscorlib, Version=4.0.0.0, ..."
  },
  "filterCondition": "",
  "iconUrl": "",
  "flsFields": ["Industry", "Website"],
  "parentObjectName": "Account",
  "objectNameAsChild": "Contacts"
}
```

| Key | Required | Description |
|---|---|---|
| `objectName` | Yes | Salesforce object API name (e.g., `Account`, `Contact`) |
| `selectedFields` | Yes | Map of Salesforce field → schema property name. Supports dot-notation for nested objects (e.g., `Account.Owner.Name`) |
| `SfColumnTypes` | No | Map of field name → .NET assembly-qualified type name for type-safe conversion |
| `filterCondition` | No | SOQL WHERE clause filter |
| `iconUrl` | No | Fallback icon URL |
| `flsFields` | No | Fields to null out (FLS-restricted) |
| `parentObjectName` | No | Links this object as a child of another object |
| `objectNameAsChild` | No | Key in the parent's SOQL response containing child records |

### Parent-Child Relationships

Child objects are defined by setting `parentObjectName` and `objectNameAsChild`:

```json
{
  "objectName": "Contact",
  "parentObjectName": "Account",
  "objectNameAsChild": "Contacts",
  ...
}
```

When `SalesforceConverter` processes an Account record, it checks for a `"Contacts"` key in the response and processes those child records with the Contact handler. **Children are emitted before their parent** in the output list.

```
Input:  Account record with 2 Contacts
Output: [Contact1, Contact2, Account]
```

### Typed Fields (SfColumnTypes)

Fields can be typed using .NET assembly-qualified type names:

| .NET Type | Python Conversion |
|---|---|
| `System.Boolean` | `bool` |
| `System.Double` | `float` |
| `System.DateTime` | `str` (passed through) |
| `System.Int32` | `int` |
| `System.Int64` | `int` |
| `System.String` | `str` |

If conversion fails, a safe default is used (`False`, `0.0`, `0`, `""`) instead of raising an error.

---

## Conversion Pipeline

For each record, the handler executes this pipeline:

```
Record
  │
  ├─ IsDeleted? ──Yes──▶ DeletedItem(truncated_id)
  │
  No
  │
  ├─ Set hardcoded props: ObjectName, Url, IconUrl
  │
  ├─ Iterate record fields:
  │   ├─ Priority 1: selectedFields match → map field to property
  │   ├─ Priority 2: metadata column match → map via METADATA_COLUMN_SCHEMA_MAPPING
  │   └─ Priority 3: nested object → traverse dot-notation keys
  │
  ├─ Null out FLS fields
  ├─ Derive AccountUrl from AccountId
  ├─ Build Authors list (deduplicated)
  ├─ Set system properties (__System.User.CreatedBy.Id, etc.)
  │
  └─▶ SearchableItem(truncated_id, properties, content)
```

### Property Mapping Priority

When iterating over record fields, the handler uses this priority order:

1. **selectedFields** — If the field key is in the config's `selectedFields` map, use the mapped property name
2. **Metadata columns** — If the field matches `METADATA_COLUMN_SCHEMA_MAPPING` (e.g., `CreatedDate`, `OwnerId`)
3. **Object fields** — If the value is a dict, traverse nested keys:
   - 3a: Config-defined object fields (e.g., `Account.Owner.Name`)
   - 3b: Metadata object columns (e.g., `CreatedBy.Name`)

### Address Serialization

Address objects (dicts with a `"street"` key) are serialized into a single string:

```
Input:  {"street": "345 Shoreline Park", "city": "Mountain View", "state": "CA", "postalCode": "94043", "country": "US"}
Output: "345 Shoreline Park, Mountain View, CA - 94043, US"
```

Parts are omitted if `None`. The format mirrors `SalesforceObjectHandler.SerializeAddressObject` in C#.

### ID Truncation and Hashing

Salesforce IDs are 18 characters (case-insensitive), but the connector uses 15-character IDs:

```
Input:  "0012v00002RkkJnAAJ" (18 chars)
Output: "0012v00002RkkJn"    (15 chars, truncated)
```

For hashing (used when `shouldHashId` is `true`), the truncated ID is hashed using SHA-512 with UTF-16LE encoding, producing a 128-character uppercase hex string.

### Metadata & System Properties

These fields are automatically extracted from every record:

| Salesforce Field | Output Property | Notes |
|---|---|---|
| `Id` | `Id` | Raw Salesforce ID |
| `CreatedDate` | `CreatedDate` | Datetime string |
| `LastModifiedDate` | `LastModifiedDate` | Datetime string |
| `CreatedBy.Name` | `CreatedBy` | Display name |
| `CreatedById` | `CreatedByUrl` | Transformed to `{instance_url}/{id}` |
| `LastModifiedBy.Name` | `LastModifiedBy` | Display name |
| `LastModifiedById` | `LastModifiedByUrl` | Transformed to `{instance_url}/{id}` |
| `Owner.Name` | `Owner` | Display name |
| `OwnerId` | `OwnerUrl` | Transformed to `{instance_url}/{id}` |
| `CreatedById` | `__System.User.CreatedBy.Id` | System property |
| `LastModifiedById` | `__System.User.ModifiedBy.Id` | System property |

### Authors Derivation

The `Authors` property is a deduplicated list built from `CreatedBy` and `LastModifiedBy`:

```python
# If both are the same person:
{"Authors": ["Alice"]}

# If different:
{"Authors": ["Alice", "Bob"]}
```

### Deleted Records

Records with `"IsDeleted": true` produce a minimal output:

```json
{"id": "0012v00002RkkJn", "type": "deleted"}
```

No properties or content are extracted for deleted records.

---

## Output Format

### Searchable Item

```json
{
  "id": "0012v00002RkkJn",
  "shouldHashId": true,
  "properties": {
    "ObjectName": "Account",
    "Url": "https://ap15.salesforce.com/0012v00002RkkJnAAJ",
    "Name": "GenePoint",
    "Description": "Genomics company",
    "BillingAddress": "345 Shoreline Park, Mountain View, CA - 94043, US",
    "Owner": "Rohit Sharma",
    "Authors": ["John Doe", "Rohit Sharma"],
    "CreatedDate": "2019-06-14T17:35:22.000+0000",
    ...
  },
  "content": {
    "parsedData": "Genomics company"
  },
  "type": "searchable"
}
```

### Deleted Item

```json
{
  "id": "0012v00002RkkJn",
  "type": "deleted"
}
```

---

## Testing

The package includes **113 comprehensive tests** covering all modules.

### Run tests via pytest (recommended)

```bash
cd python_connector
python -m pytest salesforce_converter/tests/ -v
```

### Run tests directly

```bash
python salesforce_converter/tests/test_salesforce_item_converter.py
```

### Test categories

| Category | Tests | Coverage |
|---|---|---|
| ID Construction | 6 | Truncation, validation, hashing |
| ID Generator (SHA-512) | 4 | Determinism, UTF-16LE, length |
| Type Conversion | 10 | Bool, float, int, str, None, edge cases |
| Address Serialization | 6 | Full, partial, empty addresses |
| Authors | 6 | Dedup, single, missing, schema filtering |
| Account Property Mapping | 8 | ObjectName, Url, content, metadata |
| Deleted Records | 1 | Deleted flag → DeletedItem |
| Missing ID | 2 | Null ID skipped, empty records |
| Typed Fields | 2 | Double, Boolean with SfColumnTypes |
| Object-Type Fields | 1 | Nested traversal (Account.Owner.Name) |
| Parent-Child | 10 | Single/multi children, multi parents, mixed batches |
| FLS Fields | 1 | Nullified fields |
| System Properties | 1 | __System.User.* mapping |
| Schema Filtering | 1 | Only schema properties emitted |
| Config Builder | 4 | Handler wiring, children, orphans |
| Models | 5 | Content, SearchableItem, DeletedItem |
| _resolve_type | 9 | All .NET types + edge cases |
| Converter Facade | 17 | Init, inference, defaults, error handling |
| Other edge cases | 19 | IconUrl, content, URLs, hashing, batches |

---

## Running the Demo

```bash
cd python_connector

# As a module
python -m salesforce_converter

# Or directly
python salesforce_converter/__main__.py
```

Output:
```
Total items: 3
[
  {
    "id": "003AAAAAAAAAAAA",
    "shouldHashId": true,
    "properties": {
      "ObjectName": "Contact",
      "Name": "Edna Frank",
      "Email": "edna@genepoint.com",
      ...
    },
    "type": "searchable"
  },
  {
    "id": "0012v00002RkkJn",
    "properties": {
      "ObjectName": "Account",
      "Name": "GenePoint",
      ...
    },
    "type": "searchable"
  },
  ...
]
```

---

## C# Parity Reference

This package mirrors the following C# classes:

| Python Module | C# Class |
|---|---|
| `handler.py` | `SalesforceObjectHandler.cs` |
| `id_helper.py` | `ItemIdConstructionHelper.cs`, `IdGenerator.cs` |
| `constants.py` | `SalesforceConstants.cs` |
| `models.py` | `SearchableItem`, `DeletedItem`, `Content` (Framework) |
| `config.py` | Configuration loading logic |

All conversion rules — field priority, address formatting, ID truncation, SHA-512 hashing (UTF-16LE), type conversion, metadata mapping, authors deduplication — match the C# implementation.
