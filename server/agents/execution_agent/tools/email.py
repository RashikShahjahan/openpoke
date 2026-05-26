"""Read-only local email tool schemas and actions for execution agents."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from server.services.email import get_email_service
from server.services.execution import get_execution_agent_logs

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "emailConnectionStatus",
            "description": "Check whether local read-only Thunderbird email access is configured.",
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
            "name": "listEmailFolders",
            "description": "List local Thunderbird email folders available for read-only access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_counts": {
                        "type": "boolean",
                        "description": "Whether to count messages in each folder. Defaults to false.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchEmails",
            "description": "Search read-only local Thunderbird emails by text, metadata, folder, date, attachments, or canonical filters. Returns lightweight summaries with snippets; for triage, prefer narrow date windows and 25 or fewer results, then call getEmailMessage only for selected full bodies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text terms to match across subject, sender, recipients, and body.",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder id or name to search, such as Inbox or Sent.",
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["inbox", "sent", "spam", "read", "unread", "unarchived", "trash"],
                        },
                        "description": "Canonical filters to apply. Location filters (inbox, sent, spam, trash) match common Thunderbird folder names; read/unread use Thunderbird message state; unarchived excludes archive folders. Combine values such as ['inbox', 'unread'].",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Substring to match in the From header.",
                    },
                    "recipient": {
                        "type": "string",
                        "description": "Substring to match in To/Cc/Bcc recipients.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Substring to match in the subject.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "ISO 8601 inclusive start timestamp. Naive values are treated as UTC.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO 8601 exclusive end timestamp. Naive values are treated as UTC.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "Filter to emails with or without attachments.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum emails to return. Defaults to 20 and is capped at 50. Use 25 or fewer for inbox triage.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getEmailMessage",
            "description": "Read one full local Thunderbird email by id returned from searchEmails. Use only for shortlisted messages and pass a reduced max_body_chars when a snippet is enough.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "OpenPoke email id or RFC Message-ID returned by searchEmails.",
                    },
                    "max_body_chars": {
                        "type": "integer",
                        "description": "Maximum body characters to return. Defaults to 20000 and is capped at 100000.",
                    }
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
        },
    },
]

_LOG_STORE = get_execution_agent_logs()
_EMAIL_SERVICE = get_email_service()


def get_schemas() -> List[Dict[str, Any]]:
    """Return email tool schemas."""

    return _SCHEMAS


def _connection_status_tool(*, agent_name: str) -> Dict[str, Any]:
    status = _EMAIL_SERVICE.connection_status()
    _LOG_STORE.record_action(
        agent_name,
        description=f"emailConnectionStatus checked | status={status.get('status')}",
    )
    return status


def _list_folders_tool(*, agent_name: str, include_counts: bool = False) -> Dict[str, Any]:
    try:
        folders = _EMAIL_SERVICE.list_folders(include_counts=include_counts)
    except Exception as exc:  # pragma: no cover - defensive
        _LOG_STORE.record_action(
            agent_name,
            description=f"listEmailFolders failed | error={exc}",
        )
        return {"error": str(exc)}

    _LOG_STORE.record_action(
        agent_name,
        description=f"listEmailFolders succeeded | count={len(folders)}",
    )
    return {"folders": [folder.to_payload() for folder in folders]}


def _search_emails_tool(*, agent_name: str, **kwargs: Any) -> Dict[str, Any]:
    try:
        messages = _EMAIL_SERVICE.search_messages(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        safe_args = {key: value for key, value in kwargs.items() if key != "query"}
        _LOG_STORE.record_action(
            agent_name,
            description=f"searchEmails failed | filters={json.dumps(safe_args)} | error={exc}",
        )
        return {"error": str(exc)}

    _LOG_STORE.record_action(
        agent_name,
        description=f"searchEmails succeeded | count={len(messages)}",
    )
    return {"emails": [message.to_payload(include_body=False) for message in messages]}


def _get_message_tool(*, agent_name: str, message_id: str, max_body_chars: int | None = None) -> Dict[str, Any]:
    try:
        kwargs = {"message_id": message_id}
        if max_body_chars is not None:
            kwargs["max_body_chars"] = max_body_chars
        message = _EMAIL_SERVICE.get_message(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        _LOG_STORE.record_action(
            agent_name,
            description=f"getEmailMessage failed | error={exc}",
        )
        return {"error": str(exc)}

    if message is None:
        return {"error": f"Email message not found: {message_id}"}

    _LOG_STORE.record_action(
        agent_name,
        description="getEmailMessage succeeded",
    )
    return {"email": message.to_payload(include_body=True)}


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    """Return email tool callables bound to a specific agent."""

    return {
        "emailConnectionStatus": lambda: _connection_status_tool(agent_name=agent_name),
        "listEmailFolders": lambda **kwargs: _list_folders_tool(agent_name=agent_name, **kwargs),
        "searchEmails": lambda **kwargs: _search_emails_tool(agent_name=agent_name, **kwargs),
        "getEmailMessage": lambda **kwargs: _get_message_tool(agent_name=agent_name, **kwargs),
    }


__all__ = ["build_registry", "get_schemas"]
