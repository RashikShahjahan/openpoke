from __future__ import annotations

from dataclasses import dataclass, field


_DEFAULT_SNIPPET_CHARS = 500


def _build_snippet(text: str, *, max_chars: int = _DEFAULT_SNIPPET_CHARS) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "..."


@dataclass(frozen=True)
class EmailFolder:
    """Normalized read-only local email folder."""

    id: str
    name: str
    path: str
    message_count: int | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "path": self.path,
        }
        if self.message_count is not None:
            payload["message_count"] = self.message_count
        return payload


@dataclass(frozen=True)
class EmailMessage:
    """Normalized read-only local email message."""

    id: str
    folder: str
    subject: str
    sender: str
    recipients: list[str]
    timestamp: str | None
    message_id: str | None = None
    clean_text: str = ""
    snippet: str = ""
    has_attachments: bool = False
    attachment_count: int = 0
    attachment_filenames: list[str] = field(default_factory=list)
    is_read: bool = False
    is_spam: bool = False
    is_archived: bool = False
    is_trash: bool = False

    def to_payload(self, *, include_body: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "folder": self.folder,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "snippet": self.snippet or _build_snippet(self.clean_text),
            "has_attachments": self.has_attachments,
            "attachment_count": self.attachment_count,
            "attachment_filenames": self.attachment_filenames,
            "is_read": self.is_read,
            "is_spam": self.is_spam,
            "is_archived": self.is_archived,
            "is_trash": self.is_trash,
        }
        if self.timestamp:
            payload["timestamp"] = self.timestamp
        if self.message_id:
            payload["message_id"] = self.message_id
        if include_body:
            payload["clean_text"] = self.clean_text
        return payload


__all__ = ["EmailFolder", "EmailMessage"]
