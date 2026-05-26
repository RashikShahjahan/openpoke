from __future__ import annotations

from pathlib import Path

import pytest

from server.messaging.context import (
    set_reply_target,
    subscribe,
    unsubscribe,
)
from server.messaging.types import ReplyTarget
from server.services.conversation.log import ConversationLog


@pytest.fixture
def log(tmp_path: Path) -> ConversationLog:
    return ConversationLog(tmp_path / "poke_conversation.log")


@pytest.fixture
def subscriber() -> list[tuple[str, ReplyTarget]]:
    calls: list[tuple[str, ReplyTarget]] = []

    def spy(content: str, target: ReplyTarget) -> None:
        calls.append((content, target))

    subscribe(spy)
    yield calls
    try:
        unsubscribe(spy)
    except ValueError:
        pass


@pytest.fixture
def active_target() -> ReplyTarget:
    target = ReplyTarget(source="signal", destination="+15551234567")
    set_reply_target(target)
    yield target
    set_reply_target(None)


class TestRecordReplyPublishing:
    def test_publishes_when_target_active(
        self,
        log: ConversationLog,
        subscriber: list[tuple[str, ReplyTarget]],
        active_target: ReplyTarget,
    ) -> None:
        log.record_reply("Hello from Signal")

        assert len(subscriber) == 1
        assert subscriber[0][0] == "Hello from Signal"
        assert subscriber[0][1] == active_target

    def test_does_not_publish_without_target(
        self,
        log: ConversationLog,
        subscriber: list[tuple[str, ReplyTarget]],
    ) -> None:
        set_reply_target(None)

        log.record_reply("Hello")

        assert subscriber == []

    def test_record_wait_does_not_publish(
        self,
        log: ConversationLog,
        subscriber: list[tuple[str, ReplyTarget]],
    ) -> None:
        log.record_wait("Already replied")

        assert subscriber == []

    def test_record_user_message_does_not_publish(
        self,
        log: ConversationLog,
        subscriber: list[tuple[str, ReplyTarget]],
    ) -> None:
        log.record_user_message("Hello from user")

        assert subscriber == []

    def test_record_agent_message_does_not_publish(
        self,
        log: ConversationLog,
        subscriber: list[tuple[str, ReplyTarget]],
    ) -> None:
        log.record_agent_message("Hello from agent")

        assert subscriber == []
