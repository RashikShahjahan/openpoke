"""Read-only calendar tool schemas and actions for execution agents."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from server.services.calendar import get_calendar_service
from server.services.execution import get_execution_agent_logs

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calendarConnectionStatus",
            "description": "Check whether local read-only calendar access is configured.",
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
            "name": "listCalendarEvents",
            "description": "List read-only calendar events overlapping a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "ISO 8601 range start. Naive values use the user's timezone.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO 8601 range end. Naive values use the user's timezone.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum events to return. Defaults to 20.",
                    },
                },
                "required": ["start_time", "end_time"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCalendarAvailability",
            "description": "Check whether the user has any busy calendar events in a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "ISO 8601 range start. Naive values use the user's timezone.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO 8601 range end. Naive values use the user's timezone.",
                    },
                },
                "required": ["start_time", "end_time"],
                "additionalProperties": False,
            },
        },
    },
]

_LOG_STORE = get_execution_agent_logs()
_CALENDAR_SERVICE = get_calendar_service()


def get_schemas() -> List[Dict[str, Any]]:
    """Return calendar tool schemas."""

    return _SCHEMAS


def _connection_status_tool(*, agent_name: str) -> Dict[str, Any]:
    status = _CALENDAR_SERVICE.connection_status()
    _LOG_STORE.record_action(
        agent_name,
        description=f"calendarConnectionStatus checked | status={status.get('status')}",
    )
    return status


def _list_events_tool(
    *,
    agent_name: str,
    start_time: str,
    end_time: str,
    max_results: int = 20,
) -> Dict[str, Any]:
    try:
        events = _CALENDAR_SERVICE.list_events(
            start_time=start_time,
            end_time=end_time,
            max_results=max_results,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _LOG_STORE.record_action(
            agent_name,
            description=f"listCalendarEvents failed | range={json.dumps({'start_time': start_time, 'end_time': end_time})} | error={exc}",
        )
        return {"error": str(exc)}

    _LOG_STORE.record_action(
        agent_name,
        description=f"listCalendarEvents succeeded | start={start_time} | end={end_time} | count={len(events)}",
    )
    return {"events": [event.to_payload() for event in events]}


def _availability_tool(*, agent_name: str, start_time: str, end_time: str) -> Dict[str, Any]:
    try:
        availability = _CALENDAR_SERVICE.get_availability(
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _LOG_STORE.record_action(
            agent_name,
            description=f"getCalendarAvailability failed | range={json.dumps({'start_time': start_time, 'end_time': end_time})} | error={exc}",
        )
        return {"error": str(exc)}

    payload = availability.to_payload()
    _LOG_STORE.record_action(
        agent_name,
        description=(
            f"getCalendarAvailability succeeded | start={start_time} | end={end_time} "
            f"| available={payload['available']} | busy_count={len(payload['busy'])}"
        ),
    )
    return payload


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    """Return calendar tool callables bound to a specific agent."""

    return {
        "calendarConnectionStatus": lambda: _connection_status_tool(agent_name=agent_name),
        "listCalendarEvents": lambda **kwargs: _list_events_tool(agent_name=agent_name, **kwargs),
        "getCalendarAvailability": lambda **kwargs: _availability_tool(agent_name=agent_name, **kwargs),
    }


__all__ = ["build_registry", "get_schemas"]
