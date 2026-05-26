# Conversation And Memory

The conversation layer is the shared boundary between messaging and agents. It accepts user text, creates an interaction-agent runtime, persists conversation entries, and publishes user-visible replies.

## ConversationProcessor

`ConversationProcessor` is defined in `server/services/conversation/processor.py`.

It is intentionally small. Its responsibilities are:

- Trim incoming user text.
- Reject empty messages with `ValueError("Missing user message")`.
- Create an `InteractionAgentRuntime` through a runtime factory.
- Call `runtime.execute(user_message=content)`.

The default runtime factory imports `InteractionAgentRuntime` lazily. That keeps the processor light and avoids importing agent code until a message actually needs to be processed.

The singleton accessor is `get_conversation_processor()`.

## ConversationLog

`ConversationLog` is defined in `server/services/conversation/log.py`.

It is the durable, append-only transcript used by the interaction agent. The default file path is:

```text
server/data/conversation/poke_conversation.log
```

Each line is an XML-like entry with a timestamp attribute:

```xml
<user_message timestamp="2026-05-25 10:30:00">hello</user_message>
<poke_reply timestamp="2026-05-25 10:30:02">hi</poke_reply>
```

Payloads are escaped and normalized so multiline content can be stored on a single line. Newlines are encoded as `\n` in the file and decoded when entries are loaded.

## Conversation Entry Types

The conversation log currently records these tags:

- `user_message`: text sent by the human user.
- `agent_message`: execution-agent results sent back to the interaction agent.
- `poke_reply`: user-visible replies from OpenPoke.
- `wait`: silent marker used to avoid duplicate visible replies.

The interaction prompt treats `user_message` as the only human-authored input, `agent_message` as execution-agent output, and `poke_reply` as prior user-visible assistant responses.

## Recording User Messages

`record_user_message(content)` appends a `user_message` entry to the full conversation log and mirrors that entry into the working-memory log.

This happens inside `InteractionAgentRuntime.execute()` after the runtime has loaded the previous transcript. Loading first is important: the current user message is passed separately as `<new_user_message>`, while older entries are passed as `<conversation_history>`.

## Recording Execution-Agent Messages

`record_agent_message(content)` appends an `agent_message` entry and mirrors it into working memory.

This happens in `InteractionAgentRuntime.handle_agent_message()` when an execution batch finishes and reports results back to the interaction agent.

## Recording Replies

`record_reply(content)` appends a `poke_reply` entry, mirrors it into working memory, and calls `publish_reply(content)` from `server/messaging/context.py`.

This method is the main bridge from conversation state back to external messaging.

If there is an active `ReplyTarget`, subscribers such as `MessagingGateway.deliver_reply()` send the reply externally. If there is no active `ReplyTarget`, the reply is still logged but not sent to a messaging channel.

## Wait Entries

`record_wait(reason)` appends a silent `wait` entry and mirrors it into working memory.

Wait entries are not published to the user. They exist so the interaction agent can mark that it intentionally avoided sending a duplicate response.

## Loading Conversation History

`load_transcript()` reads the full conversation log and renders parsed entries back into XML-like tags for LLM context.

The interaction runtime normally calls `_load_conversation_transcript()`, which prefers the summarized working-memory transcript when summarization is enabled and populated. If working memory is empty or summarization is disabled, it falls back to the full conversation log.

## WorkingMemoryLog

`WorkingMemoryLog` is defined in `server/services/conversation/summarization/working_memory_log.py`.

Its default path is:

```text
server/data/conversation/poke_working_memory.log
```

The working-memory file stores two things:

- A compact conversation summary.
- Recent unsummarized entries that should still be available verbatim.

The file starts with metadata and summary entries:

```xml
<summary_info>{"last_index": -1, "updated_at": null}</summary_info>
<conversation_summary></conversation_summary>
```

Subsequent conversation entries are appended in the same XML-like line format as the full conversation log.

## SummaryState

`SummaryState` is defined in `server/services/conversation/summarization/state.py`.

It contains:

- `summary_text`: the current compact briefing.
- `last_index`: the highest full-log entry index included in the summary.
- `updated_at`: UTC timestamp of the last summary update.
- `unsummarized_entries`: recent entries not yet included in the summary.

`WorkingMemoryLog.load_summary_state()` parses the working-memory file into this object. `write_summary_state()` rewrites the file atomically through a temporary path.

## Summarization Flow

Summarization is implemented in `server/services/conversation/summarization/summarizer.py`.

The flow is:

1. Collect all entries from the full conversation log.
2. Load current `SummaryState` from the working-memory log.
3. Select entries with an index greater than `state.last_index`.
4. If there are fewer than `conversation_summary_threshold + conversation_summary_tail_size` unsummarized entries, do nothing.
5. Summarize the next threshold-sized batch through `request_chat_completion()`.
6. Re-read the full conversation log to avoid dropping entries appended during summarization.
7. Write a new `SummaryState` with updated summary text and remaining unsummarized entries.

The summarization prompt is built by `server/services/conversation/summarization/prompt_builder.py`. It instructs the model to produce a structured working-memory briefing with sections for timeline, pending follow-ups, routines, preferences, and context notes.

## Summarization Scheduling

`ConversationLog._append()` calls `_notify_summarization()` after every successful append.

When summarization is enabled, `_notify_summarization()` imports and calls `schedule_summarization()` from `server/services/conversation/summarization/scheduler.py`.

The scheduler is a lightweight async coalescing worker:

- `_pending` tracks whether a summarization pass is needed.
- `_running` prevents multiple workers from running at once.
- If there is no running event loop, scheduling is skipped.
- While pending work exists, `_run_worker()` calls `summarize_conversation()`.

## Configuration Controls

Summarization is controlled by settings in `server/config.py`:

- `conversation_summary_threshold`: defaults to `100`.
- `conversation_summary_tail_size`: defaults to `10`.

`Settings.summarization_enabled` returns true when `conversation_summary_threshold > 0`.

These settings are currently fields on `Settings`, but unlike most other settings they are not read from environment variables in the current code.

## Conversation-To-Agent Boundary

The conversation layer does not decide how to answer. It only prepares durable state and invokes the interaction agent.

The main handoff is:

```text
ConversationProcessor.process_user_message
  -> InteractionAgentRuntime.execute
  -> ConversationLog.record_user_message
  -> InteractionAgentRuntime._run_interaction_loop
  -> ConversationLog.record_reply
```

For execution-agent callbacks:

```text
ExecutionBatchManager._dispatch_to_interaction_agent
  -> InteractionAgentRuntime.handle_agent_message
  -> ConversationLog.record_agent_message
  -> InteractionAgentRuntime._run_interaction_loop
  -> ConversationLog.record_reply
```
