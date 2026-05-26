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


def test_get_message_applies_body_limit(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(inbox, _message(subject="Long body", body="abcdefghij", message_id="<long@example.com>"))
    service = ThunderbirdEmailService(str(profile))
    [result] = service.search_messages(subject="Long body")

    message = service.get_message(message_id=result.id, max_body_chars=4)

    assert message is not None
    assert message.clean_text == "abcd\n[truncated]"


def test_search_filters_metadata_before_extracting_body(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(inbox, _message(subject="Skip me", message_id="<skip@example.com>"))
    _write_message(inbox, _message(subject="Target", message_id="<target@example.com>"))
    service = ThunderbirdEmailService(str(profile))

    class CountingCleaner:
        def __init__(self) -> None:
            self.subjects: list[str] = []

        def clean_message(self, message, *, max_chars: int = 20_000) -> str:
            subject = message.get("Subject", "")
            self.subjects.append(subject)
            return subject

    cleaner = CountingCleaner()
    service._cleaner = cleaner

    results = service.search_messages(subject="Target")

    assert len(results) == 1
    assert results[0].subject == "Target"
    assert cleaner.subjects == ["Target"]


def test_get_message_filters_id_before_extracting_body(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    _write_message(inbox, _message(subject="Target", message_id="<target@example.com>"))
    _write_message(inbox, _message(subject="Skip me", message_id="<skip@example.com>"))
    service = ThunderbirdEmailService(str(profile))
    [result] = service.search_messages(subject="Target")

    class CountingCleaner:
        def __init__(self) -> None:
            self.subjects: list[str] = []

        def clean_message(self, message, *, max_chars: int = 20_000) -> str:
            subject = message.get("Subject", "")
            self.subjects.append(subject)
            return subject

    cleaner = CountingCleaner()
    service._cleaner = cleaner

    message = service.get_message(message_id=result.id)

    assert message is not None
    assert message.subject == "Target"
    assert cleaner.subjects == ["Target"]


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


def test_search_messages_filters_canonical_folders(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    sent = inbox.parent / "Sent"
    junk = inbox.parent / "Junk"
    trash = inbox.parent / "Trash"
    _write_message(inbox, _message(subject="Inbox message", message_id="<inbox@example.com>"))
    _write_message(sent, _message(subject="Sent message", message_id="<sent@example.com>"))
    _write_message(junk, _message(subject="Junk message", message_id="<junk@example.com>"))
    _write_message(trash, _message(subject="Trash message", message_id="<trash@example.com>"))
    service = ThunderbirdEmailService(str(profile))

    assert [result.subject for result in service.search_messages(filters=["inbox"])] == ["Inbox message"]
    assert [result.subject for result in service.search_messages(filters=["sent"])] == ["Sent message"]
    assert [result.subject for result in service.search_messages(filters=["spam"])] == ["Junk message"]
    assert [result.subject for result in service.search_messages(filters=["trash"])] == ["Trash message"]


def test_search_messages_filters_read_unread_and_unarchived(tmp_path: Path) -> None:
    profile, inbox = _profile_with_inbox(tmp_path)
    archive = inbox.parent / "Archives"
    read_message = _message(subject="Read message", message_id="<read@example.com>")
    read_message["X-Mozilla-Status"] = "0001"
    unread_message = _message(subject="Unread message", message_id="<unread@example.com>")
    unread_message["X-Mozilla-Status"] = "0000"
    archived_message = _message(subject="Archived message", message_id="<archived@example.com>")
    archived_message["X-Mozilla-Status"] = "0000"
    _write_message(inbox, read_message)
    _write_message(inbox, unread_message)
    _write_message(archive, archived_message)
    service = ThunderbirdEmailService(str(profile))

    read_subjects = {result.subject for result in service.search_messages(filters=["read"])}
    unread_subjects = {result.subject for result in service.search_messages(filters=["unread"], max_results=10)}
    unarchived_subjects = {result.subject for result in service.search_messages(filters=["unarchived"], max_results=10)}
    unread_unarchived_subjects = {
        result.subject for result in service.search_messages(filters=["unread", "unchived"], max_results=10)
    }

    assert read_subjects == {"Read message"}
    assert unread_subjects == {"Unread message", "Archived message"}
    assert unarchived_subjects == {"Read message", "Unread message"}
    assert unread_unarchived_subjects == {"Unread message"}


def test_missing_email_profile_reports_status_and_errors(tmp_path: Path) -> None:
    service = ThunderbirdEmailService(str(tmp_path / "missing"))

    assert service.connection_status()["status"] == "missing_directory"
    with pytest.raises(ValueError, match="Thunderbird profile directory not found"):
        service.list_folders()
