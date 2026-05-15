# Commands

CLI subcommand implementations for the `run.py` unified entry point. Each module exposes a single `cmd_*` function that `argparse` dispatches to.

## Available Commands

| Command | Module | Description |
|---------|--------|-------------|
| `guide` | `guide.py` | Prints the complete setup and usage guide (prerequisites, environment variables, config files, workflow). |
| `setup-connection` | `setup_connection.py` | Create/verify the external connection, register the schema, configure search settings, and wait for the connection to reach `ready` state. Does **not** ingest any content. Useful for initial setup or re-registering an updated schema. |
| `full-deployment` | `deploy.py` | End-to-end deployment: create connection → register schema → configure search → identity crawl (if USE_GROUP_ACL) → ingest items with ACLs. |
| `ingest` | `ingest.py` | Re-ingest items into an existing connection (assumes prior `full-deployment`). |
| `ingest-item` | `ingest_item.py` | Ingest a single Salesforce record by its ID (`--id`). |
| `ingest-object` | `ingest_object.py` | Ingest all records of one Salesforce object type (`--type`). |
| `identity-dry-run` | `identity_dry_run.py` | Preview identity crawl changes without calling Graph APIs. Use `--save` to write to SQLite. |

## Shared Utilities (`__init__.py`)

| Function | Description |
|----------|-------------|
| `setup_logging(prefix, verbose)` | Configures root logger (file: INFO+, console: WARNING+ or INFO+ with `--verbose`), a `"progress"` logger that always prints to console, and a summary log file. Returns `(log_file, summary_file)`. |
| `reset_logging()` | Removes all handlers from root and progress loggers. Called before each iteration in `--continuous` mode so every run gets a fresh log file. |
| `write_summary(...)` | Writes a run summary (connection status, ingestion stats, timing) to both a log file and console. |
| `build_parser()` | Constructs the `argparse.ArgumentParser` with all subcommands and the global `--verbose` flag. |

## Options

### Global

| Flag | Description |
|------|-------------|
| `--verbose` | Print all log levels (INFO+) to console. Without this flag only WARNING+ are shown; the log file always captures everything. |

### Continuous Mode (`full-deployment` and `ingest` only)

| Flag | Default | Description |
|------|---------|-------------|
| `--continuous` | — | Keep running with scheduled full and incremental crawls. |
| `--full-crawl-hours <int>` | 24 | Full crawl interval in hours (min 12, max 168). |
| `--incremental-hours <int>` | 4 | Incremental crawl interval in hours (min 1, max 168). |

Identity crawl only runs on **full** sync cycles (not incremental).

## Usage

```bash
python run.py guide                                                         # Show setup guide
python run.py setup-connection                                              # Create connection + schema (no ingestion)
python run.py setup-connection --verbose                                    # Setup with detailed console output
python run.py full-deployment                                               # Deploy (full sync)
python run.py full-deployment --verbose                                     # Deploy (detailed console)
python run.py full-deployment --continuous                                   # Deploy + continuous (24h full, 4h incremental)
python run.py full-deployment --continuous --full-crawl-hours 48 --incremental-hours 2
python run.py ingest                                                        # Re-ingest only (full sync)
python run.py ingest --continuous                                           # Continuous (24h full, 4h incremental)
python run.py identity-dry-run --verbose                                    # Preview identity changes
python run.py identity-dry-run --save --verbose                             # Preview + save to SQLite
python run.py ingest-item --id 500f6000008iCNYAA2                           # Ingest one record
python run.py ingest-object --type Case                                     # Ingest all Cases
```
