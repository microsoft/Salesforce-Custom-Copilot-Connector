# Logs

Runtime-generated log files from connector runs. This directory is created automatically and its contents are **not committed** to source control.

## File Naming

| Pattern | Description |
|---------|-------------|
| `<command>_YYYYMMDD_HHMMSS.log` | Full detail log (all INFO+ messages) for a specific run. |
| `summary_<command>_YYYYMMDD_HHMMSS.log` | Summary log with connection status, ingestion counts, failed item IDs, and timing. |

## Examples

```
deployment_20260411_124225.log         # Full log from a full-deployment run
summary_deployment_20260411_124225.log # Summary of that run
ingestion_20260411_130015.log          # Full log from an ingest-only run
single_item_20260411_131500.log        # Full log from a single-item debug run
```

## Troubleshooting

If ingestion reports failures, check the **summary log** for the list of failed item IDs, then use the full log to find the specific error for each ID.
