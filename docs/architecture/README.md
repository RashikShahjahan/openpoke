# OpenPoke Architecture

This documentation explains OpenPoke from the process entrypoint down to the service and persistence layers. Read the files in order if you want the full architecture story, or jump to a specific subsystem when changing code.

OpenPoke is a Python messaging daemon. It receives text messages, routes them through a user-facing interaction agent, delegates work to execution agents when needed, persists conversation and agent memory locally, and sends replies back through the originating messaging channel.

## Reading Order

1. [Runtime](./01-runtime.md) explains process startup, shutdown, and background services.
2. [Messaging](./02-messaging.md) explains Signal ingress, outbound reply delivery, and reply-target context.
3. [Conversation And Memory](./03-conversation-memory.md) explains the shared user-message entrypoint, conversation logs, working memory, and summarization.
4. [Agents](./04-agents.md) explains the interaction agent, execution agents, batching, agent roster, vector search, and execution logs.
5. [Tools And Integrations](./05-tools-integrations.md) explains the execution-agent tool surface and local integrations.
6. [Persistence](./06-persistence.md) explains files, SQLite databases, and local state ownership.
7. [Configuration And Operations](./07-configuration-operations.md) explains environment variables, external dependencies, operating assumptions, and limitations.

## System Shape

At runtime, OpenPoke has four main boundaries:

- Messaging boundary: `server/messaging/*` normalizes external messages and routes replies.
- Conversation boundary: `server/services/conversation/*` owns the user transcript, visible replies, and working memory.
- Agent boundary: `server/agents/*` owns LLM orchestration, task delegation, and tool execution.
- Service boundary: `server/services/*` owns local integrations, SQLite-backed stores, and file-backed state.

The OpenRouter client in `server/openrouter_client/*` is the LLM and embeddings transport boundary used by agents, summarization, and vector search.

## Main Request Flow

```text
Signal HTTP daemon
  -> SignalAdapter
  -> MessagingGateway
  -> ConversationProcessor
  -> InteractionAgentRuntime
  -> OpenRouter chat completion
  -> interaction-agent tools
  -> ConversationLog.record_reply
  -> messaging context publish_reply
  -> SignalAdapter.send
```

When work is delegated to execution agents, the flow adds an asynchronous branch:

```text
InteractionAgentRuntime
  -> send_message_to_agent tool
  -> ExecutionBatchManager
  -> ExecutionAgentRuntime
  -> execution-agent tools/services
  -> batch result back to InteractionAgentRuntime.handle_agent_message
  -> ConversationLog.record_reply
  -> original messaging reply target
```

When a reminder trigger fires, the flow starts from the scheduler instead of a user message:

```text
TriggerScheduler
  -> TriggerService.get_due_triggers
  -> ExecutionBatchManager
  -> ExecutionAgentRuntime
  -> batch result back to InteractionAgentRuntime.handle_agent_message
```

Trigger-fired executions do not necessarily have an active messaging reply target, so final replies may be logged without being delivered to Signal.

## Important Design Choices

- The daemon is messaging-first; there is no web application router in the current codebase.
- Signal is the only implemented messaging adapter.
- The interaction agent is the only component that talks to the user in natural language.
- Execution agents perform task work and report results back to the interaction agent.
- Calendar and email integrations are read-only.
- Conversation memory is local and file-backed.
- Agent roster, agent embeddings, and triggers are SQLite-backed.
- LLM calls are OpenAI-compatible HTTP calls through configurable base URLs.
