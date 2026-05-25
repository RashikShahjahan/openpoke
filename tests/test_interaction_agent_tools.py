from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.agents.interaction_agent import tools
from server.services.execution.roster import AgentRecord, AgentRoster


def test_tool_schemas_expose_sql_and_vector_search() -> None:
    names = {schema["function"]["name"] for schema in tools.get_tool_schemas()}

    assert "query_agents_sql" in names
    assert "vector_search_agents" in names
    assert "search_agents" not in names


def test_query_agents_sql_tool_uses_roster(monkeypatch, tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")
    roster.add_agent("Email to Alice", agent_type="email")
    monkeypatch.setattr(tools, "get_agent_roster", lambda: roster)

    result = tools.query_agents_sql(
        "SELECT name FROM agents WHERE search_text LIKE ?",
        params=["%alice%"],
    )

    assert result.success is True
    assert result.payload == {
        "rows": [{"name": "Email to Alice"}],
        "truncated": False,
    }


def test_vector_search_agents_tool_returns_agent_payload(monkeypatch) -> None:
    record = AgentRecord(
        id=7,
        name="Email to Alice",
        agent_type="email",
        status="active",
        created_at="2026-05-25T00:00:00Z",
        updated_at="2026-05-25T00:00:00Z",
        last_used_at=None,
        search_text="email alice",
    )

    class FakeIndex:
        async def vector_search_agents(self, query, *, limit=5, agent_ids=None):
            assert query == "Alice reply"
            assert limit == 3
            assert agent_ids == [7]
            return [record]

    monkeypatch.setattr(tools, "get_agent_search_index", lambda: FakeIndex())

    result = asyncio.run(
        tools.vector_search_agents("Alice reply", limit=3, agent_ids=[7])
    )

    assert result.success is True
    assert result.payload == {
        "query": "Alice reply",
        "agents": [record.to_dict()],
    }


def test_send_message_to_agent_reuses_agent_id(monkeypatch, tmp_path) -> None:
    roster = AgentRoster(tmp_path / "agents.sqlite3")
    record = roster.add_agent("Email to Alice", agent_type="email")

    class FakeLogs:
        def __init__(self) -> None:
            self.requests = []

        def record_request(self, agent_name: str, instructions: str) -> None:
            self.requests.append((agent_name, instructions))

    class FakeBatchManager:
        def __init__(self) -> None:
            self.calls = []

        async def execute_agent(self, agent_name: str, instructions: str):
            self.calls.append((agent_name, instructions))
            return SimpleNamespace(success=True)

    fake_logs = FakeLogs()
    fake_manager = FakeBatchManager()
    monkeypatch.setattr(tools, "get_agent_roster", lambda: roster)
    monkeypatch.setattr(tools, "get_execution_agent_logs", lambda: fake_logs)
    monkeypatch.setattr(tools, "_EXECUTION_BATCH_MANAGER", fake_manager)

    async def run() -> None:
        result = tools.send_message_to_agent(
            instructions="check whether Alice replied",
            agent_id=record.id,
        )
        await asyncio.sleep(0)

        assert result.success is True
        assert result.payload["agent"]["id"] == record.id
        assert result.payload["new_agent_created"] is False

    asyncio.run(run())

    assert fake_logs.requests == [("Email to Alice", "check whether Alice replied")]
    assert fake_manager.calls == [("Email to Alice", "check whether Alice replied")]
    assert roster.get_agent(record.id).last_used_at is not None
