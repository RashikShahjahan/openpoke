"""SQLite-backed execution agent roster."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from ...logging_config import logger


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_AGENT_DB_PATH = _DATA_DIR / "execution_agents" / "agents.sqlite3"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(attach|alter|analyze|begin|commit|create|delete|detach|drop|insert|pragma|replace|"
    r"reindex|rollback|update|vacuum)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AgentRecord:
    """A persisted execution agent roster entry."""

    id: int
    name: str
    agent_type: str
    status: str
    created_at: str
    updated_at: str
    last_used_at: str | None
    search_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_search_text(*values: str) -> str:
    """Normalize text for deterministic SQL filtering."""

    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        lowered = value.lower().replace("'s", "")
        for token in _TOKEN_PATTERN.findall(lowered):
            if token not in seen:
                seen.add(token)
                terms.append(token)
    return " ".join(terms)


class AgentRoster:
    """SQLite roster for execution agents."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.RLock()
        self.load()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load(self) -> None:
        """Ensure the roster database exists and has the expected schema."""

        with self._lock:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                self._ensure_schema(conn)
            finally:
                conn.close()

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = f"file:{self._db_path}?mode=ro"
            conn = sqlite3.connect(uri, timeout=30, isolation_level=None, uri=True)
        else:
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                agent_type TEXT NOT NULL DEFAULT 'general',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                search_text TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(agent_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_created_at ON agents(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_last_used_at ON agents(last_used_at)")

    def add_agent(self, agent_name: str, agent_type: str = "general") -> AgentRecord:
        """Create an agent if absent and return the roster record."""

        name = agent_name.strip()
        if not name:
            raise ValueError("Missing agent name")
        normalized_type = (agent_type or "general").strip() or "general"

        with self._lock:
            existing = self.get_agent_by_name(name)
            if existing is not None:
                return existing

            now = _utc_now()
            search_text = normalize_search_text(name, normalized_type)
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO agents(name, agent_type, status, created_at, updated_at, search_text)
                    VALUES (?, ?, 'active', ?, ?, ?)
                    """,
                    (name, normalized_type, now, now, search_text),
                )
                agent_id = int(cursor.lastrowid)
            finally:
                conn.close()

        record = self.get_agent(agent_id)
        if record is None:  # pragma: no cover - defensive
            raise RuntimeError("Failed to load newly created agent")
        self._schedule_agent_embedding(record.id)
        return record

    def get_agent(self, agent_id: int) -> AgentRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM agents WHERE id = ? LIMIT 1",
                    (agent_id,),
                ).fetchone()
                return _record_from_row(row) if row is not None else None
            finally:
                conn.close()

    def get_agent_by_name(self, agent_name: str) -> AgentRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM agents WHERE name = ? LIMIT 1",
                    (agent_name.strip(),),
                ).fetchone()
                return _record_from_row(row) if row is not None else None
            finally:
                conn.close()

    def list_agents(
        self,
        *,
        status: str | None = None,
        agent_type: str | None = None,
    ) -> list[AgentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if agent_type is not None:
            clauses.append("agent_type = ?")
            params.append(agent_type)

        sql = "SELECT * FROM agents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC"

        with self._lock:
            conn = self._connect()
            try:
                return [_record_from_row(row) for row in conn.execute(sql, params)]
            finally:
                conn.close()

    def touch_agent(self, agent_id: int) -> AgentRecord | None:
        """Update recency metadata for a dispatched agent."""

        now = _utc_now()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE agents SET last_used_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, agent_id),
                )
            finally:
                conn.close()
        return self.get_agent(agent_id)

    def query_readonly(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Run a guarded read-only query against the roster database."""

        query = _validate_readonly_sql(sql)
        bounded_limit = max(1, min(int(limit), 100))
        self.load()

        with self._lock:
            conn = self._connect(readonly=True)
            try:
                cursor = conn.execute(query, list(params or []))
                rows = cursor.fetchmany(bounded_limit + 1)
            finally:
                conn.close()

        truncated = len(rows) > bounded_limit
        return [dict(row) for row in rows[:bounded_limit]], truncated

    def clear(self) -> None:
        """Clear roster rows while keeping the database file and schema."""

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM agents")
            finally:
                conn.close()

    def _schedule_agent_embedding(self, agent_id: int) -> None:
        """Cache a new agent's embedding when roster updates happen in an event loop."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _embed_agent() -> None:
            try:
                from .agent_search import get_agent_search_index

                created = await get_agent_search_index().ensure_agent_embedding(agent_id)
                if created:
                    logger.info("Agent embedding cached", extra={"agent_id": agent_id})
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to cache agent embedding",
                    extra={"agent_id": agent_id, "error": str(exc)},
                )

        loop.create_task(_embed_agent())


def _record_from_row(row: sqlite3.Row) -> AgentRecord:
    return AgentRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        agent_type=str(row["agent_type"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_used_at=row["last_used_at"],
        search_text=str(row["search_text"]),
    )


def _validate_readonly_sql(sql: str) -> str:
    query = sql.strip()
    if not query:
        raise ValueError("Missing SQL query")
    if ";" in query:
        raise ValueError("SQL query must be a single statement without semicolons")
    if not re.match(r"^(select|with)\b", query, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed")
    if _DANGEROUS_SQL_PATTERN.search(query):
        raise ValueError("SQL query contains a disallowed keyword")
    return query


_agent_roster = AgentRoster(DEFAULT_AGENT_DB_PATH)


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""

    return _agent_roster


__all__ = [
    "AgentRecord",
    "AgentRoster",
    "DEFAULT_AGENT_DB_PATH",
    "get_agent_roster",
    "normalize_search_text",
]
