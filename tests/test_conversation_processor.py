from __future__ import annotations

import asyncio

import pytest

from server.services.conversation.processor import ConversationProcessor


class FakeRuntime:
    def __init__(self, calls: list[str], release: asyncio.Event | None = None) -> None:
        self._calls = calls
        self._release = release

    async def execute(self, user_message: str) -> str:
        self._calls.append(user_message)
        if self._release is not None:
            await self._release.wait()
        return "ok"


def test_processor_calls_interaction_runtime_with_user_text() -> None:
    calls: list[str] = []
    processor = ConversationProcessor(runtime_factory=lambda: FakeRuntime(calls))

    result = asyncio.run(processor.process_user_message("  hello  "))

    assert result == "ok"
    assert calls == ["hello"]


def test_processor_rejects_empty_user_message() -> None:
    processor = ConversationProcessor(runtime_factory=lambda: FakeRuntime([]))

    with pytest.raises(ValueError, match="Missing user message"):
        asyncio.run(processor.process_user_message("   "))


def test_concurrent_processor_calls_can_overlap() -> None:
    async def run_test() -> None:
        calls: list[str] = []
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        runtime_releases = [release_first, release_second]

        def runtime_factory() -> FakeRuntime:
            return FakeRuntime(calls, runtime_releases.pop(0))

        processor = ConversationProcessor(runtime_factory=runtime_factory)

        first = asyncio.create_task(processor.process_user_message("first"))
        await asyncio.sleep(0)
        second = asyncio.create_task(processor.process_user_message("second"))
        await asyncio.sleep(0)

        assert calls == ["first", "second"]

        release_first.set()
        release_second.set()
        assert await first == "ok"
        assert await second == "ok"

    asyncio.run(run_test())


def test_processor_can_use_prebuilt_runtime() -> None:
    calls: list[str] = []
    processor = ConversationProcessor(runtime_factory=lambda: FakeRuntime([]))
    runtime = FakeRuntime(calls)

    result = asyncio.run(processor.process_user_message("hello", runtime=runtime))

    assert result == "ok"
    assert calls == ["hello"]
