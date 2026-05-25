"""Simplified configuration management."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


class Settings(BaseModel):
    """Application settings."""

    # LLM API and model selection
    llm_api_base_url: Optional[str] = Field(
        default=os.getenv("OPENPOKE_LLM_BASE_URL")
    )
    interaction_agent_model: Optional[str] = Field(default=os.getenv("OPENPOKE_INTERACTION_AGENT_MODEL"))
    execution_agent_model: Optional[str] = Field(default=os.getenv("OPENPOKE_EXECUTION_AGENT_MODEL"))
    execution_agent_search_model: Optional[str] = Field(default=os.getenv("OPENPOKE_EXECUTION_AGENT_SEARCH_MODEL"))
    summarizer_model: Optional[str] = Field(default=os.getenv("OPENPOKE_SUMMARIZER_MODEL"))
    embeddings_api_base_url: Optional[str] = Field(
        default=os.getenv("OPENPOKE_EMBEDDINGS_BASE_URL")
    )

    # Credentials / integrations
    openrouter_api_key: Optional[str] = Field(
        default=os.getenv("OPENROUTER_API_KEY")
    )
    embeddings_api_key: Optional[str] = Field(
        default=os.getenv("OPENPOKE_EMBEDDINGS_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )

    # Signal messaging integration
    signal_http_url: str = Field(
        default=os.getenv("OPENPOKE_SIGNAL_HTTP_URL", "http://127.0.0.1:8080")
    )
    signal_account: Optional[str] = Field(default=os.getenv("OPENPOKE_SIGNAL_ACCOUNT"))
    signal_allowed_senders_raw: str = Field(
        default=os.getenv("OPENPOKE_SIGNAL_ALLOWED_SENDERS", "")
    )

    # Local read-only calendar integration
    calendar_ics_path: Optional[str] = Field(default=os.getenv("OPENPOKE_CALENDAR_ICS_PATH"))
    calendar_refresh_seconds: int = Field(
        default=int(os.getenv("OPENPOKE_CALENDAR_REFRESH_SECONDS", "60"))
    )

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=100)
    conversation_summary_tail_size: int = Field(default=10)

    @property
    def signal_allowed_senders(self) -> List[str]:
        """Parse Signal sender allowlist from comma-separated phone numbers."""
        return [
            sender.strip()
            for sender in self.signal_allowed_senders_raw.split(",")
            if sender.strip()
        ]

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
