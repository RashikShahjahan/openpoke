"""Web fetch tool schemas and actions for execution agents."""

from __future__ import annotations

from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import httpx

from server.services.execution import get_execution_agent_logs

_DEFAULT_MAX_BYTES = 100_000
_ABSOLUTE_MAX_BYTES = 200_000
_TIMEOUT_SECONDS = 10.0

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fetchUrl",
            "description": "Fetch the text content at an HTTP or HTTPS URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP or HTTPS URL to fetch.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum response bytes to return. Defaults to 100000 and is capped at 200000.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]

_LOG_STORE = get_execution_agent_logs()


def get_schemas() -> List[Dict[str, Any]]:
    """Return web tool schemas."""

    return _SCHEMAS


async def _fetch_url_tool(
    *,
    agent_name: str,
    url: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"error": "url must be an absolute HTTP or HTTPS URL"}

    try:
        byte_limit = min(max(int(max_bytes), 1), _ABSOLUTE_MAX_BYTES)
    except (TypeError, ValueError):
        return {"error": "max_bytes must be an integer"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": "OpenPoke/1.0"},
            ) as response:
                chunks: list[bytes] = []
                total_bytes = 0
                truncated = False

                async for chunk in response.aiter_bytes():
                    remaining = byte_limit - total_bytes
                    if len(chunk) > remaining:
                        chunks.append(chunk[:remaining])
                        total_bytes = byte_limit
                        truncated = True
                        break

                    chunks.append(chunk)
                    total_bytes += len(chunk)
                    if total_bytes >= byte_limit:
                        truncated = True
                        break

                content_bytes = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                content = content_bytes.decode(encoding, errors="replace")

                _LOG_STORE.record_action(
                    agent_name,
                    description=(
                        f"fetchUrl succeeded | url={url} | status={response.status_code} "
                        f"| bytes={len(content_bytes)} | truncated={truncated}"
                    ),
                )
                return {
                    "url": url,
                    "final_url": str(response.url),
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "content": content,
                    "truncated": truncated,
                }
    except httpx.HTTPError as exc:
        _LOG_STORE.record_action(
            agent_name,
            description=f"fetchUrl failed | url={url} | error={exc}",
        )
        return {"error": str(exc)}


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    """Return web tool callables bound to a specific agent."""

    return {
        "fetchUrl": lambda **kwargs: _fetch_url_tool(agent_name=agent_name, **kwargs),
    }


__all__ = ["build_registry", "get_schemas"]
