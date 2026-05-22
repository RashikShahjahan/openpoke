from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Callable

from .types import ReplyTarget

logger = logging.getLogger("openpoke.server.messaging")

_reply_target: ContextVar[ReplyTarget | None] = ContextVar(
    "_reply_target", default=None
)
_subscribers: list[Callable[[str, ReplyTarget], None]] = []


def set_reply_target(target: ReplyTarget | None) -> None:
    _reply_target.set(target)


def get_reply_target() -> ReplyTarget | None:
    return _reply_target.get()


def publish_reply(content: str) -> None:
    target = get_reply_target()
    if target is not None:
        for subscriber in _subscribers:
            try:
                subscriber(content, target)
            except Exception:
                logger.exception("Reply subscriber failed")


def subscribe(callback: Callable[[str, ReplyTarget], None]) -> None:
    _subscribers.append(callback)


def unsubscribe(callback: Callable[[str, ReplyTarget], None]) -> None:
    _subscribers.remove(callback)
