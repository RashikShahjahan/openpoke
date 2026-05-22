from __future__ import annotations

import asyncio

import pytest

from server.messaging.context import (
    get_reply_target,
    publish_reply,
    set_reply_target,
    subscribe,
    unsubscribe,
)
from server.messaging.types import ReplyTarget


def test_get_set_reply_target() -> None:
    target = ReplyTarget(source="signal", destination="+111")
    set_reply_target(target)
    assert get_reply_target() == target


def test_get_defaults_to_none() -> None:
    set_reply_target(None)
    assert get_reply_target() is None


def test_publish_reply_noops_without_active_target() -> None:
    calls: list[tuple[str, ReplyTarget]] = []

    def subscriber(content: str, target: ReplyTarget) -> None:
        calls.append((content, target))

    subscribe(subscriber)
    set_reply_target(None)
    publish_reply("hello")
    assert calls == []
    unsubscribe(subscriber)


def test_publish_reply_delivers_when_target_active() -> None:
    target = ReplyTarget(source="signal", destination="+111")
    calls: list[tuple[str, ReplyTarget]] = []

    def subscriber(content: str, tgt: ReplyTarget) -> None:
        calls.append((content, tgt))

    subscribe(subscriber)
    set_reply_target(target)
    publish_reply("hello")
    assert calls == [("hello", target)]
    unsubscribe(subscriber)


def test_subscribe_multiple_subscribers() -> None:
    target = ReplyTarget(source="signal", destination="+111")
    calls1: list[str] = []
    calls2: list[str] = []

    def sub1(content: str, tgt: ReplyTarget) -> None:
        calls1.append(content)

    def sub2(content: str, tgt: ReplyTarget) -> None:
        calls2.append(content)

    subscribe(sub1)
    subscribe(sub2)
    set_reply_target(target)
    publish_reply("hello")
    assert calls1 == ["hello"]
    assert calls2 == ["hello"]
    unsubscribe(sub1)
    unsubscribe(sub2)


def test_unsubscribe_removes_callback() -> None:
    target = ReplyTarget(source="signal", destination="+111")
    calls: list[str] = []

    def subscriber(content: str, tgt: ReplyTarget) -> None:
        calls.append(content)

    subscribe(subscriber)
    unsubscribe(subscriber)
    set_reply_target(target)
    publish_reply("hello")
    assert calls == []


def test_unsubscribe_missing_callback_raises() -> None:
    def subscriber(content: str, tgt: ReplyTarget) -> None:
        pass

    with pytest.raises(ValueError):
        unsubscribe(subscriber)


def test_subscriber_error_does_not_block_others() -> None:
    target = ReplyTarget(source="signal", destination="+111")
    calls: list[str] = []

    def failing_sub(content: str, tgt: ReplyTarget) -> None:
        raise RuntimeError("subscriber failed")

    def good_sub(content: str, tgt: ReplyTarget) -> None:
        calls.append(content)

    subscribe(failing_sub)
    subscribe(good_sub)
    set_reply_target(target)

    publish_reply("hello")

    assert calls == ["hello"]
    unsubscribe(failing_sub)
    unsubscribe(good_sub)


async def _scoping_test() -> None:
    target_a = ReplyTarget(source="signal", destination="+111")
    target_b = ReplyTarget(source="signal", destination="+222")

    set_reply_target(target_a)
    assert get_reply_target() == target_a

    async def task_b() -> None:
        set_reply_target(target_b)
        assert get_reply_target() == target_b
        await asyncio.sleep(0)
        assert get_reply_target() == target_b

    task = asyncio.create_task(task_b())
    await asyncio.sleep(0)
    assert get_reply_target() == target_a
    await task
    assert get_reply_target() == target_a


def test_reply_target_context_is_scoped_per_task() -> None:
    asyncio.run(_scoping_test())
