from __future__ import annotations

from server.agents.execution_agent.agent import ExecutionAgent
from server.agents.execution_agent.tools import calendar
from server.agents.execution_agent.tools.registry import get_tool_registry, get_tool_schemas


def test_calendar_tools_are_registered() -> None:
    schema_names = {schema["function"]["name"] for schema in get_tool_schemas()}
    registry = get_tool_registry("calendar-test")

    assert "calendarConnectionStatus" in schema_names
    assert "listCalendarEvents" in schema_names
    assert "getCalendarAvailability" in schema_names
    assert "calendarConnectionStatus" in registry
    assert "listCalendarEvents" in registry
    assert "getCalendarAvailability" in registry


def test_calendar_connection_status_tool(monkeypatch) -> None:
    class FakeService:
        def connection_status(self):
            return {"configured": True, "status": "connected"}

    monkeypatch.setattr(calendar, "_CALENDAR_SERVICE", FakeService())

    registry = calendar.build_registry("calendar-test")

    assert registry["calendarConnectionStatus"]() == {
        "configured": True,
        "status": "connected",
    }


def test_execution_agent_redacts_calendar_tool_results() -> None:
    class FakeLogStore:
        def __init__(self) -> None:
            self.actions: list[str] = []
            self.responses: list[tuple[str, str]] = []

        def record_action(self, agent_name: str, description: str) -> None:
            self.actions.append(description)

        def record_tool_response(self, agent_name: str, tool_name: str, response: str) -> None:
            self.responses.append((tool_name, response))

    agent = ExecutionAgent("calendar-test")
    fake_log_store = FakeLogStore()
    agent._log_store = fake_log_store

    agent.record_tool_execution(
        "listCalendarEvents",
        '{"start_time":"2026-05-23T00:00:00Z"}',
        '{"events":[{"summary":"Private appointment"}]}',
    )

    assert fake_log_store.responses == [
        ("listCalendarEvents", "<calendar result redacted>")
    ]
