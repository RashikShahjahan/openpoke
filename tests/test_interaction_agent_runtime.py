from __future__ import annotations

import asyncio
import json

from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.tools import ToolResult


def _tool_call(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def test_interaction_loop_stops_after_agent_dispatch() -> None:
    async def run_test() -> None:
        runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        llm_calls = 0

        async def fake_llm_call(_system_prompt, _messages):
            nonlocal llm_calls
            llm_calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                _tool_call(
                                    "call-user",
                                    "send_message_to_user",
                                    {"message": "Checking now."},
                                ),
                                _tool_call(
                                    "call-agent",
                                    "send_message_to_agent",
                                    {
                                        "agent_name": "Email Check",
                                        "instructions": "Check urgent emails.",
                                    },
                                ),
                            ],
                        }
                    }
                ]
            }

        async def fake_execute_tool(tool_call):
            if tool_call.name == "send_message_to_user":
                return ToolResult(
                    success=True,
                    payload={"status": "delivered"},
                    user_message=tool_call.arguments["message"],
                )
            return ToolResult(
                success=True,
                payload={
                    "status": "submitted",
                    "agent": {"name": tool_call.arguments["agent_name"]},
                },
            )

        runtime._make_llm_call = fake_llm_call
        runtime._execute_tool = fake_execute_tool

        summary = await runtime._run_interaction_loop(
            "system",
            [{"role": "user", "content": "Do I have emails?"}],
        )

        assert llm_calls == 1
        assert summary.user_messages == ["Checking now."]
        assert summary.execution_agents == {"Email Check"}

    asyncio.run(run_test())
