from __future__ import annotations

from server.agents.execution_agent.agent import ExecutionAgent
from server.agents.execution_agent.tools import email
from server.agents.execution_agent.tools.registry import get_tool_registry, get_tool_schemas
from server.services.email.models import EmailFolder, EmailMessage


def test_email_tools_are_registered() -> None:
    schema_names = {schema["function"]["name"] for schema in get_tool_schemas()}
    registry = get_tool_registry("email-test")

    assert "emailConnectionStatus" in schema_names
    assert "listEmailFolders" in schema_names
    assert "searchEmails" in schema_names
    assert "getEmailMessage" in schema_names
    assert "emailConnectionStatus" in registry
    assert "listEmailFolders" in registry
    assert "searchEmails" in registry
    assert "getEmailMessage" in registry


def test_search_email_schema_exposes_canonical_filters() -> None:
    [search_schema] = [schema for schema in get_tool_schemas() if schema["function"]["name"] == "searchEmails"]

    filters = search_schema["function"]["parameters"]["properties"]["filters"]

    assert filters["items"]["enum"] == ["inbox", "sent", "spam", "read", "unread", "unarchived", "trash"]


def test_email_connection_status_tool(monkeypatch) -> None:
    class FakeService:
        def connection_status(self):
            return {"configured": True, "status": "connected"}

    monkeypatch.setattr(email, "_EMAIL_SERVICE", FakeService())

    registry = email.build_registry("email-test")

    assert registry["emailConnectionStatus"]() == {
        "configured": True,
        "status": "connected",
    }


def test_list_email_folders_tool(monkeypatch) -> None:
    class FakeService:
        def list_folders(self, *, include_counts: bool = False):
            return [EmailFolder(id="folder-1", name="Inbox", path="/tmp/Inbox", message_count=1 if include_counts else None)]

    monkeypatch.setattr(email, "_EMAIL_SERVICE", FakeService())

    registry = email.build_registry("email-test")
    result = registry["listEmailFolders"](include_counts=True)

    assert result == {
        "folders": [{"id": "folder-1", "name": "Inbox", "path": "/tmp/Inbox", "message_count": 1}]
    }


def test_search_emails_tool(monkeypatch) -> None:
    class FakeService:
        def search_messages(self, **kwargs):
            return [
                EmailMessage(
                    id="email-1",
                    folder="Inbox",
                    subject="Private subject",
                    sender="Alice <alice@example.com>",
                    recipients=["Bob <bob@example.com>"],
                    timestamp="2026-05-23T15:00:00+00:00",
                    clean_text="Private body",
                )
            ]

    monkeypatch.setattr(email, "_EMAIL_SERVICE", FakeService())

    registry = email.build_registry("email-test")
    result = registry["searchEmails"](query="private")

    assert result["emails"][0]["id"] == "email-1"
    assert result["emails"][0]["snippet"] == "Private body"
    assert "clean_text" not in result["emails"][0]


def test_get_email_message_tool_returns_body(monkeypatch) -> None:
    class FakeService:
        def get_message(self, *, message_id: str, max_body_chars: int = 20_000):
            assert message_id == "email-1"
            assert max_body_chars == 1200
            return EmailMessage(
                id="email-1",
                folder="Inbox",
                subject="Private subject",
                sender="Alice <alice@example.com>",
                recipients=["Bob <bob@example.com>"],
                timestamp="2026-05-23T15:00:00+00:00",
                clean_text="Private body",
            )

    monkeypatch.setattr(email, "_EMAIL_SERVICE", FakeService())

    registry = email.build_registry("email-test")
    result = registry["getEmailMessage"](message_id="email-1", max_body_chars=1200)

    assert result["email"]["clean_text"] == "Private body"
    assert result["email"]["snippet"] == "Private body"


def test_execution_agent_redacts_email_tool_results() -> None:
    class FakeLogStore:
        def __init__(self) -> None:
            self.actions: list[str] = []
            self.responses: list[tuple[str, str]] = []

        def record_action(self, agent_name: str, description: str) -> None:
            self.actions.append(description)

        def record_tool_response(self, agent_name: str, tool_name: str, response: str) -> None:
            self.responses.append((tool_name, response))

    agent = ExecutionAgent("email-test")
    fake_log_store = FakeLogStore()
    agent._log_store = fake_log_store

    agent.record_tool_execution(
        "searchEmails",
        '{"query":"private"}',
        '{"emails":[{"subject":"Private appointment","clean_text":"secret"}]}',
    )

    assert fake_log_store.responses == [("searchEmails", "<email result redacted>")]
