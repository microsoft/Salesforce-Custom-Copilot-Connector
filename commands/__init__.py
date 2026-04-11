"""
CLI command modules for the Salesforce CRM Custom Connector.

This package contains all subcommand implementations for ``run.py``.
Each module exposes a single ``cmd_*`` function that ``argparse`` dispatches to.

Modules
-------
guide          Print the interactive setup and usage guide.
deploy         Full end-to-end deployment (connection → schema → ingestion).
ingest         Re-ingest items into an existing connection.
single_item    Ingest one Salesforce record by its ID (debugging helper).
single_object  Ingest all records of a specific Salesforce object type.

Shared utilities
----------------
setup_logging(prefix, verbose)
    Configures the root logger with a timestamped file handler (always INFO+),
    a console handler (INFO+ when *verbose* is True, WARNING+ otherwise),
    and a 'progress' logger that always prints to console for key milestones.
    Returns ``(log_file, summary_file)``.

write_summary(summary_file, log_file, stats, connection_status, connector_id, elapsed, command_name)
    Writes a run summary to *summary_file* and prints it to the console via
    the progress logger.

build_parser()
    Constructs the ``argparse.ArgumentParser`` with all subcommands and the
    global ``--verbose`` flag.  Called from ``run.py``.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .guide import cmd_guide
from .deploy import cmd_full_deployment
from .ingest import cmd_ingest
from .single_item import cmd_single_item
from .single_object import cmd_single_object


# ---------------------------------------------------------------------------
# Logging setup (shared by all commands)
# ---------------------------------------------------------------------------

LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(prefix: str, verbose: bool = False) -> tuple[Path, Path]:
    """Configure logging: full INFO detail always goes to file.
    Console shows INFO+ when verbose=True, WARNING+ otherwise.
    A 'progress' logger always prints to console for key milestones.
    Returns (log_file, summary_file).
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOGS_DIR / f"{prefix}_{timestamp}.log"
    summary_file = LOGS_DIR / f"summary_{prefix}_{timestamp}.log"
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO if verbose else logging.WARNING)
    console_handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Progress logger — always visible on console for key milestones
    progress = logging.getLogger("progress")
    progress.propagate = False  # don't duplicate to root
    progress_console = logging.StreamHandler(sys.stdout)
    progress_console.setLevel(logging.INFO)
    progress_console.setFormatter(logging.Formatter("%(message)s"))
    progress.addHandler(progress_console)
    progress.addHandler(file_handler)  # also goes to main log file

    return log_file, summary_file


def write_summary(summary_file, log_file, stats, connection_status, connector_id, elapsed, command_name):
    """Write summary to file and print to console."""
    progress = logging.getLogger("progress")

    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append(f"  RUN SUMMARY — {command_name}")
    lines.append("=" * 60)
    lines.append(f"  Connector ID:     {connector_id}")
    if connection_status:
        lines.append(f"  Connection:       {connection_status}")
    lines.append(f"  Records fetched:  {stats.total_fetched}")
    if stats.object_type_counts:
        for obj_type, count in sorted(stats.object_type_counts.items()):
            lines.append(f"    - {obj_type}: {count}")
    lines.append(f"  ACL engine:       {stats.acl_engine}")
    if stats.acl_fallback_used:
        lines.append(f"  ACL fallback:     YES (public ACLs used due to error)")
    lines.append(f"  Ingested OK:      {stats.success_count}")
    lines.append(f"  Deleted:          {stats.deleted_count}")
    lines.append(f"  Failed:           {stats.failed_count}")
    if stats.failed_ids:
        lines.append(f"  Failed item IDs:")
        for fid in stats.failed_ids:
            lines.append(f"    - {fid}")
        lines.append(f"")
        lines.append(f"  >> To retry failed items, check the summary log:")
        lines.append(f"  >> {summary_file}")
    lines.append(f"  Time elapsed:     {elapsed:.1f}s")
    lines.append(f"  Full log:         {log_file}")
    lines.append(f"  Summary log:      {summary_file}")
    lines.append("=" * 60)

    summary_text = "\n".join(lines)

    # Write to summary file
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(summary_text)

    # Print to console (always visible)
    progress.info(summary_text)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Salesforce CRM Custom Connector - Unified CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py guide\n"
            "  python run.py full-deployment\n"
            "  python run.py full-deployment --verbose\n"
            "  python run.py ingest\n"
            "  python run.py single-item 500f6000008iCNYAA2\n"
            "  python run.py single-object Case\n"
            "  python run.py single-object Account\n"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print all log levels (INFO+) to console. Without this flag only WARNING and ERROR are shown on console; the log file always captures everything.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = False

    # guide
    subparsers.add_parser(
        "guide",
        help="Show the complete setup and usage guide",
    ).set_defaults(func=cmd_guide)

    # full-deployment
    subparsers.add_parser(
        "full-deployment",
        help="Deploy connection → schema → ingest items with ACLs",
    ).set_defaults(func=cmd_full_deployment)

    # ingest
    subparsers.add_parser(
        "ingest",
        help="Ingest items only (connection & schema must already exist)",
    ).set_defaults(func=cmd_ingest)

    # single-item
    p_item = subparsers.add_parser(
        "single-item",
        help="Ingest a single Salesforce record by ID",
    )
    p_item.add_argument("item_id", help="Salesforce record ID (e.g. 500f6000008iCNYAA2)")
    p_item.set_defaults(func=cmd_single_item)

    # single-object
    p_obj = subparsers.add_parser(
        "single-object",
        help="Ingest all records of a specific Salesforce object type",
    )
    p_obj.add_argument(
        "object_type",
        help="Salesforce object type (e.g. Case, Account, Opportunity, Customer_Project__c)",
    )
    p_obj.set_defaults(func=cmd_single_object)

    return parser
