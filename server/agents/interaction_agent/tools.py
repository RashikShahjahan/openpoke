"""Tool definitions for interaction agent."""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.execution import (
    AgentRecord,
    get_agent_roster,
    get_agent_search_index,
    get_execution_agent_logs,
)
from ..execution_agent.batch_manager import ExecutionBatchManager


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for OpenRouter
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to an execution agent. Pass agent_id to reuse an existing roster agent, or pass agent_name and optional agent_type to create a new one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "integer",
                        "description": "Existing agent id from query_agents_sql or vector_search_agents. Prefer this when reusing an agent.",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "Human-readable name for a new agent, e.g. 'Email to Alice' or 'Vercel Job Offer'. Required when agent_id is not provided.",
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "Optional type for a new agent, e.g. 'email', 'calendar', 'research', 'reminder', or 'general'. Defaults to 'general'.",
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_agents_sql",
            "description": "Run a read-only SELECT query against the execution-agent roster. Use for exact filters by id, name/search_text, agent_type, status, created_at, updated_at, or last_used_at. Table schema: agents(id, name, agent_type, status, created_at, updated_at, last_used_at, search_text).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single read-only SELECT or WITH query. Do not include semicolons. Use search_text for normalized name/entity matching.",
                    },
                    "params": {
                        "type": "array",
                        "description": "Optional positional SQL parameters for ? placeholders.",
                        "items": {},
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum rows to return. Defaults to 50 and is capped at 100.",
                    },
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vector_search_agents",
            "description": "Semantic search over execution agents. Use when matching task meaning/context; pass agent_ids from query_agents_sql when SQL should narrow the candidate set first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of the task, person, thread, project, or context.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum agents to return. Defaults to 5.",
                    },
                    "agent_ids": {
                        "type": "array",
                        "description": "Optional candidate agent ids from SQL filtering.",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()


# Create or reuse execution agent and dispatch instructions asynchronously
def send_message_to_agent(
    instructions: str,
    agent_id: int | None = None,
    agent_name: str | None = None,
    agent_type: str = "general",
) -> ToolResult:
    """Send instructions to an execution agent."""

    roster = get_agent_roster()
    is_new = False

    if agent_id is not None:
        try:
            record = roster.get_agent(int(agent_id))
        except (TypeError, ValueError):
            record = None
        if record is None:
            return ToolResult(success=False, payload={"error": f"Unknown agent_id: {agent_id}"})
        if record.status != "active":
            return ToolResult(success=False, payload={"error": f"Agent is not active: {agent_id}"})
    else:
        if not agent_name or not agent_name.strip():
            return ToolResult(
                success=False,
                payload={"error": "Provide agent_id to reuse an agent or agent_name to create one"},
            )
        existing = roster.get_agent_by_name(agent_name.strip())
        record = existing or roster.add_agent(agent_name, agent_type=agent_type)
        is_new = existing is None

    touched = roster.touch_agent(record.id)
    if touched is not None:
        record = touched

    get_execution_agent_logs().record_request(record.name, instructions)

    action = "Created" if is_new else "Reused"
    logger.info(f"{action} agent: {record.name}")

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(record.name, instructions)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent '{record.name}' completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Agent '%s' failed: %s", record.name, exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    loop.create_task(_execute_async())

    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent": _agent_payload(record),
            "new_agent_created": is_new,
        },
    )


def query_agents_sql(
    sql: str,
    params: list[Any] | None = None,
    limit: int = 50,
) -> ToolResult:
    """Run a guarded read-only query against the agent roster."""

    try:
        rows, truncated = get_agent_roster().query_readonly(sql, params, limit=limit)
    except Exception as exc:
        return ToolResult(success=False, payload={"error": str(exc)})

    return ToolResult(
        success=True,
        payload={
            "rows": rows,
            "truncated": truncated,
        },
    )


async def vector_search_agents(
    query: str,
    limit: int = 5,
    agent_ids: list[int] | None = None,
) -> ToolResult:
    """Search existing execution agents by semantic similarity."""

    try:
        results = await get_agent_search_index().vector_search_agents(
            query,
            limit=limit,
            agent_ids=agent_ids,
        )
    except Exception as exc:
        return ToolResult(success=False, payload={"error": str(exc)})
    return ToolResult(
        success=True,
        payload={
            "query": query,
            "agents": [_agent_payload(result) for result in results],
        },
    )


def _agent_payload(record: AgentRecord) -> dict[str, Any]:
    return record.to_dict()


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    return ToolResult(
        success=True,
        payload={
            "status": "draft_recorded",
            "to": to,
            "subject": subject,
        },
        recorded_reply=True,
    )


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()
    
    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)
    

    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


# Return predefined tool schemas for LLM function calling
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas."""
    return TOOL_SCHEMAS


# Route tool calls to appropriate handlers with argument validation and error handling
async def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if not isinstance(arguments, dict):
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "send_message_to_agent":
            return send_message_to_agent(**arguments)
        if name == "query_agents_sql":
            return query_agents_sql(**arguments)
        if name == "vector_search_agents":
            return await vector_search_agents(**arguments)
        if name == "send_message_to_user":
            return send_message_to_user(**arguments)
        if name == "send_draft":
            return send_draft(**arguments)
        if name == "wait":
            return wait(**arguments)

        logger.warning("unexpected tool", extra={"tool": name})
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
