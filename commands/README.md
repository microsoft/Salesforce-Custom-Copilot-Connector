# Commands

CLI subcommand implementations for the `run.py` unified entry point. Each module exposes a single `cmd_*` function that `argparse` dispatches to.

## Available Commands

| Command | Module | Description |
|---------|--------|-------------|
| `guide` | `guide.py` | Prints the complete setup and usage guide (prerequisites, environment variables, config files, workflow). |
| `full-deployment` | `deploy.py` | End-to-end deployment: create connection → register schema → configure search → ingest items with ACLs. |
| `ingest` | `ingest.py` | Re-ingest items into an existing connection (assumes prior `full-deployment`). |
| `ingest-item` | `ingest_item.py` | Ingest a single Salesforce record by its ID (`--id`). |
| `ingest-object` | `ingest_object.py` | Ingest all records of one Salesforce object type (`--type`). |

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

| Flag | Description |
|------|-------------|
| `--continuous` | Keep running and re-ingest on a schedule instead of exiting after one run. |
| `--hours <int>` | Re-ingestion interval in hours (min 12, max 168). Default: 12. Only used with `--continuous`. |

## Usage

```bash
python run.py guide                                        # Show setup guide
python run.py full-deployment                              # Deploy (quiet console)
python run.py full-deployment --verbose                    # Deploy (detailed console)
python run.py full-deployment --continuous --hours 24       # Deploy + re-ingest every 24h
python run.py ingest                                       # Re-ingest only
python run.py ingest --continuous --hours 12                # Re-ingest every 12h
python run.py ingest-item --id 500f6000008iCNYAA2               # Ingest one record
python run.py ingest-object --type Case                          # Ingest all Cases
```
