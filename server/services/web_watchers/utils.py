from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


UTC = timezone.utc
DEFAULT_STATUS = "active"
VALID_STATUSES = {"active", "paused", "completed"}


def utc_now() -> datetime:
    """Return the current time in UTC."""

    return datetime.now(UTC)


def to_storage_timestamp(moment: datetime) -> str:
    """Normalize timestamps before writing to SQLite."""

    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_status(status: Optional[str]) -> str:
    """Clamp watcher status to the known set."""

    if not status:
        return DEFAULT_STATUS
    normalized = status.lower()
    if normalized not in VALID_STATUSES:
        return DEFAULT_STATUS
    return normalized


__all__ = ["DEFAULT_STATUS", "VALID_STATUSES", "normalize_status", "to_storage_timestamp", "utc_now"]
