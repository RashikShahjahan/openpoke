from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ...config import get_settings
from ...logging_config import logger
from ...openrouter_client import OpenRouterError, request_chat_completion
from .models import WebPageSnapshot, WebWatcherEvaluation, WebWatcherRecord


_TOOL_NAME = "evaluate_web_watcher_update"
_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "Evaluate whether a newly fetched web page snapshot contains a meaningful "
            "change that matches the user's watcher condition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changed": {
                    "type": "boolean",
                    "description": "True when the page appears meaningfully different from the previous baseline.",
                },
                "relevant": {
                    "type": "boolean",
                    "description": "True only when the change satisfies the user's watcher condition.",
                },
                "summary": {
                    "type": "string",
                    "description": "Brief user-facing summary of the relevant change. Required when relevant=true.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Short quote or specific page detail supporting the decision.",
                },
                "new_snapshot_summary": {
                    "type": "string",
                    "description": "Compact summary of the current page state to use as the next baseline.",
                },
            },
            "required": ["changed", "relevant", "new_snapshot_summary"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = (
    "You evaluate updates for a personal web watcher. The user asked to monitor a public "
    "page for a specific condition. Compare the previous baseline with the current page "
    "snapshot. Only mark relevant=true when the current page contains a meaningful update "
    "that matches the user's condition and is worth interrupting them about. Ignore cosmetic "
    "changes, navigation changes, repeated boilerplate, timestamps, ads, cookie banners, and "
    "minor copy edits unless the user's condition explicitly cares about them. Always produce "
    "a concise new_snapshot_summary representing the current page state."
)


async def evaluate_web_watcher_update(
    watcher: WebWatcherRecord,
    snapshot: WebPageSnapshot,
) -> Optional[WebWatcherEvaluation]:
    """Return an LLM evaluation for a watcher update, or None on model/API failure."""

    settings = get_settings()
    api_key = settings.openrouter_api_key
    model = settings.web_watcher_evaluator_model

    if not api_key:
        logger.warning("Skipping web watcher evaluation; OpenRouter API key missing")
        return None

    messages = [{"role": "user", "content": _format_evaluation_payload(watcher, snapshot)}]

    try:
        response = await request_chat_completion(
            model=model,
            messages=messages,
            system=_SYSTEM_PROMPT,
            api_key=api_key,
            tools=[_TOOL_SCHEMA],
        )
    except OpenRouterError as exc:
        logger.error(
            "Web watcher evaluation failed",
            extra={"watcher_id": watcher.id, "error": str(exc)},
        )
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Unexpected error during web watcher evaluation",
            extra={"watcher_id": watcher.id, "error": str(exc)},
        )
        return None

    evaluation = parse_web_watcher_evaluation(response)
    if evaluation is None:
        logger.warning(
            "Web watcher evaluator produced invalid output",
            extra={"watcher_id": watcher.id},
        )
    return evaluation


def parse_web_watcher_evaluation(response: Dict[str, Any]) -> Optional[WebWatcherEvaluation]:
    """Parse the evaluator tool call from a raw OpenRouter response."""

    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = message.get("tool_calls") or []

    for tool_call in tool_calls:
        function_block = tool_call.get("function") or {}
        if function_block.get("name") != _TOOL_NAME:
            continue

        arguments = _coerce_arguments(function_block.get("arguments"))
        if arguments is None:
            return None

        try:
            evaluation = WebWatcherEvaluation.model_validate(arguments)
        except ValidationError:
            return None

        if evaluation.relevant and not _has_text(evaluation.summary):
            return None
        if evaluation.relevant and not _has_text(evaluation.evidence):
            return None
        return evaluation

    return None


def _format_evaluation_payload(watcher: WebWatcherRecord, snapshot: WebPageSnapshot) -> str:
    previous_hash = watcher.last_snapshot_hash or "None"
    current_hash = snapshot.content_hash
    hash_changed = previous_hash != current_hash
    return (
        "Watcher Metadata:\n"
        f"Name: {watcher.name}\n"
        f"URL: {watcher.url}\n"
        f"User condition: {watcher.condition}\n"
        f"Previous snapshot hash: {previous_hash}\n"
        f"Current snapshot hash: {current_hash}\n"
        f"Hash changed: {'Yes' if hash_changed else 'No'}\n"
        f"Last checked at: {watcher.last_checked_at or 'Never'}\n"
        f"Current fetched at: {snapshot.fetched_at}\n\n"
        "Previous Baseline Summary:\n"
        f"{watcher.last_snapshot_summary or '(no previous summary)'}\n\n"
        "Current Page Snapshot:\n"
        f"Title: {snapshot.title or '(untitled)'}\n"
        f"Content:\n{snapshot.content}"
    )


def _coerce_arguments(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _has_text(value: Optional[str]) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = ["evaluate_web_watcher_update", "parse_web_watcher_evaluation"]
