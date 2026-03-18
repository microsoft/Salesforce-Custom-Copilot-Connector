from __future__ import annotations

from datetime import datetime, timezone
import time


def delay(seconds: float) -> None:
    time.sleep(seconds)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def unix_epoch() -> datetime:
    return datetime.fromtimestamp(0, tz=timezone.utc)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_iso_z(value: datetime) -> str:
    return normalize_datetime(value).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return normalize_datetime(datetime.fromisoformat(normalized))
