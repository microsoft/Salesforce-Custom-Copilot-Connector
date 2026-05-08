"""
graph/identity.py
-----------------
Top-level orchestrator for the Identity Crawl + Publish pipeline.

Provides a single ``run_identity_sync()`` function that:

1. Queries Salesforce for group membership (via ``IdentitySyncHandler``).
2. Diffs against the SQLite store (via ``IdentityStore``).
3. Publishes only the changes to Microsoft Graph (via ``IdentityPublisher``).

Called by ``commands/deploy.py`` and ``commands/ingest.py`` when
``USE_GROUP_ACL=true``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from graph.client import GraphClient
from graph.identity_publisher import IdentityPublisher
from graph.identity_store import SyncSessionStats, create_store
from acl_engine.identity_sync import IdentitySyncHandler
from acl_engine.principal_mapper import PrincipalMapper
from acl_engine.salesforce_client import SalesforceClient
from salesforce.api_client import get_salesforce_access_token
from salesforce.settings import AppConfig

logger = logging.getLogger("salesforce_connector")


def run_identity_sync(config: AppConfig, graph_client: GraphClient) -> SyncSessionStats:
    """
    Execute a full identity crawl and publish changes to Microsoft Graph.

    Steps
    -----
    1. Create a ``SalesforceClient`` for identity queries.
    2. Run ``IdentitySyncHandler.run_full_crawl()`` to query all group
       memberships from Salesforce.
    3. Compare against the SQLite store and publish only the diff to Graph.

    Parameters
    ----------
    config       : Fully loaded ``AppConfig``.
    graph_client : Authenticated ``GraphClient``.

    Returns
    -------
    SyncSessionStats with counts of groups created/updated/deleted and
    API calls made.
    """
    progress = logging.getLogger("progress")

    # 1. Build Salesforce client
    sf_client = SalesforceClient(
        instance_url=config.connector.salesforce.instance_url,
        api_version=config.connector.salesforce.api_version,
        access_token=get_salesforce_access_token(config),
        token_refresher=lambda: get_salesforce_access_token(config),
    )

    # 2. Determine object names from config
    object_names = [
        obj["objectName"]
        for obj in config.schema_config.get("objectList", [])
        if obj.get("objectName")
    ]
    logger.info("[IdentitySync] Objects to crawl: %s", object_names)

    # 3. Run identity crawl
    progress.info("  Running identity crawl for %d object type(s)...", len(object_names))
    handler = IdentitySyncHandler(
        sf_client=sf_client,
        object_names=object_names,
        parent_map=config.parent_map,
        owd_overrides=config.owd_overrides,
        owd_field_map=config.owd_field_map,
    )
    crawl_result = handler.run_full_crawl()

    logger.info(
        "[IdentitySync] Crawl complete: %d group(s), %d user membership(s)",
        crawl_result.total_groups_emitted,
        crawl_result.total_users_emitted,
    )
    progress.info(
        "  Identity crawl: %d groups, %d memberships",
        crawl_result.total_groups_emitted,
        crawl_result.total_users_emitted,
    )

    # 4. Publish to Graph (diff-based, with AAD resolution)
    progress.info("  Publishing identity changes to Graph...")
    mapper = PrincipalMapper(
        sf_client=sf_client,
        graph_client=graph_client,
        tenant_id=config.tenant_id,
        batch_size=config.tuning.salesforce_batch_size,
    )
    with create_store(config.connector.id) as store:
        publisher = IdentityPublisher(
            graph_client=graph_client,
            connection_id=config.connector.id,
            store=store,
            principal_mapper=mapper,
        )
        stats = publisher.publish(crawl_result)

    progress.info(
        "  Identity sync: created=%d updated=%d deleted=%d unchanged=%d (API calls=%d)",
        stats.groups_created,
        stats.groups_updated,
        stats.groups_deleted,
        stats.groups_unchanged,
        stats.api_calls_made,
    )

    return stats


def record_content_crawl(config: AppConfig, ingestion_stats: Any, sync_type: str = "full") -> None:
    """
    Record content crawl stats in the SQLite sync_sessions table.

    Called after ``ingest_content()`` completes so that content crawl history
    is tracked alongside identity crawl history in one DB.

    Parameters
    ----------
    config          : Fully loaded ``AppConfig``.
    ingestion_stats : ``IngestionStats`` from ``graph.ingest.ingest_content()``.
    sync_type       : ``"full"`` or ``"incremental"``.
    """
    with create_store(config.connector.id) as store:
        session_id = store.start_session(crawl_type="content", sync_type=sync_type)
        stats = SyncSessionStats(
            session_id=session_id,
            sync_type=sync_type,
            content_total_fetched=ingestion_stats.total_fetched,
            content_success=ingestion_stats.success_count,
            content_failed=ingestion_stats.failed_count,
            content_deleted=ingestion_stats.deleted_count,
            content_acl_engine=ingestion_stats.acl_engine,
            errors=ingestion_stats.failed_count,
        )
        store.complete_session(session_id, stats)
    logger.info(
        "[ContentCrawl] Session recorded (sync_type=%s): fetched=%d success=%d failed=%d deleted=%d acl=%s",
        sync_type,
        stats.content_total_fetched,
        stats.content_success,
        stats.content_failed,
        stats.content_deleted,
        stats.content_acl_engine,
    )


def get_last_content_crawl_time(config: AppConfig) -> datetime | None:
    """
    Return the start timestamp of the last successful content crawl.

    Used by the incremental content crawl to determine the ``since``
    parameter for ``ingest_content()`` — only Salesforce records modified
    after this timestamp are fetched.

    Returns
    -------
    datetime (UTC) or None if no previous content crawl exists.
    """
    with create_store(config.connector.id) as store:
        result = store.get_last_successful_content_crawl_time()
    if result:
        logger.info("[ContentCrawl] Last successful crawl: %s", result.isoformat())
    else:
        logger.info("[ContentCrawl] No previous content crawl found — will do full sync")
    return result
