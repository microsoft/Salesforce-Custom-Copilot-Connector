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

  Config files (already provided — edit only if you need to customise):

    config/schema.json        Defines which Salesforce objects and fields to sync.
    config/graph-schema.json  Microsoft Graph external connection schema definition.
    config/template.json      Adaptive Card template for search result rendering.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — COMMANDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python run.py full-deployment
      Full end-to-end setup: creates the Graph connection, registers the schema,
      configures search settings, and ingests all Salesforce items with ACLs.
      Run this the first time or to reset the connector completely.

  python run.py ingest
      Re-ingests all items. Requires the connection and schema to already exist
      (i.e. full-deployment must have been run at least once).

  python run.py single-item <item_id>
      Ingests a single Salesforce record by its ID. Useful for debugging.
      Example: python run.py single-item 500f6000008iCNYAA2

  python run.py single-object <object_type>
      Ingests all records of one Salesforce object type.
      Example: python run.py single-object Case
      Example: python run.py single-object Account

  python run.py guide
      Show this guide.

  Global flag:
      --verbose   Print INFO-level logs to the console as well as to the log
                  file. Without this flag, only WARNING and ERROR messages are
                  shown on the console; the log file always captures everything.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — TYPICAL WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1.  Configure env/.env.local and env/.env.local.user.
  2.  python run.py full-deployment          # first-time setup
  3.  python run.py ingest                   # subsequent syncs
  4.  python run.py ingest --verbose         # if you need to see detailed output

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Timestamped log files are written to the logs/ directory automatically.
  Example: logs/deployment_20240501_143022.log

"""


def cmd_guide(_args) -> None:
    """Print the complete setup and usage guide."""
    import sys, io
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    out.write(_GUIDE)
    out.flush()
