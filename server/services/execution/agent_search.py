"""Vector search over persisted execution agents."""

from __future__ import annotations

import importlib.resources
import struct
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import (
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from ...openrouter_client import request_embeddings
from .roster import AgentRecord, AgentRoster, DEFAULT_AGENT_DB_PATH, get_agent_roster


DEFAULT_AGENT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_TABLE_NAME = "agent_embeddings"
_EMBEDDING_COLUMN = "embedding"

_metadata = MetaData()
_agent_embeddings = Table(
    _TABLE_NAME,
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("agent_id", Integer, nullable=False, unique=True),
    Column("model", Text, nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("embedding", LargeBinary, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=func.current_timestamp()),
    Index(f"idx_{_TABLE_NAME}_model", "model"),
    Index(f"idx_{_TABLE_NAME}_agent_id", "agent_id"),
)


class AgentSearchIndex:
    """Caches agent embeddings in SQLite and ranks agents with sqliteai-vector."""

    def __init__(
        self,
        db_path: Path = DEFAULT_AGENT_DB_PATH,
        *,
        roster: AgentRoster | None = None,
        embedding_model: str = DEFAULT_AGENT_EMBEDDING_MODEL,
    ) -> None:
        self._db_path = db_path
        self._roster = roster or get_agent_roster()
        self._embedding_model = embedding_model
        self._engine = _create_vector_engine(db_path)
        self._lock = threading.Lock()

    async def vector_search_agents(
        self,
        query: str,
        *,
        limit: int = 5,
        agent_ids: Sequence[int] | None = None,
    ) -> list[AgentRecord]:
        """Return agents ranked by semantic similarity to the query."""

        normalized_query = query.strip()
        if not normalized_query or limit <= 0:
            return []

        candidates = self._candidate_records(agent_ids)
        if not candidates:
            return []

        await self._ensure_embeddings(candidates)
        query_embedding = (await self._embed_texts([normalized_query]))[0]
        query_blob = _embedding_to_blob(query_embedding)
        candidate_ids = [record.id for record in candidates]
        scan_limit = max(1, self._embedding_count(), len(candidate_ids))

        with self._lock:
            with self._engine.connect() as conn:
                self._ensure_schema()
                self._initialize_vector(conn, len(query_embedding))
                placeholders = ",".join("?" for _ in candidate_ids)
                rows = list(
                    conn.exec_driver_sql(
                        f"""
                        SELECT e.agent_id, v.distance
                        FROM vector_full_scan(?, ?, vector_as_f32(?, ?), ?) AS v
                        JOIN {_TABLE_NAME} AS e ON e.rowid = v.rowid
                        JOIN agents AS a ON a.id = e.agent_id
                        WHERE e.model = ?
                          AND a.status = 'active'
                          AND e.agent_id IN ({placeholders})
                        """,
                        (
                            _TABLE_NAME,
                            _EMBEDDING_COLUMN,
                            query_blob,
                            len(query_embedding),
                            scan_limit,
                            self._embedding_model,
                            *candidate_ids,
                        ),
                    )
                )

        records_by_id = {record.id: record for record in candidates}
        ordered: list[AgentRecord] = []
        for agent_id, _distance in sorted(rows, key=lambda row: float(row[1])):
            record = records_by_id.get(int(agent_id))
            if record is not None:
                ordered.append(record)
            if len(ordered) >= limit:
                break
        return ordered

    async def ensure_agent_embedding(self, agent_id: int) -> bool:
        """Create and cache an embedding for an existing agent if missing."""

        record = self._roster.get_agent(agent_id)
        if record is None or record.status != "active":
            return False
        if self._has_embedding(record.id):
            return False

        embedding = (await self._embed_texts([_embedding_text(record)]))[0]
        self._save_embeddings([(record.id, embedding)])
        return True

    def _candidate_records(self, agent_ids: Sequence[int] | None) -> list[AgentRecord]:
        self._roster.load()
        if agent_ids is None:
            return self._roster.list_agents(status="active")

        records: list[AgentRecord] = []
        seen: set[int] = set()
        for raw_id in agent_ids:
            try:
                agent_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if agent_id in seen:
                continue
            seen.add(agent_id)
            record = self._roster.get_agent(agent_id)
            if record is not None and record.status == "active":
                records.append(record)
        return records

    async def _ensure_embeddings(self, records: Sequence[AgentRecord]) -> None:
        missing = [record for record in records if not self._has_embedding(record.id)]
        if not missing:
            return

        embeddings = await self._embed_texts([_embedding_text(record) for record in missing])
        self._save_embeddings(
            (record.id, embedding) for record, embedding in zip(missing, embeddings)
        )

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _metadata.create_all(self._engine, tables=[_agent_embeddings])

    def _initialize_vector(self, conn: Any, dimension: int) -> None:
        conn.exec_driver_sql(
            "SELECT vector_init(?, ?, ?)",
            (
                _TABLE_NAME,
                _EMBEDDING_COLUMN,
                f"type=FLOAT32,dimension={dimension},distance=COSINE",
            ),
        )

    def _has_embedding(self, agent_id: int) -> bool:
        with self._lock:
            self._ensure_schema()
            with self._engine.connect() as conn:
                row = conn.execute(
                    select(_agent_embeddings.c.id).where(
                        _agent_embeddings.c.agent_id == agent_id,
                        _agent_embeddings.c.model == self._embedding_model,
                    ).limit(1)
                ).first()
            return row is not None

    def _embedding_count(self) -> int:
        with self._lock:
            self._ensure_schema()
            with self._engine.connect() as conn:
                row = conn.execute(
                    select(func.count()).select_from(_agent_embeddings).where(
                        _agent_embeddings.c.model == self._embedding_model
                    )
                ).one()
            return int(row[0])

    def _save_embeddings(self, embeddings: Iterable[tuple[int, Sequence[float]]]) -> None:
        embeddings = list(embeddings)
        if not embeddings:
            return

        with self._lock:
            self._ensure_schema()
            with self._engine.begin() as conn:
                dimension = len(embeddings[0][1])
                self._initialize_vector(conn, dimension)
                for agent_id, embedding in embeddings:
                    embedding_dimension = len(embedding)
                    if embedding_dimension != dimension:
                        raise ValueError("Embedding dimensions did not match")
                    embedding_blob = _embedding_to_blob(embedding)
                    conn.execute(
                        sqlite_insert(_agent_embeddings)
                        .values(
                            agent_id=agent_id,
                            model=self._embedding_model,
                            dimension=embedding_dimension,
                            embedding=func.vector_as_f32(embedding_blob, embedding_dimension),
                        )
                        .on_conflict_do_update(
                            index_elements=[_agent_embeddings.c.agent_id],
                            set_={
                                "model": self._embedding_model,
                                "dimension": embedding_dimension,
                                "embedding": func.vector_as_f32(embedding_blob, embedding_dimension),
                                "updated_at": func.current_timestamp(),
                            },
                        )
                    )
                conn.exec_driver_sql("SELECT vector_quantize(?, ?)", (_TABLE_NAME, _EMBEDDING_COLUMN))

    async def _embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed one or more texts using OpenRouter."""

        response = await request_embeddings(
            model=self._embedding_model,
            input=list(texts),
        )
        raw_items = response.get("data") or []
        if not isinstance(raw_items, list):
            raise ValueError("Embedding response data was not a list")

        indexed_embeddings: dict[int, list[float]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            embedding = item.get("embedding")
            if (
                isinstance(index, int)
                and isinstance(embedding, list)
                and embedding
                and all(isinstance(value, (int, float)) for value in embedding)
            ):
                indexed_embeddings[index] = embedding

        embeddings = [indexed_embeddings[index] for index in range(len(texts)) if index in indexed_embeddings]
        if len(embeddings) != len(texts):
            raise ValueError("Embedding response did not include all requested inputs")
        return embeddings


def _embedding_text(record: AgentRecord) -> str:
    return f"{record.name}\nType: {record.agent_type}\nSearch text: {record.search_text}"


def _embedding_to_blob(embedding: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(embedding)}f", *embedding)


def _create_vector_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 30})
    ext_path = _sqlite_vector_extension_path()

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

        dbapi_connection.enable_load_extension(True)
        try:
            dbapi_connection.load_extension(str(ext_path))
        finally:
            dbapi_connection.enable_load_extension(False)

    return engine


def _sqlite_vector_extension_path() -> Path:
    binaries = importlib.resources.files("sqlite_vector.binaries")
    for candidate in ("vector", "vector.dylib", "vector.so", "vector.dll"):
        path = binaries / candidate
        if path.is_file():
            return Path(str(path))
    raise FileNotFoundError("Could not find sqlite_vector extension binary")


_agent_search_index = AgentSearchIndex(DEFAULT_AGENT_DB_PATH)


def get_agent_search_index() -> AgentSearchIndex:
    """Get the singleton agent vector search index."""

    return _agent_search_index



__all__ = [
    "AgentSearchIndex",
    "DEFAULT_AGENT_EMBEDDING_MODEL",
    "get_agent_search_index",
]
