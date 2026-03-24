from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from connector.settings import AppConfig


ITEM_UPLOAD_LOG_FILE_NAME = "graphExternalItemUploadLog.json"
ITEM_REQUEST_DEBUG_LOG_FILE_NAME = "graphPutItemRequestDebugLog.json"
ITEM_RESPONSE_DEBUG_LOG_FILE_NAME = "graphGetItemResponseDebugLog.json"


def get_item_upload_log_path(config: AppConfig) -> Path:
    return config.repo_root / "tmp" / ITEM_UPLOAD_LOG_FILE_NAME


def get_item_request_debug_log_path(config: AppConfig) -> Path:
    return config.repo_root / "tmp" / ITEM_REQUEST_DEBUG_LOG_FILE_NAME


def get_item_response_debug_log_path(config: AppConfig) -> Path:
    return config.repo_root / "tmp" / ITEM_RESPONSE_DEBUG_LOG_FILE_NAME


def initialize_item_upload_log(config: AppConfig) -> None:
    log_path = get_item_upload_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "connectionId": config.connector.id,
                "createdAtUtc": _utc_now(),
                "items": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def initialize_item_request_debug_log(config: AppConfig) -> None:
    log_path = get_item_request_debug_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "connectionId": config.connector.id,
                "createdAtUtc": _utc_now(),
                "requests": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def initialize_item_response_debug_log(config: AppConfig) -> None:
    log_path = get_item_response_debug_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "connectionId": config.connector.id,
                "createdAtUtc": _utc_now(),
                "responses": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def record_uploaded_item(
    config: AppConfig,
    *,
    item_id: str,
    object_type: str | None,
    graph_path: str,
    url: str | None,
) -> None:
    log_path = get_item_upload_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {
            "connectionId": config.connector.id,
            "createdAtUtc": _utc_now(),
            "items": [],
        }

    items = payload.get("items")
    if not isinstance(items, list):
        items = []
        payload["items"] = items

    items.append(
        {
            "itemId": item_id,
            "objectType": object_type,
            "url": url,
            "graphPath": graph_path,
            "loggedAtUtc": _utc_now(),
        }
    )

    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_item_put_request(
    config: AppConfig,
    *,
    item_id: str,
    object_type: str | None,
    url: str | None,
    graph_path: str,
    request_payload: dict[str, Any],
) -> None:
    log_path = get_item_request_debug_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {
            "connectionId": config.connector.id,
            "createdAtUtc": _utc_now(),
            "requests": [],
        }

    requests = payload.get("requests")
    if not isinstance(requests, list):
        requests = []
        payload["requests"] = requests

    requests.append(
        {
            "itemId": item_id,
            "objectType": object_type,
            "url": url,
            "graphPath": graph_path,
            "requestPayload": request_payload,
            "loggedAtUtc": _utc_now(),
        }
    )

    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_item_get_response(
    config: AppConfig,
    *,
    item_id: str,
    object_type: str | None,
    url: str | None,
    graph_path: str,
    response_payload: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    log_path = get_item_response_debug_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {
            "connectionId": config.connector.id,
            "createdAtUtc": _utc_now(),
            "responses": [],
        }

    responses = payload.get("responses")
    if not isinstance(responses, list):
        responses = []
        payload["responses"] = responses

    entry: dict[str, Any] = {
        "itemId": item_id,
        "objectType": object_type,
        "url": url,
        "graphPath": graph_path,
        "loggedAtUtc": _utc_now(),
    }
    if response_payload is not None:
        entry["responsePayload"] = response_payload
    if error is not None:
        entry["error"] = error

    responses.append(entry)

    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")