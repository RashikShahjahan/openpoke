from __future__ import annotations

import asyncio
from typing import Any

from server.messaging.context import get_reply_target, publish_reply, set_reply_target
from server.messaging.gateway import MessagingGateway
from server.messaging.types import InboundMessage, ReplyTarget


class FakeProcessor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.targets: list[ReplyTarget | None] = []

    async def process_user_message(self, user_message: str) -> None:
        self.calls.append(user_message)
        self.targets.append(get_reply_target())


class FakeSignalAdapter:
    source = "signal"

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.listen_started = asyncio.Event()
        self.listen_stopped = asyncio.Event()
        self.closed = False
        self.sent: list[tuple[ReplyTarget, str]] = []

    async def health_check(self) -> bool:
        return self.healthy

    async def listen(self) -> None:
        self.listen_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.listen_stopped.set()

    async def close(self) -> None:
        self.closed = True

    async def send(self, target: ReplyTarget, content: str) -> None:
        self.sent.append((target, content))


def test_gateway_does_not_start_without_signal_adapter() -> None:
    async def run_test() -> None:
        gateway = MessagingGateway(signal_adapter=None, processor=FakeProcessor())

        await gateway.start()
        await gateway.stop()

    asyncio.run(run_test())


def test_gateway_starts_signal_when_healthy() -> None:
    async def run_test() -> None:
        adapter = FakeSignalAdapter()
        gateway = MessagingGateway(signal_adapter=adapter, processor=FakeProcessor())

        await gateway.start()
        await asyncio.wait_for(adapter.listen_started.wait(), timeout=1)
        await gateway.stop()

        assert adapter.closed is True
        assert adapter.listen_stopped.is_set()

    asyncio.run(run_test())


def test_gateway_start_survives_signal_daemon_unavailable() -> None:
    async def run_test() -> None:
        adapter = FakeSignalAdapter(healthy=False)
        gateway = MessagingGateway(signal_adapter=adapter, processor=FakeProcessor())

        await gateway.start()
        await asyncio.sleep(0)
        await gateway.stop()

        assert not adapter.listen_started.is_set()
        assert adapter.closed is True

    asyncio.run(run_test())


def test_signal_inbound_sets_reply_target() -> None:
    async def run_test() -> None:
        processor = FakeProcessor()
        gateway = MessagingGateway(signal_adapter=FakeSignalAdapter(), processor=processor)

        await gateway.handle_inbound(
            InboundMessage(source="signal", sender="+200", text="hello")
        )

        assert processor.calls == ["hello"]
        assert processor.targets == [ReplyTarget(source="signal", destination="+200")]
        assert get_reply_target() is None

    asyncio.run(run_test())


def test_signal_reply_is_delivered_to_originating_sender() -> None:
    async def run_test() -> None:
        adapter = FakeSignalAdapter()
        gateway = MessagingGateway(signal_adapter=adapter, processor=FakeProcessor())
        await gateway.start()

        set_reply_target(ReplyTarget(source="signal", destination="+200"))
        publish_reply("hi")
        set_reply_target(None)
        await asyncio.sleep(0)
        await gateway.stop()

        assert adapter.sent == [(ReplyTarget(source="signal", destination="+200"), "hi")]

    asyncio.run(run_test())


def test_existing_http_chat_does_not_trigger_signal_send() -> None:
    async def run_test() -> None:
        adapter = FakeSignalAdapter()
        gateway = MessagingGateway(signal_adapter=adapter, processor=FakeProcessor())
        await gateway.start()

        publish_reply("web reply")
        await asyncio.sleep(0)
        await gateway.stop()

        assert adapter.sent == []

    asyncio.run(run_test())
