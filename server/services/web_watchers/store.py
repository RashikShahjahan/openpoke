from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...logging_config import logger
from .models import WebWatcherRecord
from .utils import to_storage_timestamp, utc_now


class WebWatcherStore:
    """Low-level persistence for web watchers backed by SQLite."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_directory()
        self._ensure_schema()

    def _ensure_directory(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "web watcher directory creation failed",
                extra={"error": str(exc)},
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        schema_sql = """
        CREATE TABLE IF NOT EXISTS web_watchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            condition TEXT NOT NULL,
            cadence_rule TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            last_snapshot_hash TEXT,
            last_snapshot_summary TEXT,
            last_checked_at TEXT,
            last_notified_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        index_sql = """
        CREATE INDEX IF NOT EXISTS idx_web_watchers_status_updated
        ON web_watchers (status, updated_at);
        """
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(schema_sql)
            conn.execute(index_sql)

    def insert(self, payload: Dict[str, Any]) -> int:
        with self._lock, self._connect() as conn:
            columns = ", ".join(payload.keys())
            placeholders = ", ".join([":" + key for key in payload.keys()])
            sql = f"INSERT INTO web_watchers ({columns}) VALUES ({placeholders})"
            conn.execute(sql, payload)
            watcher_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(watcher_id)

    def fetch_one(self, watcher_id: int) -> Optional[WebWatcherRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM web_watchers WHERE id = ?",
                (watcher_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update(self, watcher_id: int, fields: Dict[str, Any]) -> bool:
        if not fields:
            return False
        assignments = ", ".join(f"{key} = :{key}" for key in fields.keys())
        sql = f"UPDATE web_watchers SET {assignments}, updated_at = :updated_at WHERE id = :watcher_id"
        payload = {
            **fields,
            "updated_at": to_storage_timestamp(utc_now()),
            "watcher_id": watcher_id,
        }
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, payload)
            return cursor.rowcount > 0

    def list_all(self) -> List[WebWatcherRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM web_watchers ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def clear_all(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM web_watchers")

    def _row_to_record(self, row: sqlite3.Row) -> WebWatcherRecord:
        data = dict(row)
        return WebWatcherRecord.model_validate(data)


__all__ = ["WebWatcherStore"]
