from __future__ import annotations

from datetime import datetime
import json

from connector.settings import REPO_ROOT
from connector.utils import normalize_datetime, parse_datetime


LAST_CRAWL_FILE = REPO_ROOT / "tmp" / "lastCrawl.json"


def save_last_crawl(last_crawl: datetime) -> None:
    LAST_CRAWL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_CRAWL_FILE.write_text(
        json.dumps(normalize_datetime(last_crawl).isoformat()),
        encoding="utf-8",
    )


def get_last_crawl() -> datetime | None:
    try:
        raw_value = json.loads(LAST_CRAWL_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if isinstance(raw_value, str):
        return parse_datetime(raw_value)

    return None