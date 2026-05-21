from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import WebPageSnapshot, WebWatcherRecord
from .snapshot import fetch_web_page_snapshot, summarize_initial_snapshot
from .store import WebWatcherStore
from .utils import normalize_status, to_storage_timestamp, utc_now


class WebWatcherService:
    """High-level web watcher management with initial snapshot capture."""

    def __init__(self, store: WebWatcherStore):
        self._store = store

    async def create_watcher(
        self,
        *,
        name: str,
        url: str,
        condition: str,
        cadence_rule: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[WebWatcherRecord, WebPageSnapshot]:
        snapshot = await fetch_web_page_snapshot(url)
        now = utc_now()
        timestamp = to_storage_timestamp(now)
        record: Dict[str, Any] = {
            "name": name.strip(),
            "url": snapshot.url,
            "condition": condition.strip(),
            "cadence_rule": cadence_rule,
            "status": normalize_status(status),
            "last_snapshot_hash": snapshot.content_hash,
            "last_snapshot_summary": summarize_initial_snapshot(snapshot),
            "last_checked_at": snapshot.fetched_at,
            "last_notified_at": None,
            "last_error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        watcher_id = self._store.insert(record)
        created = self._store.fetch_one(watcher_id)
        if not created:  # pragma: no cover - defensive
            raise RuntimeError("Failed to load web watcher after insert")
        return created, snapshot

    def get_watcher(self, watcher_id: int) -> Optional[WebWatcherRecord]:
        return self._store.fetch_one(watcher_id)

    def list_watchers(self) -> List[WebWatcherRecord]:
        return self._store.list_all()

    def update_watcher(
        self,
        watcher_id: int,
        *,
        name: Optional[str] = None,
        condition: Optional[str] = None,
        cadence_rule: Optional[str] = None,
        status: Optional[str] = None,
        last_error: Optional[str] = None,
        clear_error: bool = False,
    ) -> Optional[WebWatcherRecord]:
        fields: Dict[str, Any] = {}
        if name is not None:
            fields["name"] = name.strip()
        if condition is not None:
            fields["condition"] = condition.strip()
        if cadence_rule is not None:
            fields["cadence_rule"] = cadence_rule
        if status is not None:
            fields["status"] = normalize_status(status)
        if clear_error:
            fields["last_error"] = None
        elif last_error is not None:
            fields["last_error"] = last_error

        if not fields:
            return self._store.fetch_one(watcher_id)
        updated = self._store.update(watcher_id, fields)
        return self._store.fetch_one(watcher_id) if updated else None

    def clear_all(self) -> None:
        self._store.clear_all()


__all__ = ["WebWatcherService"]
