from __future__ import annotations

from pathlib import Path

from .models import WebPageSnapshot, WebWatcherCreate, WebWatcherRecord
from .service import WebWatcherService
from .snapshot import WebPageSnapshotError, fetch_web_page_snapshot, summarize_initial_snapshot
from .store import WebWatcherStore


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_default_db_path = _DATA_DIR / "web_watchers.db"
_web_watcher_store = WebWatcherStore(_default_db_path)
_web_watcher_service = WebWatcherService(_web_watcher_store)


def get_web_watcher_service() -> WebWatcherService:
    return _web_watcher_service


__all__ = [
    "WebPageSnapshot",
    "WebPageSnapshotError",
    "WebWatcherCreate",
    "WebWatcherRecord",
    "WebWatcherService",
    "WebWatcherStore",
    "fetch_web_page_snapshot",
    "get_web_watcher_service",
    "summarize_initial_snapshot",
]
