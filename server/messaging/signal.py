from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx

from .types import InboundMessage, ReplyTarget

logger = logging.getLogger("openpoke.server.messaging.signal")

MessageHandler = Callable[[InboundMessage], None | Awaitable[None]]


class SignalAdapter:
    """Adapter for signal-cli's direct HTTP daemon."""

    source = "signal"

    def __init__(
        self,
        account: str,
        base_url: str,
        allowed_senders: set[str],
        on_message: Optional[MessageHandler] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.account = account.strip()
        self.base_url = base_url.rstrip("/")
        self.allowed_senders = {sender.strip() for sender in allowed_senders if sender.strip()}
        self.on_message = on_message
        self._client = client
        self._owns_client = client is None
        self._running = False

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=None)
        return self._client

    async def close(self) -> None:
        self._running = False
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            response = await self.client.get("/api/v1/check")
        except httpx.HTTPError:
            logger.exception("signal health check failed")
            return False
        return 200 <= response.status_code < 300

    def parse_event(self, event: dict[str, Any]) -> InboundMessage | None:
        envelope = event.get("envelope") if isinstance(event.get("envelope"), dict) else event
        if not isinstance(envelope, dict):
            return None

        sender = str(envelope.get("sourceNumber") or "").strip()
        if not sender or sender == self.account:
            return None
        if sender not in self.allowed_senders:
            return None

        data_message = envelope.get("dataMessage")
        if not isinstance(data_message, dict):
            return None

        text = str(data_message.get("message") or "").strip()
        if not text:
            return None

        return InboundMessage(source=self.source, sender=sender, text=text, raw=event)

    async def send(self, target: ReplyTarget, content: str) -> None:
        if target.source != self.source:
            raise ValueError(f"unsupported reply target source: {target.source}")

        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": self.account,
                "message": content,
                "recipient": [target.destination],
            },
            "id": 1,
        }
        response = await self.client.post("/api/v1/rpc", json=payload)
        response.raise_for_status()

    def dispatch_event(self, data: str) -> InboundMessage | None:
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("ignoring malformed Signal event")
            return None

        message = self.parse_event(event)
        if message is None:
            return None

        if self.on_message is not None:
            result = self.on_message(message)
            if isinstance(result, Awaitable):
                asyncio.create_task(result)
        return message

    async def listen(self) -> None:
        self._running = True
        while self._running:
            try:
                async with self.client.stream(
                    "GET",
                    "/api/v1/events",
                    params={"account": self.account},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not self._running:
                            return
                        if line.startswith("data:"):
                            self.dispatch_event(line.removeprefix("data:").strip())
            except asyncio.CancelledError:
                self._running = False
                raise
            except Exception:
                logger.exception("Signal event stream failed")
                await asyncio.sleep(2)


__all__ = ["SignalAdapter"]
