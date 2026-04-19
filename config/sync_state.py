"""
Persistent sync state for the ingestion pipeline.

Manages three concerns:

1. **Delta sync timestamp** — records the last successful sync completion
   time per connector so subsequent runs fetch only changed records.
2. **Checkpointing** — tracks completed chunks within an in-progress run so
   the process can resume after a crash without re-processing everything.
3. **Dead-letter queue** — persists failed item IDs to a JSONL file for
   inspection and retry in a subsequent pass.

All state files live in the ``logs/`` directory alongside the run logs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("salesforce_connector")

LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"


# ── Delta sync timestamp ─────────────────────────────────────────────────────

_SYNC_STATE_FILE = LOGS_DIR / "sync_state.json"


def read_last_sync(connector_id: str) -> datetime | None:
    """Return the last successful sync timestamp for *connector_id*, or ``None``."""
    try:
        data = json.loads(_SYNC_STATE_FILE.read_text(encoding="utf-8"))
        ts = data.get(connector_id)
        if ts:
            return datetime.fromisoformat(ts)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return None


def write_last_sync(connector_id: str, timestamp: datetime) -> None:
    """Persist *timestamp* as the last successful sync time for *connector_id*."""
    data: dict[str, Any] = {}
    try:
        data = json.loads(_SYNC_STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    data[connector_id] = timestamp.isoformat()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _SYNC_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Saved last sync timestamp: %s", timestamp.isoformat())


# ── Checkpointing ────────────────────────────────────────────────────────────


def _checkpoint_path(connector_id: str) -> Path:
    return LOGS_DIR / f"checkpoint_{connector_id}.json"


def read_checkpoint(connector_id: str) -> dict[str, Any] | None:
    """Read the checkpoint for *connector_id*.

    Returns a dict with keys ``"since"`` and ``"completed"``
    (``{object_type: last_completed_chunk_index}``), or ``None`` if no
    checkpoint exists.
    """
    path = _checkpoint_path(connector_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "completed" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def write_checkpoint(
    connector_id: str,
    since_iso: str | None,
    object_type: str,
    chunk_index: int,
) -> None:
    """Mark *object_type* chunk *chunk_index* as completed.

    The checkpoint stores the ``since`` ISO string so that a resume uses the
    same incremental boundary as the original run.
    """
    path = _checkpoint_path(connector_id)
    data: dict[str, Any] = {"since": since_iso, "completed": {}}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and existing.get("since") == since_iso:
            data = existing
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    data["completed"][object_type] = max(
        data["completed"].get(object_type, 0),
        chunk_index,
    )
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clear_checkpoint(connector_id: str) -> None:
    """Remove the checkpoint file for *connector_id*."""
    path = _checkpoint_path(connector_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ── Dead-letter queue ────────────────────────────────────────────────────────


def failed_records_path(connector_id: str) -> Path:
    """Return the path to the dead-letter JSONL file for *connector_id*."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"failed_records_{connector_id}.jsonl"


def append_failed_records(
    file_path: Path,
    failures: list[tuple[str, str]] | list[str],
    object_type: str,
    error: str = "",
) -> None:
    """Append failed items to the dead-letter JSONL file.

    *failures* is either:
    - a list of ``(item_id, error_detail)`` tuples (per-item errors), or
    - a plain list of item-ID strings (all share the same *error*).
    """
    if not failures:
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(file_path, "a", encoding="utf-8") as fh:
        for entry in failures:
            if isinstance(entry, tuple):
                item_id, item_error = entry
            else:
                item_id, item_error = entry, error
            line = json.dumps({
                "item_id": item_id,
                "object_type": object_type,
                "error": item_error,
                "timestamp": timestamp,
            })
            fh.write(line + "\n")


def read_failed_records(connector_id: str) -> list[dict[str, Any]]:
    """Read all entries from the dead-letter file for *connector_id*."""
    path = failed_records_path(connector_id)
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        pass
    return entries


def clear_failed_records(connector_id: str) -> None:
    """Remove the dead-letter file for *connector_id*."""
    path = failed_records_path(connector_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
