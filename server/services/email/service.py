from __future__ import annotations

import hashlib
import html
import mailbox
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

from dateutil import parser as date_parser

from ...logging_config import logger
from ...utils.timezones import convert_to_user_timezone
from .models import EmailFolder, EmailMessage

_DEFAULT_MAX_BODY_CHARS = 20_000
_DEFAULT_MAX_RESULTS = 20
_ABSOLUTE_MAX_RESULTS = 100
_MBOX_EXCLUDED_NAMES = {"Trash.msf", "Inbox.msf", "Sent.msf"}


@dataclass(frozen=True)
class _FolderPath:
    id: str
    name: str
    path: Path


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "head", "title", "meta", "noscript"}:
            self._skip_depth += 1
        elif lowered in {"br", "p", "div", "tr", "li", "table"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "head", "title", "meta", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif lowered in {"p", "div", "tr", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


class EmailTextCleaner:
    """Extract readable text from generic RFC 822 email messages."""

    def clean_message(self, message: Message, *, max_chars: int = _DEFAULT_MAX_BODY_CHARS) -> str:
        html_body: str | None = None
        text_body: str | None = None

        for part in _iter_body_parts(message):
            content_type = part.get_content_type().lower()
            payload = _decode_part_payload(part)
            if not payload:
                continue
            if content_type == "text/plain" and text_body is None:
                text_body = payload
            elif content_type == "text/html" and html_body is None:
                html_body = payload

        if text_body:
            cleaned = self.post_process_text(text_body)
        elif html_body:
            cleaned = self.clean_html_email(html_body)
        else:
            cleaned = ""

        if len(cleaned) > max_chars:
            return cleaned[:max_chars].rstrip() + "\n[truncated]"
        return cleaned

    def clean_html_email(self, html_content: str) -> str:
        parser = _HtmlTextExtractor()
        try:
            parser.feed(html_content)
            text = parser.text()
        except Exception:  # pragma: no cover - defensive fallback
            text = re.sub(r"<[^>]+>", " ", html_content)
        return self.post_process_text(text)

    def post_process_text(self, text: str) -> str:
        text = html.unescape(text)
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        noise_patterns = [
            r"View this email in your browser.*?\n",
            r"If you can't see this email.*?\n",
            r"This is a system-generated email.*?\n",
            r"Please do not reply to this email.*?\n",
            r"Unsubscribe.*?preferences.*?\n",
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text.strip()


class ThunderbirdEmailService:
    """Read-only email service backed by local Thunderbird mbox storage."""

    def __init__(self, profile_path: Optional[str], refresh_seconds: int = 60) -> None:
        self._configured_profile_path = Path(profile_path).expanduser() if profile_path else None
        self._refresh_seconds = max(refresh_seconds, 0)
        self._cached_profile_path: Path | None = None
        self._cached_loaded_at: datetime | None = None
        self._cached_folders: list[_FolderPath] = []
        self._cleaner = EmailTextCleaner()

    def connection_status(self) -> dict[str, object]:
        profile_path = self._resolve_profile_path()
        if profile_path is None:
            return {"configured": False, "status": "missing_profile"}
        if not profile_path.is_dir():
            return {"configured": True, "status": "missing_directory", "path": str(profile_path)}
        folders = self._discover_folders(profile_path)
        return {
            "configured": True,
            "status": "connected" if folders else "no_mail_folders",
            "path": str(profile_path),
            "folder_count": len(folders),
        }

    def list_folders(self, *, include_counts: bool = False) -> list[EmailFolder]:
        profile_path = self._require_profile_path()
        folders = self._load_folders(profile_path)
        return [
            EmailFolder(
                id=folder.id,
                name=folder.name,
                path=str(folder.path),
                message_count=self._count_messages(folder.path) if include_counts else None,
            )
            for folder in folders
        ]

    def search_messages(
        self,
        *,
        query: str | None = None,
        folder: str | None = None,
        sender: str | None = None,
        recipient: str | None = None,
        subject: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        has_attachments: bool | None = None,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> list[EmailMessage]:
        profile_path = self._require_profile_path()
        folders = self._matching_folders(profile_path, folder)
        limit = min(max(int(max_results), 1), _ABSOLUTE_MAX_RESULTS)
        start = _parse_query_datetime(start_time) if start_time else None
        end = _parse_query_datetime(end_time) if end_time else None
        results: list[EmailMessage] = []

        for folder_path in folders:
            for message_key, message in self._iter_mbox_messages(folder_path.path):
                email = self._to_email_message(folder_path, message_key, message)
                if not _matches(email, query=query, sender=sender, recipient=recipient, subject=subject):
                    continue
                timestamp = _parse_payload_timestamp(email.timestamp)
                if start and (timestamp is None or timestamp < start):
                    continue
                if end and (timestamp is None or timestamp >= end):
                    continue
                if has_attachments is not None and email.has_attachments != has_attachments:
                    continue

                results.append(email)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        results.sort(key=lambda item: item.timestamp or "", reverse=True)
        return results[:limit]

    def get_message(self, *, message_id: str) -> EmailMessage | None:
        profile_path = self._require_profile_path()
        for folder_path in self._load_folders(profile_path):
            for message_key, message in self._iter_mbox_messages(folder_path.path):
                email = self._to_email_message(folder_path, message_key, message)
                if email.id == message_id or email.message_id == message_id:
                    return email
        return None

    def _resolve_profile_path(self) -> Path | None:
        if self._configured_profile_path:
            return self._configured_profile_path

        thunderbird_roots = [
            Path.home() / "Library" / "Thunderbird" / "Profiles",
            Path.home() / ".thunderbird",
            Path.home() / ".mozilla-thunderbird",
        ]
        for thunderbird_root in thunderbird_roots:
            if not thunderbird_root.is_dir():
                continue
            profiles = sorted(path for path in thunderbird_root.iterdir() if path.is_dir())
            for profile in profiles:
                if (profile / "Mail").is_dir() or (profile / "ImapMail").is_dir():
                    return profile
            if profiles:
                return profiles[0]
        return None

    def _require_profile_path(self) -> Path:
        profile_path = self._resolve_profile_path()
        if profile_path is None:
            raise ValueError("Email is not configured. Set OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH.")
        if not profile_path.is_dir():
            raise ValueError(f"Thunderbird profile directory not found: {profile_path}")
        return profile_path

    def _load_folders(self, profile_path: Path) -> list[_FolderPath]:
        now = datetime.now(timezone.utc)
        cache_fresh = (
            self._cached_loaded_at is not None
            and (now - self._cached_loaded_at).total_seconds() < self._refresh_seconds
        )
        if self._cached_profile_path == profile_path and cache_fresh:
            return self._cached_folders

        folders = self._discover_folders(profile_path)
        self._cached_profile_path = profile_path
        self._cached_loaded_at = now
        self._cached_folders = folders
        logger.info("email folders loaded", extra={"folders": len(folders), "path": str(profile_path)})
        return folders

    def _discover_folders(self, profile_path: Path) -> list[_FolderPath]:
        roots = [profile_path / "Mail", profile_path / "ImapMail"]
        folders: list[_FolderPath] = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if _is_mbox_file(path):
                    rel = path.relative_to(profile_path).as_posix()
                    folders.append(_FolderPath(id=_folder_id(rel), name=_folder_name(path, root), path=path))
        return folders

    def _matching_folders(self, profile_path: Path, folder: str | None) -> list[_FolderPath]:
        folders = self._load_folders(profile_path)
        if not folder:
            return folders
        needle = folder.lower().strip()
        return [
            item
            for item in folders
            if item.id.lower() == needle or item.name.lower() == needle or needle in item.name.lower()
        ]

    def _iter_mbox_messages(self, path: Path) -> Iterable[tuple[str, Message]]:
        try:
            mbox = mailbox.mbox(path, create=False)
            try:
                for key in mbox.keys():
                    yield str(key), mbox.get_message(key)
            finally:
                mbox.close()
        except Exception as exc:
            logger.warning("failed to read email folder", extra={"path": str(path), "error": str(exc)})

    def _count_messages(self, path: Path) -> int:
        return sum(1 for _key, _message in self._iter_mbox_messages(path))

    def _to_email_message(self, folder: _FolderPath, message_key: str, message: Message) -> EmailMessage:
        message_id = _decode_header_value(message.get("Message-ID")) or None
        subject = _decode_header_value(message.get("Subject")) or "No Subject"
        sender = _decode_header_value(message.get("From")) or "Unknown Sender"
        recipient_headers = [message.get(name, "") for name in ("To", "Cc", "Bcc")]
        recipients = [_format_address(name, address) for name, address in getaddresses(recipient_headers)]
        timestamp = _message_timestamp(message)
        attachment_filenames = _attachment_filenames(message)
        stable_source = f"{folder.path}:{message_key}:{message_id or subject}:{timestamp or ''}"
        return EmailMessage(
            id=hashlib.sha1(stable_source.encode("utf-8", errors="replace")).hexdigest()[:24],
            folder=folder.name,
            subject=subject,
            sender=sender,
            recipients=[value for value in recipients if value],
            timestamp=timestamp,
            message_id=message_id,
            clean_text=self._cleaner.clean_message(message),
            has_attachments=bool(attachment_filenames),
            attachment_count=len(attachment_filenames),
            attachment_filenames=attachment_filenames,
        )


def _is_mbox_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".") or path.name.endswith(".msf") or path.name.endswith(".dat"):
        return False
    if path.name in _MBOX_EXCLUDED_NAMES or path.suffix in {".sqlite", ".json", ".ini", ".html"}:
        return False
    try:
        if path.stat().st_size == 0:
            return True
        with path.open("rb") as handle:
            return handle.read(5) == b"From "
    except OSError:
        return False


def _folder_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative.replace(".sbd/", "/")


def _folder_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def _format_address(name: str, address: str) -> str:
    display = _decode_header_value(name)
    if display and address:
        return f"{display} <{address}>"
    return address or display


def _message_timestamp(message: Message) -> str | None:
    raw = message.get("Date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    converted = convert_to_user_timezone(parsed)
    return converted.isoformat(timespec="seconds")


def _parse_query_datetime(value: str) -> datetime:
    parsed = date_parser.isoparse(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_payload_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.isoparse(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_body_parts(message: Message) -> Iterable[Message]:
    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if part.get_content_type().lower() in {"text/plain", "text/html"}:
                yield part
    elif message.get_content_type().lower() in {"text/plain", "text/html"}:
        yield message


def _decode_part_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _attachment_filenames(message: Message) -> list[str]:
    filenames: list[str] = []
    for part in message.walk() if message.is_multipart() else []:
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" in disposition or filename:
            filenames.append(_decode_header_value(filename) or "unnamed attachment")
    return filenames


def _matches(
    email: EmailMessage,
    *,
    query: str | None,
    sender: str | None,
    recipient: str | None,
    subject: str | None,
) -> bool:
    if sender and sender.lower() not in email.sender.lower():
        return False
    if recipient and recipient.lower() not in " ".join(email.recipients).lower():
        return False
    if subject and subject.lower() not in email.subject.lower():
        return False
    if query:
        haystack = "\n".join([email.subject, email.sender, " ".join(email.recipients), email.clean_text]).lower()
        for term in query.lower().split():
            if term not in haystack:
                return False
    return True


__all__ = ["EmailTextCleaner", "ThunderbirdEmailService"]
