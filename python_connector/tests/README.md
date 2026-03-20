# Connector Mock Data And Automated Tests

This test package adds connector-level mock data for all live object types and the permission data needed by ACL resolution.

Included:

- `mock_data/`: dedicated fixture folder with separate record and permission files
- `test_connector_flow.py`: automated tests for transformer coverage, ACL behavior, and ingestion uploads
- `conftest.py`: shared config fixture that mirrors the live connector schema

Fixture layout:

- `mock_data/salesforce_records/`: one file per Salesforce object type
- `mock_data/permissions/`: ACL helpers, org defaults, share rows, and principal samples

Covered object mocks:

- `Account`
- `Lead`
- `Contact`
- `Opportunity`
- `Case`
- `Customer_Project__c`

Each Salesforce object file is capped at 10 generated sample records.

Covered permission and identity mocks:

- org-wide defaults
- private-case share rows
- authorized users
- user roles
- public groups and group members
- frozen-user shape
- Graph GUID-backed ACL entries

Install test dependencies:

```powershell
cd python_connector
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the connector tests:

```powershell
cd python_connector
.venv\Scripts\python.exe -m pytest tests -v
```

Run a single test file:

```powershell
cd python_connector
.venv\Scripts\python.exe -m pytest tests/test_connector_flow.py -v
```

The mock-data folder is intended to be reused when adding more automated tests around `connector/acl.py`, `connector/transform.py`, `connector/ingest.py`, and any future crawl-state or route-level tests.