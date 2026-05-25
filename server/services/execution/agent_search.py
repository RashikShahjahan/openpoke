"""Vector search over SQLite-backed execution agents."""

from __future__ import annotations

import importlib.resources
import struct
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from ...logging_config import logger
from ...openrouter_client import request_embeddings
from .roster import AgentRecord, AgentRoster, DEFAULT_AGENT_DB_PATH, get_agent_roster


DEFAULT_AGENT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_TABLE_NAME = "agent_embeddings"
_EMBEDDING_COLUMN = "embedding"


class AgentSearchIndex:
    """Caches agent embeddings in SQLite and ranks agents with sqlite-vector."""

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
            try:
                conn = self._connect()
                try:
                    self._initialize_vector(conn, len(query_embedding))
                    placeholders = ",".join("?" for _ in candidate_ids)
                    rows = list(
                        conn.execute(
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
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Failed to search agent embeddings", extra={"error": str(exc)})
                return []

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

    def _connect(self) -> Any:
        """Open SQLite and load sqlite-vector for this connection."""

        import apsw

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = apsw.Connection(str(self._db_path))
        conn.setbusytimeout(30_000)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        ext_path = importlib.resources.files("sqlite_vector.binaries") / "vector"
        conn.enableloadextension(True)
        try:
            conn.loadextension(str(ext_path))
            conn.enableloadextension(False)
            self._ensure_schema(conn)
            return conn
        except Exception:
            conn.close()
            raise
        finally:
            try:
                conn.enableloadextension(False)
            except Exception:
                pass

    def _ensure_schema(self, conn: Any) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                id INTEGER PRIMARY KEY,
                agent_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_model ON {_TABLE_NAME}(model)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_agent_id ON {_TABLE_NAME}(agent_id)"
        )

    def _initialize_vector(self, conn: Any, dimension: int) -> None:
        conn.execute(
            "SELECT vector_init(?, ?, ?)",
            (
                _TABLE_NAME,
                _EMBEDDING_COLUMN,
                f"type=FLOAT32,dimension={dimension},distance=COSINE",
            ),
        )

    def _has_embedding(self, agent_id: int) -> bool:
        with self._lock:
            try:
                conn = self._connect()
                try:
                    row = next(
                        conn.execute(
                            f"SELECT 1 FROM {_TABLE_NAME} WHERE agent_id = ? AND model = ? LIMIT 1",
                            (agent_id, self._embedding_model),
                        ),
                        None,
                    )
                    return row is not None
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Failed to check agent embedding", extra={"error": str(exc)})
                return False

    def _embedding_count(self) -> int:
        with self._lock:
            try:
                conn = self._connect()
                try:
                    row = next(
                        conn.execute(
                            f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE model = ?",
                            (self._embedding_model,),
                        ),
                        None,
                    )
                    return int(row[0]) if row is not None else 0
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Failed to count agent embeddings", extra={"error": str(exc)})
                return 0

    def _save_embeddings(self, embeddings: Iterable[tuple[int, Sequence[float]]]) -> None:
        embeddings = list(embeddings)
        if not embeddings:
            return

        with self._lock:
            try:
                conn = self._connect()
                try:
                    dimension = len(embeddings[0][1])
                    self._initialize_vector(conn, dimension)
                    for agent_id, embedding in embeddings:
                        embedding_dimension = len(embedding)
                        if embedding_dimension != dimension:
                            raise ValueError("Embedding dimensions did not match")
                        conn.execute(
                            f"""
                            INSERT INTO {_TABLE_NAME}(agent_id, model, dimension, embedding, updated_at)
                            VALUES (?, ?, ?, vector_as_f32(?, ?), CURRENT_TIMESTAMP)
                            ON CONFLICT(agent_id) DO UPDATE SET
                                model = excluded.model,
                                dimension = excluded.dimension,
                                embedding = excluded.embedding,
                                updated_at = excluded.updated_at
                            """,
                            (
                                agent_id,
                                self._embedding_model,
                                embedding_dimension,
                                _embedding_to_blob(embedding),
                                embedding_dimension,
                            ),
                        )
                    conn.execute("SELECT vector_quantize(?, ?)", (_TABLE_NAME, _EMBEDDING_COLUMN))
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Failed to save agent embeddings", extra={"error": str(exc)})

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
        for fallback_index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            index = item.get("index", fallback_index)
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


_agent_search_index = AgentSearchIndex(DEFAULT_AGENT_DB_PATH)


def get_agent_search_index() -> AgentSearchIndex:
    """Get the singleton agent vector search index."""

    return _agent_search_index



__all__ = [
    "AgentSearchIndex",
    "DEFAULT_AGENT_EMBEDDING_MODEL",
    "get_agent_search_index",
]
