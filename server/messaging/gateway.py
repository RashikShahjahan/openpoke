from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Optional

from ..config import Settings, get_settings
from ..logging_config import logger
from ..services.conversation.processor import ConversationProcessor, get_conversation_processor
from .context import set_reply_target, subscribe, unsubscribe
from .signal import SignalAdapter
from .types import InboundMessage, ReplyTarget


class MessagingGateway:
    """Coordinates external messaging adapters with the shared conversation processor."""

    def __init__(
        self,
        signal_adapter: Optional[SignalAdapter] = None,
        processor: Optional[ConversationProcessor] = None,
    ) -> None:
        self.signal_adapter = signal_adapter
        self.processor = processor or get_conversation_processor()
        self._signal_task: Optional[asyncio.Task[None]] = None
        self._subscribed = False

        if self.signal_adapter is not None:
            self.signal_adapter.on_message = self.handle_inbound

    async def start(self) -> None:
        if self.signal_adapter is None:
            return

        if not await self.signal_adapter.health_check():
            logger.warning("Signal daemon unavailable; messaging gateway continuing without Signal")
            await self.signal_adapter.close()
            return

        if not self._subscribed:
            subscribe(self.deliver_reply)
            self._subscribed = True
        self._signal_task = asyncio.create_task(self.signal_adapter.listen(), name="signal-listener")

    async def stop(self) -> None:
        if self._subscribed:
            with suppress(ValueError):
                unsubscribe(self.deliver_reply)
            self._subscribed = False

        if self._signal_task is not None:
            self._signal_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._signal_task
            self._signal_task = None

        if self.signal_adapter is not None:
            await self.signal_adapter.close()

    async def handle_inbound(self, message: InboundMessage) -> None:
        target = ReplyTarget(source=message.source, destination=message.sender)
        set_reply_target(target)
        try:
            await self.processor.process_user_message(message.text)
        finally:
            set_reply_target(None)

    def deliver_reply(self, content: str, target: ReplyTarget) -> None:
        if target.source != SignalAdapter.source or self.signal_adapter is None:
            return
        asyncio.create_task(self.signal_adapter.send(target, content))


def create_messaging_gateway(settings: Optional[Settings] = None) -> MessagingGateway:
    settings = settings or get_settings()
    signal_adapter: Optional[SignalAdapter] = None

    if not settings.signal_account:
        logger.warning("Signal messaging enabled without OPENPOKE_SIGNAL_ACCOUNT")
    else:
        signal_adapter = SignalAdapter(
                account=settings.signal_account,
                base_url=settings.signal_http_url,
                allowed_senders=set(settings.signal_allowed_senders),
            )

    return MessagingGateway(signal_adapter=signal_adapter)


_messaging_gateway: Optional[MessagingGateway] = None


def get_messaging_gateway() -> MessagingGateway:
    global _messaging_gateway
    if _messaging_gateway is None:
        _messaging_gateway = create_messaging_gateway()
    return _messaging_gateway


__all__ = ["MessagingGateway", "create_messaging_gateway", "get_messaging_gateway"]
