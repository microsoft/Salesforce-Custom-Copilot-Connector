# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from datetime import datetime, timezone
import time


def delay(seconds: float) -> None:
    """Sleep for the given number of seconds."""
    time.sleep(seconds)


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def unix_epoch() -> datetime:
    """Return the Unix epoch (1970-01-01T00:00:00Z) as a UTC datetime."""
    return datetime.fromtimestamp(0, tz=timezone.utc)


def normalize_datetime(value: datetime) -> datetime:
    """Ensure *value* is a UTC-aware datetime, assuming UTC if naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_iso_z(value: datetime) -> str:
    """Format *value* as an ISO-8601 string with a trailing ``Z`` suffix."""
    return normalize_datetime(value).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 string (with optional ``Z`` suffix) into a UTC datetime."""
    normalized = value.replace("Z", "+00:00")
    return normalize_datetime(datetime.fromisoformat(normalized))
