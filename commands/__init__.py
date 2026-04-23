"""
CLI command modules for the Salesforce CRM Custom Connector.

This package contains all subcommand implementations for ``run.py``.
Each module exposes a single ``cmd_*`` function that ``argparse`` dispatches to.

Modules
-------
guide          Print the interactive setup and usage guide.
deploy         Full end-to-end deployment (connection → schema → ingestion).
ingest         Re-ingest items into an existing connection.
ingest_item    Ingest a single Salesforce record by its ID.
ingest_object  Ingest all records of a specific Salesforce object type.

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
from .ingest_item import cmd_ingest_item
from .ingest_object import cmd_ingest_object


# ---------------------------------------------------------------------------
# Logging setup (shared by all commands)
# ---------------------------------------------------------------------------

LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


_console_handlers: list[tuple[logging.Handler, int]] = []


def setup_logging(prefix: str, verbose: bool = False, dashboard_mode: bool = False) -> tuple[Path, Path]:
    """Configure logging: full INFO detail always goes to file.
    Console shows INFO+ when verbose=True, WARNING+ otherwise.
    A 'progress' logger always prints to console for key milestones.
    When *dashboard_mode* is True, console handlers are suppressed so
    that the rich live dashboard can render without interference.
    Returns (log_file, summary_file).
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOGS_DIR / f"{prefix}_{timestamp}.log"
    summary_file = LOGS_DIR / f"summary_{prefix}_{timestamp}.log"
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(fmt))

    console_level = logging.INFO if verbose else logging.WARNING
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.CRITICAL + 1 if dashboard_mode else console_level)
    console_handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Progress logger — always visible on console for key milestones
    progress = logging.getLogger("progress")
    progress.propagate = False  # don't duplicate to root
    progress_console = logging.StreamHandler(sys.stdout)
    progress_console.setLevel(logging.CRITICAL + 1 if dashboard_mode else logging.INFO)
    progress_console.setFormatter(logging.Formatter("%(message)s"))
    progress.addHandler(progress_console)
    progress.addHandler(file_handler)  # also goes to main log file

    # Store handlers so they can be restored after dashboard stops
    _console_handlers.clear()
    _console_handlers.append((console_handler, console_level))
    _console_handlers.append((progress_console, logging.INFO))

    return log_file, summary_file


def restore_console_logging() -> None:
    """Re-enable console handlers after dashboard mode ends."""
    for handler, level in _console_handlers:
        handler.setLevel(level)


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
        lines.append(f"  Failed item IDs (first {len(stats.failed_ids)}):")
        for fid in stats.failed_ids:
            lines.append(f"    - {fid}")
        if stats.failed_count > len(stats.failed_ids):
            lines.append(f"    ... and {stats.failed_count - len(stats.failed_ids)} more")
        lines.append(f"")
        lines.append(f"  >> Failed records: logs/failed_records_{connector_id}.jsonl")
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


def reset_logging() -> None:
    """Remove all handlers from the root and progress loggers.

    Must be called before ``setup_logging`` when running multiple ingestion
    iterations in the same process (e.g. ``--continuous`` mode) so that each
    iteration writes to a fresh log file without duplicating output.
    """
    for logger_name in (None, "progress"):
        lgr = logging.getLogger(logger_name)
        for handler in lgr.handlers[:]:
            handler.close()
            lgr.removeHandler(handler)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser for run.py."""
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Salesforce CRM Custom Connector - Unified CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py guide\n"
            "  python run.py full-deployment\n"
            "  python run.py full-deployment --verbose\n"
            "  python run.py full-deployment --continuous --hours 24\n"
            "  python run.py ingest\n"
            "  python run.py ingest --continuous --hours 12\n"
            "  python run.py ingest-item --id 500f6000008iCNYAA2\n"
            "  python run.py ingest-object --type Case\n"
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
    p_deploy = subparsers.add_parser(
        "full-deployment",
        help="Deploy connection → schema → ingest items with ACLs",
    )
    p_deploy.add_argument(
        "--continuous",
        action="store_true",
        default=False,
        help="Keep running and re-ingest every --hours hours instead of exiting.",
    )
    p_deploy.add_argument(
        "--hours",
        type=int,
        default=12,
        help="Re-ingestion interval in hours when --continuous is set (min 12, max 168). Default: 12.",
    )
    p_deploy.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Force a full sync, ignoring delta timestamps and checkpoints.",
    )
    p_deploy.set_defaults(func=cmd_full_deployment)

    # ingest
    p_ingest = subparsers.add_parser(
        "ingest",
        help="Ingest items only (connection & schema must already exist)",
    )
    p_ingest.add_argument(
        "--continuous",
        action="store_true",
        default=False,
        help="Keep running and re-ingest every --hours hours instead of exiting.",
    )
    p_ingest.add_argument(
        "--hours",
        type=int,
        default=12,
        help="Re-ingestion interval in hours when --continuous is set (min 12, max 168). Default: 12.",
    )
    p_ingest.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Force a full sync, ignoring delta timestamps and checkpoints.",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    # ingest-item
    p_item = subparsers.add_parser(
        "ingest-item",
        help="Ingest a single Salesforce record by its ID",
    )
    p_item.add_argument(
        "--id",
        required=True,
        help="Salesforce record ID (e.g. 500f6000008iCNYAA2)",
    )
    p_item.set_defaults(func=cmd_ingest_item)

    # ingest-object
    p_obj = subparsers.add_parser(
        "ingest-object",
        help="Ingest all records of a specific Salesforce object type",
    )
    p_obj.add_argument(
        "--type",
        required=True,
        help="Salesforce object type (e.g. Case, Account, Opportunity)",
    )
    p_obj.set_defaults(func=cmd_ingest_object)

    return parser
