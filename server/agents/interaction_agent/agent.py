"""Interaction agent helpers for prompt construction."""

from pathlib import Path
from typing import Dict, List

from ...services.execution import get_agent_roster

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Return the static system prompt for the interaction agent."""
    return SYSTEM_PROMPT


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, agent guidance, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(f"<execution_agents>\n{_render_execution_agent_guidance()}\n</execution_agents>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Format execution-agent availability without listing every agent name.
def _render_execution_agent_guidance() -> str:
    roster = get_agent_roster()
    agent_count = len(roster.list_agents(status="active"))

    if agent_count == 0:
        return "No existing execution agents. Create one with send_message_to_agent when needed."

    return (
        f"{agent_count} existing execution agents are available. "
        "Use query_agents_sql for structured filters and vector_search_agents for semantic matching before reusing one."
    )


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
