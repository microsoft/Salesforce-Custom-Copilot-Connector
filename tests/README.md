# Tests

This directory contains the test suite for the Salesforce CRM Custom Connector.
All tests use [pytest](https://docs.pytest.org/) and require no external services — every dependency is mocked.

## Prerequisites

```bash
pip install -r requirements.txt   # pytest is included
```

## Directory Structure

```
tests/
├── conftest.py                        # Shared fixtures (test_config, tenant_id)
├── test_acl_engine/                   # ACL engine & identity sync
│   ├── test_acl_parent_mapping.py     # Parent-controlled ACL resolution
│   └── test_identity_sync.py          # SOQL response parsing
├── test_commands/                     # CLI commands
│   ├── test_cli_parser.py             # Argument parser & subcommands
│   ├── test_cmd_full_deployment.py    # Full deployment end-to-end
│   ├── test_cmd_guide.py              # Guide output
│   ├── test_cmd_ingest.py             # Ingest command
│   ├── test_cmd_single_item.py        # Single-item debug command
│   └── test_cmd_single_object.py      # Single-object debug command
├── test_graph/                        # Microsoft Graph API layer
│   ├── test_graph_client.py           # HTTP client, retries, pagination
│   ├── test_graph_connection.py       # Connection lifecycle (CRUD)
│   ├── test_graph_ingest.py           # Ingestion pipeline & ACL fallback
│   └── test_graph_schema.py           # Schema provisioning
├── test_item/                         # Item conversion
│   └── test_item_converter.py         # Salesforce → Graph item mapping
└── test_salesforce/                   # Salesforce integration
    ├── test_salesforce.py             # API client, field retry logic
    ├── test_salesforce_item_transformer.py  # Record transformation & ACL
    └── test_settings.py               # Config loading from env files
```

## Running Tests

### Run the entire suite

```bash
python -m pytest tests/
```

### Run tests for a specific module

```bash
python -m pytest tests/test_graph/
python -m pytest tests/test_commands/
python -m pytest tests/test_salesforce/
python -m pytest tests/test_acl_engine/
python -m pytest tests/test_item/
```

### Run a single test file

```bash
python -m pytest tests/test_graph/test_graph_client.py
```

### Run a single test by name

```bash
python -m pytest tests/test_graph/test_graph_client.py::test_retries_on_retryable_status_codes
```

### Run tests matching a keyword

```bash
python -m pytest -k "acl"           # all tests with "acl" in the name
python -m pytest -k "retry"         # all retry-related tests
```

## Useful Flags

| Flag | Purpose |
|------|---------|
| `-v` | Verbose — show each test name and result |
| `-q` | Quiet — show only pass/fail summary |
| `-x` | Stop on first failure |
| `--tb=short` | Shorter tracebacks |
| `--tb=long` | Full tracebacks |
| `-s` | Disable output capture (print statements visible) |
| `--lf` | Re-run only tests that failed last time |
| `--pdb` | Drop into the Python debugger on failure |

## Debugging Workflows

### Investigate a failing test

Run with full output and stop on first failure:

```bash
python -m pytest tests/test_graph/test_graph_ingest.py -x -v --tb=long -s
```

### Drop into the debugger on failure

```bash
python -m pytest tests/test_graph/test_graph_ingest.py --pdb
```

This opens an interactive `pdb` session at the exact point of failure. You can
inspect variables, step through code, and evaluate expressions.

### Set a breakpoint in code

Add this line anywhere in source or test code:

```python
breakpoint()   # or: import pdb; pdb.set_trace()
```

Then run with `-s` to disable output capture:

```bash
python -m pytest tests/test_graph/test_graph_ingest.py -s
```

### Debug a single Salesforce object or item

The connector supports two environment variables for narrowing scope during
development:

```bash
# Ingest only a specific record
DEBUG_ITEM_ID=001000000000001AAA python run.py single-item --item-id 001000000000001AAA

# Ingest only a specific object type
DEBUG_OBJECT_TYPE=Account python run.py single-object --object-type Account
```

The corresponding tests (`test_cmd_single_item.py`, `test_cmd_single_object.py`)
verify that these variables are wired correctly.

### Check test coverage

```bash
pip install pytest-cov
python -m pytest tests/ --cov=. --cov-report=term-missing
```

## Shared Fixtures (conftest.py)

The root `conftest.py` provides fixtures available to all tests:

| Fixture | Description |
|---------|-------------|
| `test_config` | A fully-populated `AppConfig` with safe dummy values |
| `tenant_id` | The AAD tenant ID from `test_config` |

Use them by adding the fixture name as a test parameter:

```python
def test_example(test_config):
    assert test_config.connector.connector_id == "test-connector"
```

## Writing New Tests

1. Place the file in the appropriate `test_*/` subdirectory.
2. Name it `test_<module>.py` so pytest auto-discovers it.
3. Use the shared `test_config` fixture for any test that needs configuration.
4. Mock all external I/O (HTTP calls, file system, environment variables).
5. Run your new test in isolation first, then the full suite:

```bash
python -m pytest tests/test_graph/test_my_new_module.py -v
python -m pytest tests/
```
