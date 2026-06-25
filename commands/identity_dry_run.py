# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
identity-dry-run command — preview identity crawl changes without calling Graph.

Crawls Salesforce for group membership, diffs against the local SQLite store,
and prints a report of what would be created, updated, deleted, or left
unchanged.  No Microsoft Graph API calls are made.

Usage::

    python run.py identity-dry-run
    python run.py identity-dry-run --verbose
    python run.py identity-dry-run --save          # also writes to SQLite DB
    python run.py identity-dry-run --save --verbose
"""
from __future__ import annotations

import logging
import time

from salesforce.settings import load_config


def cmd_identity_dry_run(args) -> bool:
    """Run identity crawl against Salesforce and show what Graph calls would be made."""
    from commands import setup_logging

    log_file, _ = setup_logging("identity_dry_run", verbose=getattr(args, "verbose", False))
    logger = logging.getLogger("identity_dry_run")
    progress = logging.getLogger("progress")
    start_time = time.monotonic()

    try:
        config = load_config()
        progress.info("Identity Dry Run for connector '%s'", config.connector.id)
        progress.info("=" * 60)

        # ── Step 1: Crawl Salesforce ──────────────────────────────────────────
        from acl_engine.identity_sync import IdentitySyncHandler
        from acl_engine.salesforce_client import SalesforceClient
        from salesforce.api_client import get_salesforce_access_token

        sf_client = SalesforceClient(
            instance_url=config.connector.salesforce.instance_url,
            api_version=config.connector.salesforce.api_version,
            access_token=get_salesforce_access_token(config),
            token_refresher=lambda: get_salesforce_access_token(config),
        )

        object_names = [
            obj["objectName"]
            for obj in config.schema_config.get("objectList", [])
            if obj.get("objectName")
        ]
        progress.info("  Objects: %s", ", ".join(object_names))

        handler = IdentitySyncHandler(
            sf_client=sf_client,
            object_names=object_names,
            parent_map=config.parent_map,
            owd_overrides=config.owd_overrides,
            owd_field_map=config.owd_field_map,
        )

        progress.info("  Crawling Salesforce...")
        crawl_result = handler.run_full_crawl()
        progress.info(
            "  Crawl complete: %d group(s), %d user membership(s)",
            crawl_result.total_groups_emitted,
            crawl_result.total_users_emitted,
        )

        # ── Step 2: Flatten to member sets (resolve SF users → AAD GUIDs) ─────
        import asyncio
        from graph.client import GraphClient
        from graph.identity_publisher import IdentityPublisher
        from acl_engine.principal_mapper import PrincipalMapper

        graph_client = GraphClient()
        principal_mapper = PrincipalMapper(
            sf_client=sf_client,
            graph_client=graph_client,
            tenant_id=config.tenant_id,
            batch_size=config.tuning.salesforce_batch_size,
        )
        publisher = IdentityPublisher(
            graph_client=graph_client,
            connection_id=config.connector.id,
            principal_mapper=principal_mapper,
        )
        flat = asyncio.run(publisher._flatten_crawl_result_async(crawl_result))

        # ── Step 3: Diff against SQLite store ─────────────────────────────────
        from graph.identity_store import create_store

        store = create_store(config.connector.id)
        stored_stats = store.get_stats()
        progress.info(
            "  SQLite store: %d group(s), %d member(s) from previous run",
            stored_stats["groups"],
            stored_stats["members"],
        )

        diffs = store.compute_diff(flat)

        # ── Step 4: Print report ──────────────────────────────────────────────
        creates = [d for d in diffs if d.action == "create"]
        updates = [d for d in diffs if d.action == "update"]
        deletes = [d for d in diffs if d.action == "delete"]
        unchanged = [d for d in diffs if d.action == "unchanged"]
        total_api_calls = sum(d.api_calls_needed for d in diffs)

        progress.info("")
        progress.info("=" * 60)
        progress.info("  IDENTITY DRY RUN REPORT")
        progress.info("=" * 60)
        progress.info("  Groups to CREATE:    %d", len(creates))
        progress.info("  Groups to UPDATE:    %d", len(updates))
        progress.info("  Groups to DELETE:    %d", len(deletes))
        progress.info("  Groups UNCHANGED:    %d", len(unchanged))
        progress.info("  Est. API calls:      %d", total_api_calls)
        progress.info("-" * 60)

        if creates:
            progress.info("")
            progress.info("  NEW GROUPS:")
            for d in creates:
                progress.info("    + %s  (%d members)", d.group_id, len(d.members_to_add))

        if updates:
            progress.info("")
            progress.info("  UPDATED GROUPS:")
            for d in updates:
                progress.info(
                    "    ~ %s  (+%d members, -%d members)",
                    d.group_id,
                    len(d.members_to_add),
                    len(d.members_to_remove),
                )
                for m in d.members_to_add[:5]:
                    progress.info("        + %s (%s)", m.member_id, m.member_type)
                if len(d.members_to_add) > 5:
                    progress.info("        ... and %d more", len(d.members_to_add) - 5)
                for m in d.members_to_remove[:5]:
                    progress.info("        - %s (%s)", m.member_id, m.member_type)
                if len(d.members_to_remove) > 5:
                    progress.info("        ... and %d more", len(d.members_to_remove) - 5)

        if deletes:
            progress.info("")
            progress.info("  STALE GROUPS (to delete):")
            for d in deletes:
                progress.info("    x %s", d.group_id)

        if unchanged:
            progress.info("")
            progress.info("  UNCHANGED GROUPS: %d (no API calls needed)", len(unchanged))

        elapsed = time.monotonic() - start_time
        progress.info("")
        progress.info("  Time: %.1fs", elapsed)
        progress.info("  Log:  %s", log_file)
        progress.info("=" * 60)
        progress.info("")
        progress.info("  This was a DRY RUN. No Graph API calls were made.")

        # ── Step 5: Optionally save to SQLite ─────────────────────────────────
        save_to_db = getattr(args, "save", False)
        if save_to_db:
            from graph.identity_store import SyncSessionStats

            progress.info("  --save flag set: writing crawl data to SQLite...")

            session_id = store.start_session(crawl_type="identity-dry-run")
            dry_stats = SyncSessionStats(
                session_id=session_id,
                groups_created=len(creates),
                groups_updated=len(updates),
                groups_deleted=len(deletes),
                groups_unchanged=len(unchanged),
                members_added=sum(len(d.members_to_add) for d in creates + updates),
                members_removed=sum(len(d.members_to_remove) for d in updates),
            )

            for group_id, (display_name, members) in flat.items():
                store.upsert_group(group_id, display_name)
                store.replace_members(group_id, members)

            # Delete stale groups from store
            for d in deletes:
                store.delete_group(d.group_id)

            store.complete_session(session_id, dry_stats, status="completed")

            final_stats = store.get_stats()
            progress.info(
                "  SQLite updated: %d group(s), %d member(s)",
                final_stats["groups"],
                final_stats["members"],
            )
            progress.info("  DB path: %s", store.db_path)
        else:
            progress.info("  Tip: add --save to write crawl data to SQLite without calling Graph.")

        progress.info("  To execute for real: set USE_GROUP_ACL=true and run full-deployment or ingest.")
        progress.info("")

        store.close()
        return True

    except Exception as e:
        logging.getLogger("identity_dry_run").exception("Fatal error: %s", e)
        return False
    finally:
        # Ensure the SQLite connection is always released
        try:
            store.close()
        except Exception:
            pass
