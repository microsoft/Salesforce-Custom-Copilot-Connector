"""
Salesforce CRM Custom Connector - Unified CLI

All operations are available as subcommands of this single entry point.

Usage:
    python run.py <command> [--verbose]

Commands:
    guide                    Show the complete setup and usage guide
    full-deployment          Deploy connection → schema → ingest items with ACLs
    ingest                   Ingest items only (connection & schema must already exist)
    ingest-item              Ingest a single Salesforce record by its ID
    ingest-object            Ingest all records of a specific Salesforce object type

Global options:
    --verbose                Print all log levels (INFO, WARNING, ERROR) to console.
                             Without this flag only WARNING and ERROR are shown on
                             console; the log file always captures everything.

Continuous mode (full-deployment and ingest only):
    --continuous             Keep running and re-ingest on a schedule instead of exiting.
    --hours <int>            Re-ingestion interval in hours (min 12, max 168). Default: 12.
    --full                   Force a full sync, ignoring delta timestamps and checkpoints.

Examples:
    python run.py guide
    python run.py full-deployment
    python run.py full-deployment --verbose
    python run.py full-deployment --continuous --hours 24
    python run.py ingest
    python run.py ingest --continuous --hours 12
    python run.py ingest-item --id 500f6000008iCNYAA2
    python run.py ingest-object --type Case
    python run.py ingest-object --type Account
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from commands import build_parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    result = args.func(args)
    if isinstance(result, bool):
        sys.exit(0 if result else 1)
