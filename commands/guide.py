# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
guide command — prints the complete setup and usage guide.

Running ``python run.py guide`` displays a formatted reference covering:

* Prerequisites (Python version, pip packages, Salesforce & Azure AD setup)
* All required and optional environment variables with examples
* Configuration files (schema.json, graph-schema.json, template.json)
* Available CLI commands and their usage
* Typical first-time and ongoing workflow
* Log file location

No configuration or credentials are required — the guide is purely informational.
"""

_GUIDE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║          Salesforce CRM Custom Connector — Setup & Usage Guide              ║
╚══════════════════════════════════════════════════════════════════════════════╝

OVERVIEW
────────
This connector syncs Salesforce CRM data (Leads, Accounts, Contacts,
Opportunities, Cases, Customer Projects) into Microsoft Search / Microsoft 365
via the Microsoft Graph Connector API.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — PREREQUISITES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  • Python 3.9+
  • pip install -r requirements.txt
  • A Salesforce org with a Connected App (OAuth 2.0 client credentials flow)
  • An Azure AD app registration with the following Graph API permissions:
      - ExternalConnection.ReadWrite.OwnedBy  (application)
      - ExternalItem.ReadWrite.OwnedBy        (application)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — CONFIGURATION FILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Copy the example env file and fill in your values:

    cp env/.env.local.example env/.env.local
    cp env/.env.local.example env/.env.local.user   # for secrets only

  Required environment variables (env/.env.local):

    CONNECTOR_ID              Unique alphanumeric ID, 3–32 chars, no spaces.
                              Must not start with reserved Microsoft prefixes.
                              Example: SalesforceCRM

    CONNECTOR_NAME            Human-readable display name shown in the admin UI.
                              Example: Salesforce CRM

    CONNECTOR_DESCRIPTION     Short description of the connector (plain text).

    SALESFORCE_INSTANCE_URL   Your Salesforce org URL.
                              Example: https://your-org.salesforce.com/

    SALESFORCE_API_VERSION    Salesforce REST API version.
                              Example: v48.0

    SALESFORCE_CLIENT_ID      Client ID of your Salesforce Connected App.

  Secret variables (env/.env.local.user — keep out of source control):

    SECRET_SALESFORCE_CLIENT_SECRET   Client secret of the Connected App.
    SECRET_AAD_APP_CLIENT_SECRET      Azure AD app client secret value
                                      (not the secret ID).

  Azure AD variables (env/.env.local):

    AAD_APP_CLIENT_ID         Azure AD application (client) ID.
    AAD_APP_TENANT_ID         Azure AD tenant ID.

  Optional tuning variables (defaults shown):

    GRAPH_API_VERSION                  v1.0
    GRAPH_MAX_RETRIES                  4
    GRAPH_RETRY_BACKOFF_BASE           2
    CONNECTION_TIMEOUT_SECONDS         600
    CONNECTION_RETRY_INTERVAL_SECONDS  15
    SCHEMA_RETRY_INTERVAL_SECONDS      15
    SALESFORCE_QUERY_LIMIT             10
    SALESFORCE_BATCH_SIZE              100
    ACL_MAX_PARENT_DEPTH               5

  ACL mode variables (set at most one to true):

    USE_NEW_ACL_ENGINE                 false   New user-only ACL engine (modular rewrite)
    USE_GROUP_ACL                      false   Group-based ACL with identity crawl
                                               (requires identity crawl before content)

  OWD override (for testing):

    OWD_OVERRIDES                      {}      JSON, e.g. {"Account":"Private"}

  Config files (already provided — edit only if you need to customise):

    config/schema.json        Defines which Salesforce objects and fields to sync.
    config/graph-schema.json  Microsoft Graph external connection schema definition.
    config/template.json      Adaptive Card template for search result rendering.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — COMMANDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python run.py full-deployment
      Full end-to-end setup: creates the Graph connection, registers the schema,
      configures search settings, runs identity crawl (if USE_GROUP_ACL=true),
      and ingests all Salesforce items with ACLs.

  python run.py full-deployment --continuous
      After the initial full deployment, enters continuous mode.
      Runs incremental content crawls every 4 hours and full content crawls
      every 24 hours (identity crawl always runs as full).
      Defaults can be overridden:
        --full-crawl-hours N      Full crawl interval (default 24, min 12)
        --incremental-hours N     Incremental crawl interval (default 4, min 1)
      Example:
        python run.py full-deployment --continuous --full-crawl-hours 48 --incremental-hours 6

  python run.py ingest
      Re-ingests all items. Requires full-deployment to have been run first.

  python run.py ingest --continuous
      Enters continuous mode with full + incremental content crawl scheduling.
      Same --full-crawl-hours and --incremental-hours options as full-deployment.

  python run.py identity-dry-run
      Preview identity crawl changes without calling Graph APIs.
      Shows which groups would be created, updated, or deleted.
      Use --save to write crawl data to SQLite without calling Graph.

  python run.py ingest-item --id <salesforce_id>
      Ingests a single Salesforce record by its ID. Useful for debugging.

  python run.py ingest-object --type <object_type>
      Ingests all records of one Salesforce object type.

  python run.py guide
      Show this guide.

  Global flag:
      --verbose   Print INFO-level logs to the console as well as to the log
                  file. Without this flag, only WARNING and ERROR messages are
                  shown on the console; the log file always captures everything.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — TYPICAL WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  First-time setup:
    1.  Configure env/.env.local and env/.env.local.user.
    2.  python run.py full-deployment          # creates everything + full ingest

  Subsequent syncs:
    3.  python run.py ingest                   # full re-ingest
    4.  python run.py ingest --verbose         # with detailed console output

  Continuous operation:
    5.  python run.py full-deployment --continuous
        # Runs forever: full crawl every 24h, incremental every 4h.

  Preview group-based ACL:
    6.  python run.py --verbose identity-dry-run
    7.  python run.py --verbose identity-dry-run --save   # writes to SQLite

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOGS & DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Log files:
    Timestamped log files are written to logs/ automatically.

  SQLite state database:
    Located at data/{CONNECTOR_ID}_identity.db (auto-created on first run).
    Tracks identity crawl group membership and content crawl history.

    Tables:
      groups          — External groups published to Graph.
      group_members   — Members of each group (user or nested group).
      sync_sessions   — Audit log of every crawl run (crawl_type, sync_type,
                        identity stats, content stats, timestamps).

"""


def cmd_guide(_args) -> None:
    """Print the complete setup and usage guide."""
    import sys, io
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    out.write(_GUIDE)
    out.flush()
