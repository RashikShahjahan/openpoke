from __future__ import annotations

import pytest

from server.services.execution.roster import AgentRoster


def test_roster_persists_agents_with_search_text(tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")

    record = roster.add_agent("Email to Alice", agent_type="email")

    assert record.id > 0
    assert record.name == "Email to Alice"
    assert record.agent_type == "email"
    assert record.status == "active"
    assert "alice" in record.search_text


def test_roster_query_readonly_filters_by_search_text_and_type(tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")
    roster.add_agent("Email to Alice", agent_type="email")
    roster.add_agent("Vercel Job Offer", agent_type="research")

    rows, truncated = roster.query_readonly(
        "SELECT id, name FROM agents WHERE agent_type = ? AND search_text LIKE ?",
        ["email", "%alice%"],
    )

    assert truncated is False
    assert rows == [{"id": 1, "name": "Email to Alice"}]


def test_roster_query_readonly_rejects_writes(tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")

    with pytest.raises(ValueError, match="Only SELECT"):
        roster.query_readonly("DELETE FROM agents")


def test_roster_touch_updates_last_used_at(tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")
    record = roster.add_agent("Calendar Followup", agent_type="calendar")

    touched = roster.touch_agent(record.id)

    assert touched is not None
    assert touched.last_used_at is not None

    rows, _ = roster.query_readonly(
        "SELECT id FROM agents WHERE last_used_at IS NOT NULL AND id = ?",
        [record.id],
    )
    assert rows == [{"id": record.id}]
