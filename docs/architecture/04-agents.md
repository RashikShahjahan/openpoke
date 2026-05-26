# Agents

OpenPoke uses two agent roles.

The interaction agent owns the user conversation. It decides what to say, when to delegate work, and how to summarize results for the user.

Execution agents perform task work. They use tools, maintain per-agent history, and report results back to the interaction agent. They do not talk directly to the user.

## Interaction Agent Runtime

`InteractionAgentRuntime` is defined in `server/agents/interaction_agent/runtime.py`.

It is the primary conversation brain. It is created for each user turn and for each execution-agent callback.

On construction, it loads:

- Settings from `get_settings()`.
- OpenRouter API key and interaction model.
- The singleton `ConversationLog`.
- The singleton `WorkingMemoryLog`.
- Interaction-agent tool schemas from `get_tool_schemas()`.

It raises `ValueError` if `OPENROUTER_API_KEY` is missing.

## User Message Execution

`execute(user_message)` handles human-authored messages.

The flow is:

1. Load the prior conversation transcript.
2. Record the new user message.
3. Build the system prompt.
4. Build a structured user message containing conversation history, execution-agent guidance, and `<new_user_message>`.
5. Run the interaction loop.
6. Choose the final response.
7. If no tool already recorded a visible user message, record the final assistant text as a reply.

The returned `InteractionResult` includes success state, response text, error text if any, and the number of execution agents used.

## Execution-Agent Callback Handling

`handle_agent_message(agent_message)` handles results from execution agents.

It mirrors the user-message flow but records an `agent_message` entry and wraps the current turn in `<new_agent_message>` instead of `<new_user_message>`.

The interaction prompt tells the model to treat `<new_agent_message>` blocks as execution-agent results and summarize them for the user.

## Prompt Construction

Prompt helpers are in `server/agents/interaction_agent/agent.py`.

`build_system_prompt()` returns the static markdown prompt from `system_prompt.md`.

`prepare_message_with_history(latest_text, transcript, message_type)` creates a single user message with three sections:

```xml
<conversation_history>
...
</conversation_history>

<execution_agents>
...
</execution_agents>

<new_user_message>
...
</new_user_message>
```

For execution-agent callbacks, the final tag is `<new_agent_message>`.

The `<execution_agents>` section does not list all agent names. It reports whether any active execution agents exist and instructs the model to use SQL and vector search before reusing an agent ID.

## Interaction Loop

`_run_interaction_loop()` is the interaction agent's LLM/tool loop.

For up to `MAX_TOOL_ITERATIONS = 8`, it:

1. Calls OpenRouter through `_make_llm_call()`.
2. Extracts the assistant message.
3. Parses tool calls.
4. Appends the assistant message to the local message list.
5. Executes tool calls in order.
6. Appends tool results back into the message list.
7. Stops if there are no tool calls.
8. Stops early if an execution agent was successfully dispatched.

The early stop after `send_message_to_agent` is important. It allows the interaction agent to acknowledge the user immediately, then wait for asynchronous execution-agent results before producing the final answer.

## Interaction-Agent Tools

Interaction-agent tools are defined in `server/agents/interaction_agent/tools.py`.

The tool surface is:

- `send_message_to_agent`: create or reuse an execution agent and dispatch instructions asynchronously.
- `send_message_to_user`: record and publish an immediate visible reply.
- `query_agents_sql`: run guarded read-only SQL against the agent roster.
- `vector_search_agents`: semantically search active execution agents.
- `send_draft`: record an email draft-like reply for user review; it does not send email.
- `wait`: record a silent wait marker.

Tool functions return a `ToolResult` with:

- `success`: whether the tool succeeded.
- `payload`: structured result or error data.
- `user_message`: visible message text when applicable.
- `recorded_reply`: whether the tool already wrote to the conversation log.

## User-Facing Response Selection

`_finalize_response()` chooses the final visible response from the loop summary.

If any tool produced `user_message`, the last one wins. Otherwise, the runtime uses the last assistant text returned by the LLM.

If the final response came from plain assistant text rather than `send_message_to_user`, `execute()` or `handle_agent_message()` records it with `ConversationLog.record_reply()`.

## Execution Agent Runtime

`ExecutionAgentRuntime` is defined in `server/agents/execution_agent/runtime.py`.

Each runtime handles one request for one named execution agent.

On construction, it loads:

- Settings from `get_settings()`.
- An `ExecutionAgent` object for the agent name.
- OpenRouter API key and execution model.
- Tool registry bound to the agent name.
- Execution-agent tool schemas.

It raises `ValueError` if `OPENROUTER_API_KEY` is missing.

## Execution Flow

`execute(instructions)` performs the execution-agent loop:

1. Build the execution system prompt with agent history.
2. Start the LLM message list with the interaction agent's instructions.
3. Call OpenRouter.
4. Parse tool calls.
5. Execute requested tools with a 30-second per-tool timeout.
6. Append tool results back to the LLM message list.
7. Repeat up to `MAX_TOOL_ITERATIONS = 8`.
8. Record the final response in the agent log.
9. Return an `ExecutionResult`.

If execution fails, the runtime records an error response in the agent log and returns an unsuccessful `ExecutionResult`.

## ExecutionAgent State

`ExecutionAgent` is defined in `server/agents/execution_agent/agent.py`.

It owns prompt construction and history access for a named agent.

`build_system_prompt()` fills the execution-agent prompt template with:

- `agent_name`: the persisted agent name.
- `agent_purpose`: derived as `Handle tasks related to: <agent name>`.

`build_system_prompt_with_history()` appends the agent's execution log transcript under `# Execution History` when history exists.

The optional `conversation_limit` can limit history by number of recent `agent_request` entries, though current runtime construction does not pass a limit.

## Execution Batch Manager

`ExecutionBatchManager` is defined in `server/agents/execution_agent/batch_manager.py`.

It coordinates asynchronous execution-agent calls and sends one combined result payload back to the interaction agent when all calls from a batch are complete.

The interaction-agent tool module owns a global `_EXECUTION_BATCH_MANAGER`. Calls to `send_message_to_agent()` schedule work through this shared manager.

Batching is useful when the interaction agent issues multiple independent execution-agent calls in one turn. The manager waits until all pending calls in the batch finish, then formats results like:

```text
[SUCCESS] Agent Name: result text
[FAILED] Other Agent: error text
```

That combined payload is passed to `InteractionAgentRuntime.handle_agent_message()`.

## Reply Target Preservation

`ExecutionBatchManager._register_pending_execution()` captures the current messaging `ReplyTarget` with `get_reply_target()` when the batch starts.

When the batch completes, `_complete_execution()` temporarily restores that target with `set_reply_target(reply_target)` while dispatching the agent result back into the interaction agent.

This allows delayed execution-agent results to reply to the original Signal conversation even though they finish after the inbound Signal handler has returned.

## Agent Roster

The persistent execution-agent roster is implemented in `server/services/execution/roster.py`.

It stores agents in SQLite at:

```text
server/data/execution_agents/agents.sqlite3
```

The `agents` table contains:

- `id`
- `name`
- `agent_type`
- `status`
- `created_at`
- `updated_at`
- `last_used_at`
- `search_text`

`send_message_to_agent()` uses the roster as follows:

1. If a positive `agent_id` is provided, load that active agent.
2. If the ID is missing, invalid, unknown, or non-positive, create or reuse by `agent_name`.
3. If no name is provided, generate a default name from the instructions and agent type.
4. Touch the agent to update recency metadata.
5. Record the request in the execution-agent log.
6. Schedule async execution.

## Roster SQL Search

`query_agents_sql()` calls `AgentRoster.query_readonly()`.

The SQL guard allows a single `SELECT` or `WITH` query and rejects:

- Empty SQL.
- Semicolons.
- Non-read queries.
- Dangerous keywords such as `insert`, `update`, `delete`, `drop`, `pragma`, and `attach`.

Results are capped to a maximum of 100 rows.

## Vector Search

`AgentSearchIndex` is implemented in `server/services/execution/agent_search.py`.

It stores embeddings in the same SQLite database as the roster, in the `agent_embeddings` table.

Important details:

- Default embedding model is `openai/text-embedding-3-small`.
- Embeddings are requested through `request_embeddings()`.
- Vector search uses the `sqliteai-vector` extension.
- Only active agents are returned.
- Search can be constrained to candidate IDs found by SQL.

New roster agents schedule embedding creation when they are added inside a running event loop.

## Execution Logs

Execution logs are managed by `ExecutionAgentLogStore` in `server/services/execution/log_store.py`.

Logs live under:

```text
server/data/execution_agents/*.log
```

Each agent gets a slugified log file. Entries are XML-like lines with tags:

- `agent_request`: instructions from the interaction agent.
- `agent_action`: tool call descriptions or other action summaries.
- `tool_response`: tool result summary.
- `agent_response`: final execution-agent response.

Calendar and email tool responses are redacted before being written to execution logs. This avoids unnecessarily persisting local calendar events or email contents in long-term agent history.
