"""
full-deployment command — complete end-to-end connector setup.

Performs the following steps in order:

1. Load configuration from environment variables and config files.
2. Initialise the Microsoft Graph API client.
3. Create or verify the external connection.
4. Register the Graph connector schema.
5. Configure search display settings (result type / adaptive card).
6. Wait for the connection to reach the ``ready`` state.
7. (If USE_GROUP_ACL) Run identity crawl to create/update external groups.
8. Ingest Salesforce items with ACL resolution.

Continuous mode
---------------
When ``--continuous`` is passed, the first run performs a full deployment.
Subsequent iterations alternate between **full** and **incremental** content
crawls on independent schedules:

* ``--full-crawl-hours N``  — full crawl every N hours (default 24, min 12).
* ``--incremental-hours N`` — incremental crawl every N hours (default 4, min 1).

**Identity crawl** always runs as full (no incremental for groups).

**Incremental content crawl** only fetches Salesforce records modified since
the last successful content crawl (``LastModifiedDate >= since``).

Usage::

    python run.py full-deployment
    python run.py full-deployment --verbose
    python run.py full-deployment --continuous
    python run.py full-deployment --continuous --full-crawl-hours 24 --incremental-hours 4

Returns ``True`` on success, ``False`` on failure (exit code 1).
"""
import logging
import time
from datetime import datetime

from graph.connection import ensure_connection, is_connection_ready, set_search_settings
from graph.client import GraphClient
from graph.ingest import ingest_content, IngestionStats
from graph.identity import run_identity_sync, record_content_crawl, get_last_content_crawl_time
from graph.schema import ensure_schema
from salesforce.settings import load_config


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _run_full_deployment(args, *, since: datetime | None = None, sync_type: str = "full") -> bool:
    """Execute a single deployment run.

    Parameters
    ----------
    args      : Parsed CLI arguments.
    since     : If set, only fetch Salesforce records modified after this time
                (incremental content crawl).  ``None`` means full crawl.
    sync_type : ``"full"`` or ``"incremental"`` — recorded in the sync session
                and controls which steps are executed.

    Returns ``True`` on success, ``False`` on failure.
    """
    from commands import setup_logging, write_summary

    prefix = "deployment" if sync_type == "full" else "incremental"
    log_file, summary_file = setup_logging(prefix, verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("deployment")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()
    connection_status = None
    stats = None
    config = None

    try:
        config = load_config()
        progress.info(
            "Starting %s deployment for connector '%s'...",
            sync_type, config.connector.id,
        )

        # ── Init Graph client (always needed) ─────────────────────────────
        client = GraphClient(
            api_version=config.tuning.graph_api_version,
            max_retries=config.tuning.graph_max_retries,
            retry_backoff_base=config.tuning.graph_retry_backoff_base,
        )
        progress.info("  Graph client initialized")

        # ── Connection / schema setup (full crawl only) ───────────────────
        if sync_type == "full":
            initial_timestamp = time.monotonic()
            connection_status = ensure_connection(config, client, initial_timestamp)
            if connection_status is None:
                logger.error("Failed to create/ensure connection")
                return False

            ensure_schema(config, client)
            progress.info("  Schema registered")

            set_search_settings(config, client)
            progress.info("  Search settings configured")

            if not is_connection_ready(config, client):
                time.sleep(5)
                if not is_connection_ready(config, client):
                    logger.error("Connection still not ready")
                    return False
            progress.info("  Connection ready")
        else:
            # Incremental: just verify connection is still ready
            connection_status = "existing (incremental)"
            if not is_connection_ready(config, client):
                logger.error("Connection not ready for incremental crawl")
                return False
            progress.info("  Connection verified")

        # ── Identity Crawl (group-based ACL only, always full) ────────────
        if config.use_group_acl:
            progress.info("  Running identity sync (group-based ACL)...")
            identity_stats = run_identity_sync(config, client)
            logger.info(
                "Identity sync: created=%d updated=%d deleted=%d unchanged=%d",
                identity_stats.groups_created, identity_stats.groups_updated,
                identity_stats.groups_deleted, identity_stats.groups_unchanged,
            )

        # ── Content Ingestion ─────────────────────────────────────────────
        if since:
            progress.info("  Starting incremental ingestion (since %s)...", since.isoformat())
        else:
            progress.info("  Starting full ingestion...")

        stats = ingest_content(config, client, since=since)
        logger.info("Ingestion completed (%s)", sync_type)

        try:
            record_content_crawl(config, stats, sync_type=sync_type)
        except Exception as rec_err:
            logger.warning("Could not record content crawl stats: %s", rec_err)

        elapsed = time.monotonic() - start_time
        label = f"FULL DEPLOYMENT ({sync_type.upper()})"
        write_summary(summary_file, log_file, stats, connection_status, config.connector.id, elapsed, label)
        return stats.failed_count == 0

    except Exception as e:
        elapsed = time.monotonic() - start_time
        if stats is None:
            stats = IngestionStats()
        write_summary(summary_file, log_file, stats, connection_status,
                     getattr(config, 'connector', None) and config.connector.id or 'unknown',
                     elapsed, "FULL DEPLOYMENT (CRASHED)")
        logging.getLogger("deployment").exception("Fatal error during deployment: %s", e)
        return False


def cmd_full_deployment(args) -> bool:
    """Deploy connection → schema → ingest items with ACLs.

    When ``--continuous`` is passed, the first iteration performs a full
    deployment.  Subsequent iterations alternate between full and incremental
    content crawls on independent schedules:

    * ``--full-crawl-hours N``  — full content crawl every N hours (default 24).
    * ``--incremental-hours N`` — incremental content crawl every N hours (default 4).

    Identity crawl always runs as full (no incremental for groups).

    Incremental content crawl reads the last successful content crawl timestamp
    from the SQLite DB and passes it as ``since`` to ``ingest_content()``,
    causing Salesforce queries to include ``WHERE LastModifiedDate >= <since>``.
    """
    # First run: always full
    success = _run_full_deployment(args, since=None, sync_type="full")

    continuous = getattr(args, "continuous", False)
    if not continuous:
        return success

    from commands import reset_logging

    full_hours = _clamp(getattr(args, "full_crawl_hours", 24), 12, 168)
    incr_hours = _clamp(getattr(args, "incremental_hours", 4), 1, 168)
    incr_interval = incr_hours * 3600
    full_interval = full_hours * 3600

    progress = logging.getLogger("progress")
    progress.info(
        "\n🔁 Continuous mode enabled:\n"
        "   Full crawl every %d hour(s)\n"
        "   Incremental crawl every %d hour(s)\n"
        "   Press Ctrl+C to stop.\n",
        full_hours, incr_hours,
    )

    last_full_time = time.monotonic()

    while True:
        progress.info("⏳ Next incremental crawl in %d hour(s)...", incr_hours)
        time.sleep(incr_interval)

        reset_logging()

        # Check if it's time for a full crawl
        elapsed_since_full = time.monotonic() - last_full_time
        if elapsed_since_full >= full_interval:
            progress.info("🔄 Starting scheduled FULL crawl...")
            _run_full_deployment(args, since=None, sync_type="full")
            last_full_time = time.monotonic()
        else:
            # Incremental: read last content crawl time from DB
            progress.info("🔄 Starting scheduled INCREMENTAL crawl...")
            try:
                config = load_config()
                since = get_last_content_crawl_time(config)
            except Exception:
                since = None
            _run_full_deployment(args, since=since, sync_type="incremental")
