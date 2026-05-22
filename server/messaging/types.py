from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MessagingSource = str


@dataclass(frozen=True)
class ReplyTarget:
    source: MessagingSource
    destination: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if not self.destination:
            raise ValueError("destination is required")


@dataclass(frozen=True)
class InboundMessage:
    source: MessagingSource
    sender: str
    text: str
    raw: Any = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if not self.text:
            raise ValueError("text is required")
