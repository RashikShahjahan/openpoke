from __future__ import annotations

import pytest

from server.agents.execution_agent.tools import web
from server.agents.execution_agent.tools.registry import get_tool_registry, get_tool_schemas


def test_fetch_url_tool_is_registered() -> None:
    schema_names = {schema["function"]["name"] for schema in get_tool_schemas()}
    registry = get_tool_registry("web-test")

    assert "fetchUrl" in schema_names
    assert "fetchUrl" in registry


@pytest.mark.asyncio
async def test_fetch_url_tool_rejects_non_http_url() -> None:
    registry = web.build_registry("web-test")

    result = await registry["fetchUrl"](url="file:///etc/passwd")

    assert result == {"error": "url must be an absolute HTTP or HTTPS URL"}


@pytest.mark.asyncio
async def test_fetch_url_tool_returns_text_content(monkeypatch) -> None:
    class FakeLogStore:
        def __init__(self) -> None:
            self.actions: list[str] = []

        def record_action(self, agent_name: str, description: str) -> None:
            self.actions.append(description)

    class FakeResponse:
        status_code = 200
        url = "https://example.com/final"
        encoding = "utf-8"
        headers = {"content-type": "text/plain"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield b"hello world"

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers: dict[str, str]):
            return FakeResponse()

    fake_log_store = FakeLogStore()
    monkeypatch.setattr(web, "_LOG_STORE", fake_log_store)
    monkeypatch.setattr(web.httpx, "AsyncClient", FakeClient)

    registry = web.build_registry("web-test")
    result = await registry["fetchUrl"](url="https://example.com")

    assert result == {
        "url": "https://example.com",
        "final_url": "https://example.com/final",
        "status_code": 200,
        "content_type": "text/plain",
        "content": "hello world",
        "truncated": False,
    }
    assert fake_log_store.actions == [
        "fetchUrl succeeded | url=https://example.com | status=200 | bytes=11 | truncated=False"
    ]
