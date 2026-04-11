# Commands

CLI subcommand implementations for the `run.py` unified entry point. Each module exposes a single `cmd_*` function that `argparse` dispatches to.

## Available Commands

| Command | Module | Description |
|---------|--------|-------------|
| `guide` | `guide.py` | Prints the complete setup and usage guide (prerequisites, environment variables, config files, workflow). |
| `full-deployment` | `deploy.py` | End-to-end deployment: create connection → register schema → configure search → ingest items with ACLs. |
| `ingest` | `ingest.py` | Re-ingest items into an existing connection (assumes prior `full-deployment`). |
| `single-item <id>` | `single_item.py` | Ingest a single Salesforce record by ID — useful for debugging. |
| `single-object <type>` | `single_object.py` | Ingest all records of one Salesforce object type (e.g. `Case`, `Account`). |

## Shared Utilities (`__init__.py`)

| Function | Description |
|----------|-------------|
| `setup_logging(prefix, verbose)` | Configures root logger (file: INFO+, console: WARNING+ or INFO+ with `--verbose`), a `"progress"` logger that always prints to console, and a summary log file. Returns `(log_file, summary_file)`. |
| `write_summary(...)` | Writes a run summary (connection status, ingestion stats, timing) to both a log file and console. |
| `build_parser()` | Constructs the `argparse.ArgumentParser` with all subcommands and the global `--verbose` flag. |

## Usage

```bash
python run.py guide                          # Show setup guide
python run.py full-deployment                # Deploy (quiet console)
python run.py full-deployment --verbose      # Deploy (detailed console)
python run.py ingest                         # Re-ingest only
python run.py single-item 500f6000008iCNYAA2 # Debug one record
python run.py single-object Case             # Ingest all Cases
```
