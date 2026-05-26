# Persistence

OpenPoke stores local state under `server/data/`. Most persisted data is either append-only XML-like logs or SQLite databases.

The data directory is created by individual services as needed. It is not a separate database layer; each service owns its own files and schema.

## Data Layout

Default persisted paths:

```text
server/data/
  timezone.txt
  triggers.db
  conversation/
    poke_conversation.log
    poke_working_memory.log
  execution_agents/
    agents.sqlite3
    <agent-slug>.log
```

## Conversation Log

Path:

```text
server/data/conversation/poke_conversation.log
```

Owner:

```text
server/services/conversation/log.py
```

The conversation log is append-only and line-oriented. It stores the full transcript used by the interaction agent when summarized working memory is unavailable.

Entry tags:

- `user_message`
- `agent_message`
- `poke_reply`
- `wait`

Each append uses the current user timezone for the timestamp. Payloads are HTML-escaped, and newlines are encoded as `\n`.

## Working Memory Log

Path:

```text
server/data/conversation/poke_working_memory.log
```

Owner:

```text
server/services/conversation/summarization/working_memory_log.py
```

The working-memory log stores summarized conversation state plus recent unsummarized entries.

It starts with:

- `summary_info`: JSON containing `last_index` and `updated_at`.
- `conversation_summary`: current summary text.

Then it stores recent conversation entries using the same line format as the full conversation log.

`write_summary_state()` rewrites this file through a temporary file and atomic replace.

## Execution-Agent Logs

Directory:

```text
server/data/execution_agents/
```

Owner:

```text
server/services/execution/log_store.py
```

Each execution agent gets one slugified `.log` file. For example, an agent named `Email Alice` writes to something like:

```text
server/data/execution_agents/email-alice.log
```

Entry tags:

- `agent_request`: instructions from the interaction agent.
- `agent_action`: actions such as tool calls.
- `tool_response`: tool result summaries.
- `agent_response`: final response from the execution agent.

The execution runtime includes this transcript in future prompts for the same named agent.

## Agent Roster Database

Path:

```text
server/data/execution_agents/agents.sqlite3
```

Owners:

```text
server/services/execution/roster.py
server/services/execution/agent_search.py
```

This SQLite database stores both the execution-agent roster and agent embeddings.

### agents Table

Defined in `server/services/execution/roster.py`.

Columns:

- `id`: integer primary key.
- `name`: unique human-readable agent name.
- `agent_type`: general category such as `email`, `calendar`, `research`, `reminder`, or `general`.
- `status`: typically `active`.
- `created_at`: UTC timestamp string.
- `updated_at`: UTC timestamp string.
- `last_used_at`: UTC timestamp string or null.
- `search_text`: normalized searchable text from name and type.

Indexes exist for type, status, creation time, and last-used time.

SQLite is configured with:

- `PRAGMA foreign_keys=ON`
- `PRAGMA journal_mode=WAL`

### agent_embeddings Table

Defined in `server/services/execution/agent_search.py`.

Columns:

- `id`: integer primary key.
- `agent_id`: unique roster agent ID.
- `model`: embedding model name.
- `dimension`: vector dimension.
- `embedding`: binary vector data.
- `updated_at`: database timestamp.

The table is initialized for vector search through the `sqliteai-vector` extension. Embeddings are stored as FLOAT32 vectors and searched by cosine distance.

## Trigger Database

Path:

```text
server/data/triggers.db
```

Owner:

```text
server/services/triggers/store.py
```

The `triggers` table contains:

- `id`: integer primary key.
- `agent_name`: execution agent that owns and receives the trigger payload.
- `payload`: instructions to run when the trigger fires.
- `start_time`: original schedule start.
- `next_trigger`: next due timestamp in UTC storage format.
- `recurrence_rule`: stored recurrence text with `DTSTART` when recurring.
- `timezone`: timezone used for schedule interpretation.
- `status`: `active`, `paused`, or `completed`.
- `last_error`: last execution error, if any.
- `created_at`: UTC timestamp string.
- `updated_at`: UTC timestamp string.

The table has an index on `(agent_name, next_trigger)`.

`TriggerStore` is the low-level SQLAlchemy persistence class. `TriggerService` owns recurrence logic and status transitions.

## Timezone Store

Path:

```text
server/data/timezone.txt
```

Owner:

```text
server/services/timezone_store.py
```

`TimezoneStore` stores one timezone string. It validates values with `zoneinfo.ZoneInfo` before writing.

The helper module `server/utils/timezones.py` exposes:

- `get_user_timezone_name()`
- `resolve_user_timezone()`
- `now_in_user_timezone()`
- `convert_to_user_timezone()`

If no timezone is stored, services default to UTC.

## Settings Cache

Settings are not persisted under `server/data/`; they are loaded from environment variables and `.env`.

`get_settings()` in `server/config.py` is cached with `functools.lru_cache(maxsize=1)`, so environment changes made after settings are first loaded do not affect the running process unless the process restarts or the cache is cleared by code.

## File Locking

File-backed stores use thread locks:

- `ConversationLog` uses `threading.Lock`.
- `WorkingMemoryLog` uses `threading.Lock`.
- `ExecutionAgentLogStore` uses one lock per slugified agent plus a global lock for lock creation.
- `TimezoneStore` uses `threading.Lock`.

These locks protect in-process concurrent access. They are not inter-process locks.

## SQLite Locking

SQLite-backed services use SQLAlchemy engines and Python locks around operations.

The roster database sets a 30-second connection timeout and WAL journaling. The trigger database sets a 30-second connection timeout but does not explicitly configure WAL in the current code.

These databases are designed for local single-process daemon usage, not high-concurrency multi-process writes.

## State Ownership

Each service owns its own persistence details:

- Conversation services own conversation files.
- Execution services own roster, embeddings, and agent log files.
- Trigger services own trigger database schema and recurrence state.
- Timezone services own the timezone file.
- Calendar and email integrations read external local files but do not write to them.

Agent and tool code should use service APIs rather than writing these files or databases directly.
