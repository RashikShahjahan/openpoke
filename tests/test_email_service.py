from __future__ import annotations

import mailbox
from email.message import EmailMessage as RawEmailMessage
from pathlib import Path

import pytest

from server.services.email import ThunderbirdEmailService


def _write_message(mbox_path: Path, message: RawEmailMessage) -> None:
    mbox = mailbox.mbox(mbox_path)
    try:
        mbox.add(message)
        mbox.flush()
    finally:
        mbox.close()


def _message(
    *,
    subject: str,
    sender: str = "Alice <alice@example.com>",
    recipient: str = "Bob <bob@example.com>",
    date: str = "Sat, 23 May 2026 15:00:00 +0000",
    body: str = "Hello from email",
    message_id: str = "<message-1@example.com>",
) -> RawEmailMessage:
    message = RawEmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = message_id
    message.set_content(body)
    return message


def _profile_with_inbox(tmp_path: Path) -> tuple[Path, Path]:
    profile = tmp_path / "profile.default-release"
    local = profile / "Mail" / "Local Folders"
    local.mkdir(parents=True)
    return profile, local / "Inbox"


def test_connection_status_and_folder_discovery(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(inbox, _message(subject="Team Standup"))
    (inbox.parent / "Inbox.msf").write_text("index", encoding="utf-8")

    service = ThunderbirdEmailService(str(profile))

    assert service.connection_status()["status"] == "connected"
    folders = service.list_folders(include_counts=True)
    assert len(folders) == 1
    assert folders[0].name == "Local Folders/Inbox"
    assert folders[0].message_count == 1


def test_search_messages_filters_and_extracts_body(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(
        inbox,
        _message(
            subject="Project Alpha update",
            sender="Carol <carol@example.com>",
            body="The launch plan is ready for review.",
            message_id="<alpha@example.com>",
        ),
    )
    _write_message(
        inbox,
        _message(
            subject="Other topic",
            sender="Dan <dan@example.com>",
            body="Unrelated",
            message_id="<other@example.com>",
        ),
    )
    service = ThunderbirdEmailService(str(profile))

    results = service.search_messages(query="launch review", sender="carol")

    assert len(results) == 1
    assert results[0].subject == "Project Alpha update"
    assert results[0].message_id == "<alpha@example.com>"
    assert "launch plan" in results[0].clean_text


def test_get_message_by_openpoke_id(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(inbox, _message(subject="Find me", message_id="<find-me@example.com>"))
    service = ThunderbirdEmailService(str(profile))
    [result] = service.search_messages(subject="Find me")

    message = service.get_message(message_id=result.id)

    assert message is not None
    assert message.subject == "Find me"


def test_search_messages_filters_attachments(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    message = _message(subject="Invoice", body="See attachment", message_id="<invoice@example.com>")
    message.add_attachment(b"data", maintype="application", subtype="pdf", filename="invoice.pdf")
    _write_message(inbox, message)
    _write_message(inbox, _message(subject="No attachment", message_id="<plain@example.com>"))
    service = ThunderbirdEmailService(str(profile))

    results = service.search_messages(has_attachments=True)

    assert len(results) == 1
    assert results[0].attachment_filenames == ["invoice.pdf"]


def test_missing_email_profile_reports_status_and_errors(tmp_path: Path) -> None:
    service = ThunderbirdEmailService(str(tmp_path / "missing"))

    assert service.connection_status()["status"] == "missing_directory"
    with pytest.raises(ValueError, match="Thunderbird profile directory not found"):
        service.list_folders()
