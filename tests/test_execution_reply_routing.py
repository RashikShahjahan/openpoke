from __future__ import annotations

import asyncio

from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.execution_agent.runtime import ExecutionResult
from server.messaging.context import get_reply_target, set_reply_target
from server.messaging.types import ReplyTarget


class CapturingBatchManager(ExecutionBatchManager):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_payloads: list[str] = []
        self.dispatch_targets: list[ReplyTarget | None] = []

    async def _dispatch_to_interaction_agent(
        self,
        payload: str,
        reply_target: ReplyTarget | None = None,
    ) -> None:
        self.dispatch_payloads.append(payload)
        self.dispatch_targets.append(get_reply_target())


def test_delayed_execution_reply_preserves_original_signal_target() -> None:
    async def run_test() -> None:
        manager = CapturingBatchManager()
        target = ReplyTarget(source="signal", destination="+200")
        set_reply_target(target)
        batch_id = await manager._register_pending_execution("agent", "do work", "request-1")

        set_reply_target(None)
        await manager._complete_execution(
            batch_id,
            ExecutionResult(agent_name="agent", success=True, response="done"),
            "agent",
        )

        assert manager.dispatch_targets == [target]
        assert get_reply_target() is None

    asyncio.run(run_test())


def test_execution_reply_target_remains_empty_without_origin_target() -> None:
    async def run_test() -> None:
        manager = CapturingBatchManager()
        set_reply_target(None)
        batch_id = await manager._register_pending_execution("agent", "do work", "request-1")

        await manager._complete_execution(
            batch_id,
            ExecutionResult(agent_name="agent", success=True, response="done"),
            "agent",
        )

        assert manager.dispatch_targets == [None]
        assert get_reply_target() is None

    asyncio.run(run_test())
