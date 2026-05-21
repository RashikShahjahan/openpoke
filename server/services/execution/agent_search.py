"""Vector search over execution agent names."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ...logging_config import logger
from ...openrouter_client import request_embeddings
from .roster import AgentRoster, get_agent_roster


DEFAULT_AGENT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_AGENT_SEARCH_MIN_SCORE = 0.35
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_INDEX_PATH = _DATA_DIR / "execution_agents" / "agent_embeddings.json"


@dataclass(frozen=True)
class AgentSearchResult:
    """A matching execution agent name and similarity score."""

    agent_name: str
    score: float


class AgentSearchIndex:
    """Caches agent name embeddings and ranks agents by query similarity."""

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

        index = self._load_index()
        embeddings = self._extract_cached_embeddings(index)
        agent_name_set = set(agent_names)
        embeddings = {
            name: embedding
            for name, embedding in embeddings.items()
            if name in agent_name_set
        }

        query_embedding = (await self._embed_texts([normalized_query]))[0]
        score_threshold = self._min_score if min_score is None else min_score
        results: list[AgentSearchResult] = []
        for name, embedding in embeddings.items():
            if name not in agent_name_set:
                continue
            score = _cosine_similarity(query_embedding, embedding)
            if score >= score_threshold:
                results.append(AgentSearchResult(agent_name=name, score=score))

        results.sort(key=lambda result: result.score, reverse=True)
        return results[:limit]

    async def ensure_agent_embedding(self, agent_name: str) -> bool:
        """Create and cache an embedding for an existing agent if missing."""

        normalized_name = agent_name.strip()
        if not normalized_name:
            return False

        self._roster.load()
        agent_names = self._roster.get_agents()
        if normalized_name not in agent_names:
            return False

        index = self._load_index()
        embeddings = self._extract_cached_embeddings(index)
        if normalized_name in embeddings:
            return False

        embedding = (await self._embed_texts([normalized_name]))[0]
        embeddings[normalized_name] = embedding
        self._save_embeddings(embeddings, valid_agent_names=agent_names)
        return True

    def _load_index(self) -> dict[str, Any]:
        """Load the persisted embedding index."""

        with self._lock:
            try:
                if not self._index_path.exists():
                    return {}
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                logger.warning(f"Failed to load agent embedding index: {exc}")
                return {}

    def _extract_cached_embeddings(self, index: dict[str, Any]) -> dict[str, list[float]]:
        """Return cached embeddings if they match the current model."""

        if index.get("model") != self._embedding_model:
            return {}

        raw_agents = index.get("agents")
        if not isinstance(raw_agents, dict):
            return {}

        embeddings: dict[str, list[float]] = {}
        for name, raw_embedding in raw_agents.items():
            if (
                isinstance(name, str)
                and isinstance(raw_embedding, list)
                and raw_embedding
                and all(isinstance(value, (int, float)) for value in raw_embedding)
            ):
                embeddings[name] = raw_embedding
        return embeddings

    def _save_embeddings(
        self,
        embeddings: dict[str, list[float]],
        *,
        valid_agent_names: Sequence[str],
    ) -> None:
        """Persist embeddings for currently active agents only."""

        valid_agent_name_set = set(valid_agent_names)
        payload = {
            "model": self._embedding_model,
            "agents": {
                name: embedding
                for name, embedding in embeddings.items()
                if name in valid_agent_name_set
            },
        }

        with self._lock:
            try:
                self._index_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self._index_path.with_suffix(".tmp")
                temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                temp_path.replace(self._index_path)
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

def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0

    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


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
