# Config

Static JSON configuration files that define how Salesforce data maps to Microsoft Graph external connector schema and search results.

> **Note:** All loading logic lives in `salesforce/settings.py`. This folder contains data files only.

## Files

| File | Description |
|------|-------------|
| `schema.json` | Defines which Salesforce objects (Account, Lead, Contact, Opportunity, Case, Customer_Project__c) and fields to sync, along with OWD configuration per object. |
| `graph-schema.json` | Microsoft Graph schema properties — marks each field as searchable, queryable, retrievable, and/or refinable with optional display labels. |
| `template.json` | Adaptive Card JSON template used by Microsoft Search to render connector results in the search experience. |

## Customisation

To add a new Salesforce object or field:

1. Add the object/field mapping in `schema.json`.
2. Add corresponding Graph property definitions in `graph-schema.json`.
3. Update `template.json` if the new field should appear in search results.
4. Re-run `python run.py full-deployment` to push the updated schema.
