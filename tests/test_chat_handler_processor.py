from __future__ import annotations

import asyncio
from typing import Any

from fastapi import status
from fastapi.responses import PlainTextResponse

from server.models import ChatRequest
from server.services.conversation import chat_handler


class FakeProcessor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_runtime(self) -> object:
        return object()

    async def process_user_message(self, user_message: str, runtime: object | None = None) -> None:
        self.calls.append(user_message)


def test_chat_send_still_returns_202(monkeypatch: Any) -> None:
    processor = FakeProcessor()
    scheduled: list[Any] = []

    def fake_create_task(coro: Any) -> object:
        scheduled.append(coro)
        return object()

    monkeypatch.setattr(chat_handler, "get_conversation_processor", lambda: processor)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    payload = ChatRequest(messages=[{"role": "user", "content": "hello"}])
    response = asyncio.run(chat_handler.handle_chat_request(payload))

    assert isinstance(response, PlainTextResponse)
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert len(scheduled) == 1

    asyncio.run(scheduled[0])
    assert processor.calls == ["hello"]
