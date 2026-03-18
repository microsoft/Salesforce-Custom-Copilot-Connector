from __future__ import annotations

import logging
import os
import time

import azure.functions as func

from connector.connection import (
    clear_connection_items,
    delete_connection,
    ensure_connection,
    is_connection_ready,
    set_search_settings,
)
from connector.crawl_state import get_last_crawl, save_last_crawl
from connector.graph import GraphClient
from connector.ingest import ingest_content
from connector.schema import ensure_schema
from connector.settings import load_config, load_local_environment
from connector.utils import unix_epoch, utc_now


load_local_environment()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("salesforce_connector")

app = func.FunctionApp()

full_crawl_in_progress = False
retract_in_progress = False
_graph_client: GraphClient | None = None


def _get_graph_client() -> GraphClient:
    global _graph_client

    if _graph_client is None:
        _graph_client = GraphClient()
    return _graph_client


def _is_development() -> bool:
    return os.getenv("AZURE_FUNCTIONS_ENVIRONMENT", "").lower() == "development"


def _deploy_connection() -> None:
    config = load_config()
    client = _get_graph_client()
    initial_timestamp = time.monotonic()

    if ensure_connection(config, client, initial_timestamp):
        ensure_schema(config, client)
        set_search_settings(config, client)
        _full_crawl()


def _full_crawl() -> None:
    global full_crawl_in_progress

    config = load_config()
    client = _get_graph_client()

    if not is_connection_ready(config, client):
        logger.warning("Connection not ready yet...")
        return

    full_crawl_in_progress = True
    last_crawl = get_last_crawl()
    next_crawl = utc_now()

    try:
        logger.info("Starting full crawl...")
        since = last_crawl if _is_development() else None
        ingest_content(config, client, since=since)
        save_last_crawl(next_crawl)
        logger.info("Finished full crawl...")
    finally:
        full_crawl_in_progress = False


def _incremental_crawl() -> None:
    config = load_config()
    client = _get_graph_client()

    if not is_connection_ready(config, client):
        logger.warning("Connection not ready yet...")
        return

    if full_crawl_in_progress:
        logger.warning("Full crawl in progress, skipping incremental...")
        return

    if retract_in_progress:
        logger.warning("Retract in progress, skipping incremental...")
        return

    logger.info("Starting incremental crawl...")
    next_crawl = utc_now()
    last_crawl = get_last_crawl()
    ingest_content(config, client, since=last_crawl)
    save_last_crawl(next_crawl)
    logger.info("Finished incremental crawl...")


@app.function_name(name="deployConnection")
@app.schedule(schedule="0 0 0 1 1 *", arg_name="timer", run_on_startup=True, use_monitor=False)
def deploy_connection_timer(timer: func.TimerRequest) -> None:
    _deploy_connection()


@app.function_name(name="fullCrawl")
@app.schedule(schedule="0 0 0 * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def full_crawl_timer(timer: func.TimerRequest) -> None:
    _full_crawl()


@app.function_name(name="incrementalCrawl")
@app.schedule(schedule="0 0 */12 * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def incremental_crawl_timer(timer: func.TimerRequest) -> None:
    _incremental_crawl()


@app.function_name(name="retract")
@app.route(route="retract", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def retract_connection(req: func.HttpRequest) -> func.HttpResponse:
    global full_crawl_in_progress
    global retract_in_progress

    if not _is_development():
        return func.HttpResponse(status_code=404)

    config = load_config()
    client = _get_graph_client()

    if not is_connection_ready(config, client):
        logger.warning("Connection not ready yet...")
        return func.HttpResponse("Connection not ready yet.", status_code=409)

    full_crawl_in_progress = False
    retract_in_progress = True

    try:
        delete_connection(config, client, time.monotonic())
        save_last_crawl(unix_epoch())
        return func.HttpResponse(status_code=204)
    finally:
        retract_in_progress = False


@app.function_name(name="clear")
@app.route(route="clear", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def clear_connection(req: func.HttpRequest) -> func.HttpResponse:
    if not _is_development():
        return func.HttpResponse(status_code=404)

    config = load_config()
    client = _get_graph_client()

    if not is_connection_ready(config, client):
        logger.warning("Connection not ready yet...")
        return func.HttpResponse("Connection not ready yet.", status_code=409)

    if full_crawl_in_progress:
        logger.warning("Full crawl in progress...")
        return func.HttpResponse("Full crawl in progress.", status_code=409)

    deleted_count = clear_connection_items(config, client)
    save_last_crawl(unix_epoch())
    return func.HttpResponse(f"Deleted {deleted_count} items.", status_code=200)
