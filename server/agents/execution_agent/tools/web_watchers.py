"""Web watcher tool schemas and actions for the execution agent."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from server.services.execution import get_execution_agent_logs
from server.services.timezone_store import get_timezone_store
from server.services.triggers import get_trigger_service
from server.services.web_watchers import WebWatcherRecord, get_web_watcher_service


DEFAULT_WATCHER_CADENCE_RULE = "RRULE:FREQ=DAILY"


_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "createWebWatcher",
            "description": "Create a web watcher for a public URL and capture its initial page snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short human-readable watcher name.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Public URL to monitor.",
                    },
                    "condition": {
                        "type": "string",
                        "description": "Specific user condition for when this watcher should notify them.",
                    },
                    "cadence_rule": {
                        "type": "string",
                        "description": "Optional iCalendar RRULE string describing the intended check cadence.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Initial watcher status; usually 'active' or 'paused'.",
                    },
                },
                "required": ["name", "url", "condition"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkWebWatcher",
            "description": "Fetch a web watcher's URL, compare it to the stored baseline, and decide whether it has a relevant update.",
            "parameters": {
                "type": "object",
                "properties": {
                    "watcher_id": {
                        "type": "integer",
                        "description": "Identifier returned by createWebWatcher.",
                    },
                },
                "required": ["watcher_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listWebWatchers",
            "description": "List all stored web watchers and their latest check state.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "updateWebWatcher",
            "description": "Update or pause an existing web watcher.",
            "parameters": {
                "type": "object",
                "properties": {
                    "watcher_id": {
                        "type": "integer",
                        "description": "Identifier returned by createWebWatcher.",
                    },
                    "name": {
                        "type": "string",
                        "description": "New watcher name.",
                    },
                    "condition": {
                        "type": "string",
                        "description": "New condition for deciding when to notify the user.",
                    },
                    "cadence_rule": {
                        "type": "string",
                        "description": "New intended iCalendar RRULE check cadence.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Set watcher status to 'active', 'paused', or 'completed'.",
                    },
                },
                "required": ["watcher_id"],
                "additionalProperties": False,
            },
        },
    },
]

_LOG_STORE = get_execution_agent_logs()
_WATCHER_SERVICE = get_web_watcher_service()
_TRIGGER_SERVICE = get_trigger_service()


def get_schemas() -> List[Dict[str, Any]]:
    """Return web watcher tool schemas."""

    return _SCHEMAS


async def create_web_watcher(
    *,
    agent_name: str,
    name: str,
    url: str,
    condition: str,
    cadence_rule: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    summary_args = {
        "name": name,
        "url": url,
        "condition": condition,
        "cadence_rule": cadence_rule,
        "status": status,
    }
    try:
        resolved_cadence_rule = cadence_rule or DEFAULT_WATCHER_CADENCE_RULE
        watcher, snapshot = await _WATCHER_SERVICE.create_watcher(
            name=name,
            url=url,
            condition=condition,
            cadence_rule=resolved_cadence_rule,
            status=status,
        )
    except Exception as exc:
        _LOG_STORE.record_action(
            agent_name,
            description=f"createWebWatcher failed | details={json.dumps(summary_args, ensure_ascii=False)} | error={exc}",
        )
        return {"error": str(exc)}

    trigger_payload = _build_watcher_trigger_payload(watcher.id)
    trigger_error: Optional[str] = None
    trigger_id: Optional[int] = None
    try:
        trigger = _TRIGGER_SERVICE.create_trigger(
            agent_name=agent_name,
            payload=trigger_payload,
            recurrence_rule=watcher.cadence_rule or DEFAULT_WATCHER_CADENCE_RULE,
            timezone_name=get_timezone_store().get_timezone(),
            status=watcher.status,
        )
        trigger_id = trigger.id
        updated_watcher = _WATCHER_SERVICE.update_watcher(watcher.id, trigger_id=trigger.id)
        if updated_watcher is not None:
            watcher = updated_watcher
    except Exception as exc:  # pragma: no cover - defensive
        trigger_error = str(exc)

    _LOG_STORE.record_action(
        agent_name,
        description=f"createWebWatcher succeeded | watcher_id={watcher.id} | trigger_id={trigger_id}",
    )
    result = {
        "watcher": _watcher_record_to_payload(watcher),
        "trigger_id": trigger_id,
        "initial_snapshot": {
            "hash": snapshot.content_hash,
            "title": snapshot.title,
            "fetched_at": snapshot.fetched_at,
            "content_length": len(snapshot.content),
        },
    }
    if trigger_error:
        result["trigger_error"] = trigger_error
    return result


async def check_web_watcher(*, agent_name: str, watcher_id: Any) -> Dict[str, Any]:
    try:
        watcher_id_int = int(watcher_id)
    except (TypeError, ValueError):
        return {"error": "watcher_id must be an integer"}

    try:
        result = await _WATCHER_SERVICE.check_watcher(watcher_id_int)
    except Exception as exc:
        _LOG_STORE.record_action(
            agent_name,
            description=f"checkWebWatcher failed | watcher_id={watcher_id_int} | error={exc}",
        )
        return {"error": str(exc)}

    if result is None:
        return {"error": f"Web watcher {watcher_id_int} not found"}

    _LOG_STORE.record_action(
        agent_name,
        description=f"checkWebWatcher succeeded | watcher_id={watcher_id_int} | relevant={result.relevant}",
    )
    return {
        "watcher": _watcher_record_to_payload(result.watcher),
        "changed": result.changed,
        "relevant": result.relevant,
        "summary": result.summary,
        "evidence": result.evidence,
        "snapshot_hash": result.snapshot_hash,
    }


def list_web_watchers(*, agent_name: str) -> Dict[str, Any]:
    try:
        records = _WATCHER_SERVICE.list_watchers()
    except Exception as exc:
        _LOG_STORE.record_action(
            agent_name,
            description=f"listWebWatchers failed | error={exc}",
        )
        return {"error": str(exc)}

    _LOG_STORE.record_action(
        agent_name,
        description=f"listWebWatchers succeeded | count={len(records)}",
    )
    return {"watchers": [_watcher_record_to_payload(record) for record in records]}


def update_web_watcher(
    *,
    agent_name: str,
    watcher_id: Any,
    name: Optional[str] = None,
    condition: Optional[str] = None,
    cadence_rule: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        watcher_id_int = int(watcher_id)
    except (TypeError, ValueError):
        return {"error": "watcher_id must be an integer"}

    try:
        existing = _WATCHER_SERVICE.get_watcher(watcher_id_int)
        if existing is None:
            return {"error": f"Web watcher {watcher_id_int} not found"}

        record = _WATCHER_SERVICE.update_watcher(
            watcher_id_int,
            name=name,
            condition=condition,
            cadence_rule=cadence_rule,
            status=status,
            clear_error=True,
        )
    except Exception as exc:
        _LOG_STORE.record_action(
            agent_name,
            description=f"updateWebWatcher failed | watcher_id={watcher_id_int} | error={exc}",
        )
        return {"error": str(exc)}

    if record is None:
        return {"error": f"Web watcher {watcher_id_int} not found"}

    trigger_update_error: Optional[str] = None
    if record.trigger_id is not None and (cadence_rule is not None or status is not None):
        try:
            _TRIGGER_SERVICE.update_trigger(
                record.trigger_id,
                agent_name=agent_name,
                payload=_build_watcher_trigger_payload(record.id),
                recurrence_rule=record.cadence_rule,
                timezone_name=get_timezone_store().get_timezone(),
                status=record.status,
            )
        except Exception as exc:  # pragma: no cover - defensive
            trigger_update_error = str(exc)

    _LOG_STORE.record_action(
        agent_name,
        description=f"updateWebWatcher succeeded | watcher_id={watcher_id_int}",
    )
    result = {"watcher": _watcher_record_to_payload(record)}
    if trigger_update_error:
        result["trigger_error"] = trigger_update_error
    return result


def _watcher_record_to_payload(record: WebWatcherRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "url": record.url,
        "condition": record.condition,
        "cadence_rule": record.cadence_rule,
        "trigger_id": record.trigger_id,
        "status": record.status,
        "last_snapshot_hash": record.last_snapshot_hash,
        "last_snapshot_summary": record.last_snapshot_summary,
        "last_checked_at": record.last_checked_at,
        "last_notified_at": record.last_notified_at,
        "last_error": record.last_error,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _build_watcher_trigger_payload(watcher_id: int) -> str:
    return (
        f"Scheduled web watcher check for watcher ID {watcher_id}.\n"
        f"Call checkWebWatcher with watcher_id={watcher_id}.\n"
        "If the result has relevant=true, final response must start with "
        "'Web watcher notification:' and include the watcher name, summary, evidence, and URL.\n"
        "If the result has relevant=false, final response must be exactly: "
        f"No relevant web watcher update for watcher {watcher_id}."
    )


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    """Return web watcher tool callables bound to a specific agent."""

    async def _create_bound(**kwargs: Any) -> Dict[str, Any]:
        return await create_web_watcher(agent_name=agent_name, **kwargs)

    async def _check_bound(**kwargs: Any) -> Dict[str, Any]:
        return await check_web_watcher(agent_name=agent_name, **kwargs)

    def _list_bound(**kwargs: Any) -> Dict[str, Any]:
        return list_web_watchers(agent_name=agent_name, **kwargs)

    def _update_bound(**kwargs: Any) -> Dict[str, Any]:
        return update_web_watcher(agent_name=agent_name, **kwargs)

    return {
        "createWebWatcher": _create_bound,
        "checkWebWatcher": _check_bound,
        "listWebWatchers": _list_bound,
        "updateWebWatcher": _update_bound,
    }


__all__ = [
    "build_registry",
    "get_schemas",
]
