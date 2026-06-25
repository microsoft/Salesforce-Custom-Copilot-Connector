# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlencode
import json
import logging
import os
import queue
import re
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from salesforce.sharing_model import SalesforceConstants
from item.converter import METADATA_COLUMNS, load_converter_config
from salesforce.settings import AppConfig
from salesforce.utils import to_iso_z


logger = logging.getLogger("salesforce_connector")


def _build_sf_session() -> requests.Session:
    """Create a ``requests.Session`` with connection pooling and retry for Salesforce API calls.

    Reusing a session avoids repeated TCP + TLS handshakes — this alone can
    cut per-request latency by 50-80 ms on typical Salesforce endpoints.
    """
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Module-level pooled session — shared across all Salesforce fetch threads.
# Thread-safe: urllib3 connection pools are internally locked.
_sf_session = _build_sf_session()

# ── Per-org, per-object field-list cache (SQLite-backed) ───────────────────
# After the per-field retry loop discovers which fields work for a given org,
# we persist the final list in the SQLite state store so subsequent runs skip
# the retry loop entirely.  The store is located at data/{connection_id}_identity.db.
#
# We need a connection_id to open the store, but the cache functions only
# receive instance_url.  We use a module-level variable that is set when
# load_config() is called (via the first fetch_salesforce_records call).
_active_connection_id: str | None = None


def _load_field_cache(instance_url: str, object_type: str) -> tuple[str, ...] | None:
    """Return cached field list from SQLite, or ``None`` if not found."""
    if not _active_connection_id:
        return None
    try:
        from graph.identity_store import create_store
        with create_store(_active_connection_id) as store:
            result = store.get_cached_fields(instance_url, object_type)
            if result:
                logger.info(
                    "[FieldCache] Loaded %d cached fields for %s from SQLite",
                    len(result), object_type,
                )
            return result
    except Exception as exc:
        logger.warning("[FieldCache] Could not load cache for %s: %s", object_type, exc)
    return None


def _save_field_cache(instance_url: str, object_type: str, fields: tuple[str, ...]) -> None:
    """Persist working field list to SQLite so the next run skips the retry loop."""
    if not _active_connection_id:
        return
    try:
        from graph.identity_store import create_store
        with create_store(_active_connection_id) as store:
            store.save_cached_fields(instance_url, object_type, fields)
            logger.info(
                "[FieldCache] Saved %d working fields for %s to SQLite",
                len(fields), object_type,
            )
    except Exception as exc:
        logger.warning("[FieldCache] Could not save cache for %s: %s", object_type, exc)


INVALID_FIELD_NAME_PATTERNS = (
    re.compile(r"No such column '([^']+)' on entity", re.IGNORECASE),
    re.compile(r"No such column '([^']+)' on sobject", re.IGNORECASE),
)
INVALID_RELATIONSHIP_PATTERN = re.compile(
    r"Didn't understand relationship '([^']+)' in field path",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SalesforceObjectConfig:
    object_type: str
    fields: tuple[str, ...]
    filter_condition: str = ""


@dataclass(frozen=True)
class SalesforceErrorInfo:
    error_code: str | None
    message: str | None
    raw_text: str


def _dedupe_fields(fields: list[str]) -> tuple[str, ...]:
    """Return *fields* as an order-preserving tuple with duplicates and blanks removed."""
    seen: set[str] = set()
    ordered: list[str] = []
    for field in fields:
        if not field or field in seen:
            continue
        seen.add(field)
        ordered.append(field)
    return tuple(ordered)


def _build_object_configs() -> tuple[SalesforceObjectConfig, ...]:
    """Build a ``SalesforceObjectConfig`` for each object defined in the converter config."""
    converter_config = load_converter_config()
    converter_objects = {
        object_config["objectName"]: object_config
        for object_config in converter_config["objectList"]
    }

    configs: list[SalesforceObjectConfig] = []

    for object_name in SalesforceConstants.ORDERED_OBJECT_NAMES:
        object_config = converter_objects.get(object_name)
        selected_fields = list((object_config or {}).get("selectedFields", {}).keys())
        fields = _dedupe_fields(["Id", *selected_fields, *METADATA_COLUMNS])
        configs.append(
            SalesforceObjectConfig(
                object_type=object_name,
                fields=fields,
                filter_condition=(object_config or {}).get("filterCondition", ""),
            )
        )

    # configs.append(
    #     SalesforceObjectConfig(
    #         object_type="Customer_Project__c",
    #         fields=_dedupe_fields(["Id", *LEGACY_QUERY_FIELDS["Customer_Project__c"]]),
    #     )
    #)

    return tuple(configs)


OBJECT_CONFIGS = _build_object_configs()


def get_object_counts(
    config: AppConfig,
    since: datetime | None = None,
) -> dict[str, int]:
    """Run lightweight ``SELECT COUNT()`` queries to get total record counts per object type.

    Salesforce returns the count in the ``totalSize`` response field for
    ``COUNT()`` queries, so no actual records are transferred.  This is
    used by the dashboard to calculate a stable ETA from the start.
    """
    access_token = get_salesforce_access_token(config)
    base_url = config.connector.salesforce.instance_url
    api_version = config.connector.salesforce.api_version
    headers = {"accept": "application/json", "authorization": f"Bearer {access_token}"}

    active_configs = OBJECT_CONFIGS
    if config.debug_object_type:
        active_configs = tuple(c for c in OBJECT_CONFIGS if c.object_type == config.debug_object_type)

    counts: dict[str, int] = {}
    for obj_cfg in active_configs:
        soql = f"SELECT COUNT() FROM {obj_cfg.object_type}"
        where_clauses: list[str] = []
        if obj_cfg.filter_condition:
            where_clauses.append(obj_cfg.filter_condition)
        if since:
            where_clauses.append(f"LastModifiedDate >= {to_iso_z(since)}")
        if where_clauses:
            soql += f" WHERE {' AND '.join(where_clauses)}"
        url = _build_query_url(base_url, api_version, soql)
        try:
            resp = _sf_session.get(url, headers=headers, timeout=30)
            if resp.ok:
                counts[obj_cfg.object_type] = resp.json().get("totalSize", 0)
                logger.info("COUNT %s: %d records", obj_cfg.object_type, counts[obj_cfg.object_type])
        except Exception as exc:
            logger.warning("COUNT query failed for %s: %s", obj_cfg.object_type, exc)
    return counts


def get_salesforce_access_token(config: AppConfig) -> str:
    """Authenticate with Salesforce using client-credentials and return an access token."""
    token_url = f"{config.connector.salesforce.instance_url}/services/oauth2/token"
    logger.info("Authenticating with Salesforce at %s", token_url)

    response = _sf_session.post(
        token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": config.connector.salesforce.client_id,
            "client_secret": config.connector.salesforce.client_secret,
        },
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            f"Failed to authenticate with Salesforce: {response.status_code} {response.reason} - {response.text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Salesforce authentication response did not contain an access token")

    logger.info("Successfully authenticated with Salesforce")
    return access_token


def get_all_items_from_api(config: AppConfig, since: datetime | None = None) -> Iterator[dict[str, Any]]:
    """Yield all Salesforce records across configured objects, optionally filtered by *since*.

    Records for each object type are fetched **in parallel** using one thread per
    object type, then merged into a single sequential stream ordered by object
    type.  This dramatically reduces total fetch time for large orgs.

    When ``config.debug_object_type`` is set (``ingest-object`` command), only
    the matching object's thread is started — no unnecessary Salesforce API calls
    are made for the other objects.

    When ``config.debug_item_id`` is set (``ingest-item`` command), all object
    threads start (we don't know which object the ID belongs to) but each thread
    stops as soon as it has yielded the matching record, thanks to the sentinel
    drain in the consumer.
    """
    debug_object_type = config.debug_object_type

    # When a specific object type is requested, restrict to that one config only.
    # This avoids starting N-1 unnecessary Salesforce fetch threads.
    active_configs = (
        [cfg for cfg in OBJECT_CONFIGS if cfg.object_type == debug_object_type]
        if debug_object_type
        else list(OBJECT_CONFIGS)
    )

    if debug_object_type and not active_configs:
        logger.warning(
            "DEBUG_OBJECT_TYPE '%s' not found in OBJECT_CONFIGS — nothing to fetch.",
            debug_object_type,
        )
        return

    access_token = get_salesforce_access_token(config)

    _SENTINEL = object()  # signals that a producer thread has finished

    # One bounded queue per active object type
    per_object_queues: list[tuple[SalesforceObjectConfig, queue.Queue]] = [
        (obj_cfg, queue.Queue(maxsize=200))
        for obj_cfg in active_configs
    ]

    def _producer(obj_cfg: SalesforceObjectConfig, q: queue.Queue) -> None:
        try:
            for record in fetch_salesforce_records(config, access_token, obj_cfg, since):
                clean_url = (
                    f"{config.connector.salesforce.instance_url}/{record['Id']}"
                    .replace("'", "")
                    .replace('"', "")
                )
                record["url"] = clean_url
                q.put(record)  # blocks when queue is full → natural back-pressure
        except Exception as exc:  # pragma: no cover
            logger.error(
                "Producer thread failed for %s: %s — remaining records for this "
                "object type will NOT be fetched. This may cause missing items.",
                obj_cfg.object_type, exc, exc_info=True,
            )
        finally:
            q.put(_SENTINEL)

    # Start one producer thread per active object type
    threads = []
    for obj_cfg, q in per_object_queues:
        t = threading.Thread(
            target=_producer,
            args=(obj_cfg, q),
            name=f"sf-fetch-{obj_cfg.object_type}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        logger.debug("Started fetch thread for %s", obj_cfg.object_type)

    logger.info(
        "Fetching %d object type(s) in parallel: %s",
        len(active_configs),
        ", ".join(c.object_type for c in active_configs),
    )

    # Drain queues in schema order (preserves grouping required by the ingest pipeline)
    for _obj_cfg, q in per_object_queues:
        while True:
            item = q.get()
            if item is _SENTINEL:
                break
            yield item

    for t in threads:
        t.join()


def iter_object_chunks(
    config: AppConfig,
    object_config: SalesforceObjectConfig,
    since: datetime | None,
    chunk_size: int,
) -> Iterator[list[dict[str, Any]]]:
    """Yield record chunks (lists of at most *chunk_size* dicts) for a single object type.

    Each record is enriched with ``objectType`` and ``url`` fields,
    identical to the output of ``get_all_items_from_api``.

    If the Salesforce fetch fails mid-pagination, any partially buffered
    records are still yielded so they are not silently lost.
    """
    access_token = get_salesforce_access_token(config)
    buffer: list[dict[str, Any]] = []
    try:
        for record in fetch_salesforce_records(config, access_token, object_config, since):
            clean_url = (
                f"{config.connector.salesforce.instance_url}/{record['Id']}"
                .replace("'", "")
                .replace('"', "")
            )
            record["url"] = clean_url
            buffer.append(record)
            if len(buffer) >= chunk_size:
                yield buffer
                buffer = []
    except _SkipObjectError:
        raise  # Let skip-object errors propagate as-is
    except Exception as exc:
        logger.error(
            "Salesforce fetch failed for %s mid-pagination — %d record(s) in buffer, "
            "remaining pages will NOT be fetched: %s",
            object_config.object_type, len(buffer), exc, exc_info=True,
        )
    if buffer:
        yield buffer


def get_object_config(object_type: str) -> SalesforceObjectConfig | None:
    """Return the ``SalesforceObjectConfig`` for *object_type*, or ``None``."""
    for cfg in OBJECT_CONFIGS:
        if cfg.object_type == object_type:
            return cfg
    return None


class _SkipObjectError(RuntimeError):
    """Raised when a Salesforce object type is not available in this org."""


def fetch_salesforce_records(
    config: AppConfig,
    access_token: str,
    object_config: SalesforceObjectConfig,
    since: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Fetch records for a single Salesforce object, retrying on unsupported-field errors."""
    global _active_connection_id
    _active_connection_id = config.connector.id

    base_url = config.connector.salesforce.instance_url
    api_version = config.connector.salesforce.api_version

    def _make_headers(token: str) -> dict[str, str]:
        return {
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        }

    headers = _make_headers(access_token)
    current_token = access_token

    active_config = object_config
    all_removed_fields: list[str] = []

    # ── Load per-org field cache (skip retry loop on subsequent runs) ─────────
    cached_fields = _load_field_cache(
        config.connector.salesforce.instance_url, object_config.object_type
    )
    if cached_fields is not None:
        # Use the pre-validated field list directly — no retry loop needed
        active_config = replace(object_config, fields=cached_fields)
        logger.info(
            "[FieldCache] Using %d cached fields for %s (skipping retry loop)",
            len(cached_fields), object_config.object_type,
        )

    while True:
        soql = build_soql_query(
            active_config,
            since,
            query_limit=config.tuning.salesforce_query_limit,
            record_id=config.debug_item_id,
        )
        query_url = _build_query_url(base_url, api_version, soql)
        logger.info("Querying Salesforce %s: %s", active_config.object_type, soql)

        next_url: str | None = query_url
        fetched_count = 0
        retry_requested = False

        while next_url:
            response = _sf_session.get(next_url, headers=headers, timeout=60)

            # ── 401: token expired — refresh once and retry immediately ────────────────
            if response.status_code == 401:
                logger.warning(
                    "[SF] 401 Unauthorized for %s — refreshing Salesforce token and retrying",
                    active_config.object_type,
                )
                try:
                    current_token = get_salesforce_access_token(config)
                    headers = _make_headers(current_token)
                    response = _sf_session.get(next_url, headers=headers, timeout=60)
                except Exception as exc:
                    raise RuntimeError(
                        f"Token refresh failed for {active_config.object_type}: {exc}"
                    ) from exc

            if not response.ok:
                # ── 400 INVALID_TYPE: object not available in this org — skip ──────
                error_info = _extract_salesforce_error_info(response)
                if response.status_code == 400 and "INVALID_TYPE" in (str(error_info) if error_info else ""):
                    raise _SkipObjectError(
                        f"{active_config.object_type} is not available in this Salesforce org (INVALID_TYPE)"
                    )

                retry_config, removed_fields = _build_retry_object_config(active_config, error_info)
                if retry_config is not None and next_url == query_url and fetched_count == 0:
                    all_removed_fields.extend(removed_fields)
                    active_config = retry_config
                    retry_requested = True
                    break

                raise RuntimeError(_format_salesforce_fetch_error(active_config.object_type, response))

            data = response.json()
            for record in data.get("records", []):
                record["objectType"] = active_config.object_type
                fetched_count += 1
                yield record

            next_records_url = data.get("nextRecordsUrl")
            next_url = f"{base_url}{next_records_url}" if next_records_url else None

        if retry_requested:
            continue

        if all_removed_fields:
            logger.warning(
                "Salesforce %s: skipped unsupported field(s): %s",
                active_config.object_type,
                ", ".join(all_removed_fields),
            )
            # Persist the working field list so the next run skips the retry loop
            if cached_fields is None:  # only save when we just discovered it
                _save_field_cache(
                    config.connector.salesforce.instance_url,
                    active_config.object_type,
                    active_config.fields,
                )
        logger.info("Fetched %s %s records from Salesforce", fetched_count, active_config.object_type)
        return


def _build_query_url(base_url: str, api_version: str, soql: str) -> str:
    """Construct a full Salesforce REST API query URL from the given SOQL."""
    return f"{base_url}/services/data/{api_version}/query?{urlencode({'q': soql})}"


def _format_salesforce_fetch_error(object_type: str, response: requests.Response) -> str:
    """Format a human-readable error message for a failed Salesforce fetch."""
    return (
        "Failed to fetch "
        f"{object_type} from Salesforce: {response.status_code} {response.reason} - {response.text}"
    )


def _extract_salesforce_error_info(response: requests.Response) -> SalesforceErrorInfo:
    """Extract a structured ``SalesforceErrorInfo`` from an error response."""
    try:
        payload = response.json()
    except ValueError:
        return SalesforceErrorInfo(error_code=None, message=None, raw_text=response.text)

    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                error_code = entry.get("errorCode")
                message = entry.get("message")
                if error_code or message:
                    return SalesforceErrorInfo(error_code=error_code, message=message, raw_text=response.text)
    elif isinstance(payload, dict):
        return SalesforceErrorInfo(
            error_code=payload.get("errorCode"),
            message=payload.get("message"),
            raw_text=response.text,
        )

    return SalesforceErrorInfo(error_code=None, message=None, raw_text=response.text)


def _build_retry_object_config(
    object_config: SalesforceObjectConfig,
    error_info: SalesforceErrorInfo,
) -> tuple[SalesforceObjectConfig | None, tuple[str, ...]]:
    """Return a new config with unsupported fields removed, or ``(None, ())`` if retry is not possible."""
    unsupported_fields = _extract_unsupported_fields(object_config.fields, error_info)
    if not unsupported_fields:
        return None, ()

    remaining_fields = tuple(field for field in object_config.fields if field not in set(unsupported_fields))
    if not remaining_fields or len(remaining_fields) == len(object_config.fields):
        return None, ()

    return replace(object_config, fields=remaining_fields), unsupported_fields


def _extract_unsupported_fields(
    fields: tuple[str, ...],
    error_info: SalesforceErrorInfo,
) -> tuple[str, ...]:
    """Identify field names flagged as invalid or using an unsupported relationship in *error_info*."""
    if error_info.error_code != "INVALID_FIELD" or not error_info.message:
        return ()

    for pattern in INVALID_FIELD_NAME_PATTERNS:
        match = pattern.search(error_info.message)
        if match:
            return _match_exact_or_prefixed_fields(fields, match.group(1))

    relationship_match = INVALID_RELATIONSHIP_PATTERN.search(error_info.message)
    if relationship_match:
        relationship_name = relationship_match.group(1).lower()
        return tuple(
            field
            for field in fields
            if field.split(".", 1)[0].lower() == relationship_name
        )

    return ()


def _match_exact_or_prefixed_fields(fields: tuple[str, ...], candidate: str) -> tuple[str, ...]:
    """Return fields matching *candidate* exactly or starting with ``candidate.`` (case-insensitive)."""
    candidate_lower = candidate.lower()
    exact_matches = tuple(field for field in fields if field.lower() == candidate_lower)
    if exact_matches:
        return exact_matches

    prefix = f"{candidate_lower}."
    return tuple(field for field in fields if field.lower().startswith(prefix))


def build_soql_query(
    object_config: SalesforceObjectConfig,
    since: datetime | None,
    query_limit: int = 0,
    record_id: str | None = None,
) -> str:
    """Build a SOQL SELECT string for *object_config*, with optional *since* and *query_limit* clauses.

    When *record_id* is provided, a ``WHERE Id = '<record_id>'`` clause is added
    so that only the single matching record is returned.  This is used by the
    ``ingest-item`` command to avoid fetching the entire object table.

    When *query_limit* is ``0`` (or negative) no ``LIMIT`` clause is added and Salesforce
    paginates the full result set automatically at 2 000 records per page via
    ``nextRecordsUrl``.  Set a positive value only for local testing / debugging.
    """
    soql = f"SELECT {', '.join(object_config.fields)} FROM {object_config.object_type}"
    where_clauses: list[str] = []
    if record_id:
        where_clauses.append(f"Id = '{record_id}'")
    if object_config.filter_condition:
        where_clauses.append(object_config.filter_condition)
    if since:
        where_clauses.append(f"LastModifiedDate >= {to_iso_z(since)}")
    if where_clauses:
        soql += f" WHERE {' AND '.join(where_clauses)}"
    if query_limit and query_limit > 0:
        soql += f" LIMIT {query_limit}"
    return soql
