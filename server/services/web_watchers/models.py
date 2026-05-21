from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, HttpUrl


class WebWatcherRecord(BaseModel):
    """Serialized web watcher representation returned to callers."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    condition: str
    cadence_rule: Optional[str] = None
    status: str
    last_snapshot_hash: Optional[str] = None
    last_snapshot_summary: Optional[str] = None
    last_checked_at: Optional[str] = None
    last_notified_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str


class WebWatcherCreate(BaseModel):
    """Inputs needed to create a watcher and capture its initial snapshot."""

    name: str
    url: HttpUrl
    condition: str
    cadence_rule: Optional[str] = None
    status: str = "active"


class WebPageSnapshot(BaseModel):
    """Cleaned page snapshot used as the watcher baseline."""

    url: str
    title: Optional[str] = None
    content: str
    content_hash: str
    fetched_at: str


class WebWatcherEvaluation(BaseModel):
    """LLM decision about whether a watcher update should notify the user."""

    changed: bool
    relevant: bool
    summary: Optional[str] = None
    evidence: Optional[str] = None
    new_snapshot_summary: str


__all__ = [
    "WebPageSnapshot",
    "WebWatcherCreate",
    "WebWatcherEvaluation",
    "WebWatcherRecord",
]
