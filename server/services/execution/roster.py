"""SQLAlchemy-backed execution agent roster."""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, RowMapping

from ...logging_config import logger


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_AGENT_DB_PATH = _DATA_DIR / "execution_agents" / "agents.sqlite3"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(attach|alter|analyze|begin|commit|create|delete|detach|drop|insert|pragma|replace|"
    r"reindex|rollback|update|vacuum)\b",
    re.IGNORECASE,
)

_metadata = MetaData()
_agents = Table(
    "agents",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("agent_type", Text, nullable=False, server_default="general"),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("last_used_at", Text),
    Column("search_text", Text, nullable=False),
    Index("idx_agents_type", "agent_type"),
    Index("idx_agents_status", "status"),
    Index("idx_agents_created_at", "created_at"),
    Index("idx_agents_last_used_at", "last_used_at"),
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
    """Persistent roster for execution agents."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._engine = _create_engine(db_path)
        self._lock = threading.RLock()
        self.load()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load(self) -> None:
        """Ensure the roster database exists and has the expected schema."""

        with self._lock:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            _metadata.create_all(self._engine, tables=[_agents])

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
            with self._engine.begin() as conn:
                result = conn.execute(
                    insert(_agents).values(
                        name=name,
                        agent_type=normalized_type,
                        status="active",
                        created_at=now,
                        updated_at=now,
                        search_text=search_text,
                    )
                )
                agent_id = int(result.inserted_primary_key[0])

        record = self.get_agent(agent_id)
        if record is None:  # pragma: no cover - defensive
            raise RuntimeError("Failed to load newly created agent")
        self._schedule_agent_embedding(record.id)
        return record

    def get_agent(self, agent_id: int) -> AgentRecord | None:
        with self._lock:
            with self._engine.connect() as conn:
                row = conn.execute(select(_agents).where(_agents.c.id == agent_id).limit(1)).mappings().first()
            return _record_from_row(row) if row is not None else None

    def get_agent_by_name(self, agent_name: str) -> AgentRecord | None:
        with self._lock:
            with self._engine.connect() as conn:
                row = conn.execute(select(_agents).where(_agents.c.name == agent_name.strip()).limit(1)).mappings().first()
            return _record_from_row(row) if row is not None else None

    def list_agents(
        self,
        *,
        status: str | None = None,
        agent_type: str | None = None,
    ) -> list[AgentRecord]:
        query = select(_agents)
        if status is not None:
            query = query.where(_agents.c.status == status)
        if agent_type is not None:
            query = query.where(_agents.c.agent_type == agent_type)
        query = query.order_by(text("COALESCE(last_used_at, created_at) DESC"), _agents.c.id.desc())

        with self._lock:
            with self._engine.connect() as conn:
                rows = conn.execute(query).mappings().all()
            return [_record_from_row(row) for row in rows]

    def touch_agent(self, agent_id: int) -> AgentRecord | None:
        """Update recency metadata for a dispatched agent."""

        now = _utc_now()
        with self._lock:
            with self._engine.begin() as conn:
                conn.execute(
                    update(_agents)
                    .where(_agents.c.id == agent_id)
                    .values(last_used_at=now, updated_at=now)
                )
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
            with self._engine.connect() as conn:
                result = conn.exec_driver_sql(query, tuple(params or []))
                rows = result.mappings().fetchmany(bounded_limit + 1)

        truncated = len(rows) > bounded_limit
        return [dict(row) for row in rows[:bounded_limit]], truncated

    def clear(self) -> None:
        """Clear roster rows while keeping the database file and schema."""

        with self._lock:
            with self._engine.begin() as conn:
                conn.execute(delete(_agents))

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


def _create_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def _record_from_row(row: RowMapping) -> AgentRecord:
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
