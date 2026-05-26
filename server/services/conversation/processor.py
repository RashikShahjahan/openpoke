from __future__ import annotations

from typing import Callable, Optional, TypeVar

RuntimeT = TypeVar("RuntimeT")


def _create_interaction_runtime() -> object:
    from ...agents.interaction_agent.runtime import InteractionAgentRuntime

    return InteractionAgentRuntime()


class ConversationProcessor:
    """Shared entry point for user-message processing."""

    def __init__(
        self,
        runtime_factory: Callable[[], RuntimeT] = _create_interaction_runtime,
    ) -> None:
        self._runtime_factory = runtime_factory

    def create_runtime(self) -> RuntimeT:
        return self._runtime_factory()

    async def process_user_message(
        self,
        user_message: str,
        runtime: Optional[RuntimeT] = None,
    ) -> object:
        content = user_message.strip()
        if not content:
            raise ValueError("Missing user message")

        active_runtime = runtime if runtime is not None else self.create_runtime()
        return await active_runtime.execute(user_message=content)  # type: ignore[attr-defined]


_conversation_processor = ConversationProcessor()


def get_conversation_processor() -> ConversationProcessor:
    return _conversation_processor


__all__ = ["ConversationProcessor", "get_conversation_processor"]
