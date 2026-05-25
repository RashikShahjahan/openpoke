"""Vector search over execution agent names."""

from __future__ import annotations

import importlib.resources
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ...logging_config import logger
from ...openrouter_client import request_embeddings
from .roster import AgentRoster, get_agent_roster


DEFAULT_AGENT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_AGENT_SEARCH_MIN_SCORE = 0.35
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_INDEX_PATH = _DATA_DIR / "execution_agents" / "agent_embeddings.sqlite3"
_TABLE_NAME = "agent_embeddings"
_EMBEDDING_COLUMN = "embedding"


@dataclass(frozen=True)
class AgentSearchResult:
    """A matching execution agent name and similarity score."""

    agent_name: str
    score: float


class AgentSearchIndex:
    """Caches agent name embeddings in SQLite and ranks agents with sqlite-vector."""

    def __init__(
        self,
        index_path: Path,
        *,
        roster: AgentRoster | None = None,
        embedding_model: str = DEFAULT_AGENT_EMBEDDING_MODEL,
        min_score: float = DEFAULT_AGENT_SEARCH_MIN_SCORE,
    ) -> None:
        self._index_path = index_path
        self._roster = roster or get_agent_roster()
        self._embedding_model = embedding_model
        self._min_score = min_score
        self._lock = threading.Lock()

    async def search_agents(
        self,
        query: str,
        limit: int = 3,
        min_score: float | None = None,
    ) -> list[AgentSearchResult]:
        """Return the top matching execution agents for a query."""

        normalized_query = query.strip()
        if not normalized_query or limit <= 0:
            return []

        self._roster.load()
        agent_names = self._roster.get_agents()
        if not agent_names:
            return []

        query_embedding = (await self._embed_texts([normalized_query]))[0]
        query_blob = _embedding_to_blob(query_embedding)
        score_threshold = self._min_score if min_score is None else min_score
        self._sync_roster(agent_names)

        with self._lock:
            try:
                conn = self._connect()
                try:
                    self._initialize_vector(conn, len(query_embedding))
                    rows = list(conn.execute(
                        f"""
                        SELECT e.name, v.distance
                        FROM vector_full_scan(?, ?, vector_as_f32(?, ?), ?) AS v
                        JOIN {_TABLE_NAME} AS e ON e.rowid = v.rowid
                        WHERE e.model = ?
                        """,
                        (
                            _TABLE_NAME,
                            _EMBEDDING_COLUMN,
                            query_blob,
                            len(query_embedding),
                            limit,
                            self._embedding_model,
                        ),
                    ))
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning(f"Failed to search agent embeddings with sqlite-vector: {exc}")
                return []

        results = []
        for name, distance in rows:
            score = 1.0 - float(distance)
            if score >= score_threshold:
                results.append(AgentSearchResult(agent_name=str(name), score=score))
        results.sort(key=lambda result: result.score, reverse=True)
        return results

    async def ensure_agent_embedding(self, agent_name: str) -> bool:
        """Create and cache an embedding for an existing agent if missing."""

        normalized_name = agent_name.strip()
        if not normalized_name:
            return False

        self._roster.load()
        agent_names = self._roster.get_agents()
        if normalized_name not in agent_names:
            return False

        self._sync_roster(agent_names)
        if self._has_embedding(normalized_name):
            return False

        embedding = (await self._embed_texts([normalized_name]))[0]
        self._save_embedding(normalized_name, embedding)
        return True

    def _connect(self) -> Any:
        """Open SQLite and load sqlite-vector for this connection."""

        import apsw

        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        conn = apsw.Connection(str(self._index_path))
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
                name TEXT NOT NULL UNIQUE,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                embedding BLOB NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_model ON {_TABLE_NAME}(model)"
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

    def _sync_roster(self, valid_agent_names: Sequence[str]) -> None:
        """Keep the SQLite index scoped to the active roster and embedding model."""

        with self._lock:
            try:
                conn = self._connect()
                try:
                    names = list(valid_agent_names)
                    if names:
                        placeholders = ",".join("?" for _ in names)
                        conn.execute(
                            f"DELETE FROM {_TABLE_NAME} WHERE model != ? OR name NOT IN ({placeholders})",
                            (self._embedding_model, *names),
                        )
                    else:
                        conn.execute(f"DELETE FROM {_TABLE_NAME}")
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning(f"Failed to sync agent embedding index: {exc}")

    def _has_embedding(self, agent_name: str) -> bool:
        with self._lock:
            try:
                conn = self._connect()
                try:
                    row = next(conn.execute(
                        f"SELECT 1 FROM {_TABLE_NAME} WHERE name = ? AND model = ? LIMIT 1",
                        (agent_name, self._embedding_model),
                    ), None)
                    return row is not None
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning(f"Failed to check agent embedding index: {exc}")
                return False

    def _save_embedding(self, agent_name: str, embedding: Sequence[float]) -> None:
        self._save_embeddings([(agent_name, embedding)])

    def _save_embeddings(self, embeddings: Iterable[tuple[str, Sequence[float]]]) -> None:
        embeddings = list(embeddings)
        if not embeddings:
            return

        with self._lock:
            try:
                conn = self._connect()
                try:
                    dimension = len(embeddings[0][1])
                    self._initialize_vector(conn, dimension)
                    for agent_name, embedding in embeddings:
                        embedding_dimension = len(embedding)
                        if embedding_dimension != dimension:
                            raise ValueError("Embedding dimensions did not match")
                        conn.execute(
                            f"""
                            INSERT INTO {_TABLE_NAME}(name, model, dimension, embedding)
                            VALUES (?, ?, ?, vector_as_f32(?, ?))
                            ON CONFLICT(name) DO UPDATE SET
                                model = excluded.model,
                                dimension = excluded.dimension,
                                embedding = excluded.embedding
                            """,
                            (
                                agent_name,
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
                logger.warning(f"Failed to save agent embedding index: {exc}")

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

def _embedding_to_blob(embedding: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(embedding)}f", *embedding)


_agent_search_index = AgentSearchIndex(_INDEX_PATH)


def get_agent_search_index() -> AgentSearchIndex:
    """Get the singleton agent search index."""

    return _agent_search_index


__all__ = [
    "AgentSearchIndex",
    "AgentSearchResult",
    "DEFAULT_AGENT_EMBEDDING_MODEL",
    "DEFAULT_AGENT_SEARCH_MIN_SCORE",
    "get_agent_search_index",
]
