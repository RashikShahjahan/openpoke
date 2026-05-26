# Configuration And Operations

OpenPoke is configured through environment variables, with optional loading from a repository-level `.env` file.

Configuration is centralized in `server/config.py`.

## Settings Loading

When `server/config.py` is imported, `_load_env_file()` attempts to read:

```text
.env
```

from the repository root. Each non-comment `KEY=value` line is inserted into `os.environ` only if the key is not already present.

`Settings` then reads values from `os.environ` into a Pydantic model. `get_settings()` caches a single `Settings` instance with `lru_cache(maxsize=1)`.

Operational implication: restart the process after changing environment variables or `.env`.

## Required LLM Configuration

The interaction and execution runtimes require an API key in:

```text
OPENROUTER_API_KEY
```

The current settings model does not read `OPENPOKE_LLM_API_KEY`; chat calls use `OPENROUTER_API_KEY` even when `OPENPOKE_LLM_BASE_URL` points at another OpenAI-compatible endpoint.

Chat completions also require an LLM base URL and model. The current code reads:

- `OPENPOKE_LLM_BASE_URL`
- `OPENPOKE_INTERACTION_AGENT_MODEL` or fallback `OPENPOKE_LLM_MODEL`
- `OPENPOKE_EXECUTION_AGENT_MODEL` or fallback `OPENPOKE_LLM_MODEL`
- `OPENPOKE_SUMMARIZER_MODEL` or fallback `OPENPOKE_LLM_MODEL`

If the API key is missing, agent runtime construction fails. If the model or base URL is missing, the OpenRouter client raises `OpenRouterError` when a request is made.

## Embeddings Configuration

Agent vector search uses embeddings through:

- `OPENPOKE_EMBEDDINGS_BASE_URL`
- `OPENPOKE_EMBEDDINGS_API_KEY` or fallback `OPENROUTER_API_KEY`

The embedding model used by `AgentSearchIndex` defaults to:

```text
openai/text-embedding-3-small
```

`OPENPOKE_EXECUTION_AGENT_SEARCH_MODEL` exists in settings but is not currently used by `AgentSearchIndex`; the index has its own hardcoded default embedding model.

## Signal Configuration

Signal support is configured with:

- `OPENPOKE_SIGNAL_HTTP_URL`, default `http://127.0.0.1:8080`.
- `OPENPOKE_SIGNAL_ACCOUNT`, required to create a Signal adapter.
- `OPENPOKE_SIGNAL_ALLOWED_SENDERS`, comma-separated allowlist.

If `OPENPOKE_SIGNAL_ACCOUNT` is missing, the gateway logs a warning and starts without a Signal adapter.

If the Signal adapter is created but its health check fails, the gateway logs a warning, closes the adapter, and continues running without Signal.

An empty sender allowlist denies all inbound Signal messages.

## Calendar Configuration

Calendar access is optional and read-only.

Environment variables:

- `OPENPOKE_CALENDAR_ICS_PATH`: absolute or `~`-expanded path to a local `.ics` file.
- `OPENPOKE_CALENDAR_REFRESH_SECONDS`, default `60`.

If the calendar path is missing, `calendarConnectionStatus` reports `missing_path`, and calendar event/availability calls raise an error that the tool converts into an error payload.

## Email Configuration

Email access is optional and read-only.

Environment variables:

- `OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH`: absolute or `~`-expanded path to a Thunderbird profile.
- `OPENPOKE_EMAIL_REFRESH_SECONDS`, default `60`.

If the profile path is unset, OpenPoke attempts to auto-detect a Thunderbird profile from common locations:

- `~/Library/Thunderbird/Profiles`
- `~/.thunderbird`
- `~/.mozilla-thunderbird`

If no profile is found, `emailConnectionStatus` reports `missing_profile`, and email folder/search/read calls raise an error that the tool converts into an error payload.

## Summarization Configuration

Summarization settings are fields on `Settings`:

- `conversation_summary_threshold`, default `100`.
- `conversation_summary_tail_size`, default `10`.

`Settings.summarization_enabled` is true when `conversation_summary_threshold > 0`.

These fields are not currently wired to environment variables, so changing them requires code-level configuration rather than `.env` changes.

## External Dependencies

Python dependencies are listed in `server/requirements.txt`:

- `pydantic`
- `httpx`
- `python-dateutil`
- `icalendar`
- `SQLAlchemy`
- `sqliteai-vector`

Operational external services and local resources:

- An OpenAI-compatible chat completion endpoint, normally OpenRouter.
- An OpenAI-compatible embeddings endpoint for vector search.
- signal-cli direct HTTP daemon for Signal messaging.
- Local `.ics` file for calendar tools.
- Local Thunderbird mbox profile for email tools.
- Native `sqliteai-vector` extension binary for vector search.

## OpenRouter Client Boundary

`server/openrouter_client/client.py` provides two async functions:

- `request_chat_completion()` posts to `{OPENPOKE_LLM_BASE_URL}/chat/completions`.
- `request_embeddings()` posts to `{OPENPOKE_EMBEDDINGS_BASE_URL}/embeddings`.

The client always sends:

- `Authorization: Bearer <key>`
- `Content-Type: application/json`
- `Accept: application/json`

Chat completions are non-streaming. Tool schemas are included in the payload when supplied.

Errors from HTTP status failures are normalized into `OpenRouterError` with response details where available.

## Running The Daemon

The code entrypoint supports running the daemon as a module:

```bash
python -m server.server
```

The docs in `docs/signal.md` mention a `--require-signal` flag, but the current `server/server.py` does not parse CLI arguments. In the current code, Signal unavailability logs a warning and the process continues.

## Signal Daemon Requirement

For Signal messaging, start signal-cli's HTTP daemon separately:

```bash
signal-cli --http 127.0.0.1:8080 daemon
```

OpenPoke then connects to that daemon through `OPENPOKE_SIGNAL_HTTP_URL`.

## Operational Flow For A User Message

1. Signal sends an SSE event through signal-cli's HTTP daemon.
2. `SignalAdapter` filters and normalizes the event.
3. `MessagingGateway` stores a reply target and calls `ConversationProcessor`.
4. `InteractionAgentRuntime` records the user message and calls the LLM.
5. The interaction agent either replies directly or dispatches execution agents.
6. Direct replies are recorded and published immediately.
7. Execution-agent replies are batched, routed back through the interaction agent, recorded, and published to the original reply target.

## Operational Flow For A Trigger

1. `TriggerScheduler` polls due triggers.
2. Each due trigger is dispatched to the owning execution agent.
3. The execution agent performs work and returns a result.
4. The batch manager sends the result to the interaction agent.
5. The interaction agent records a reply.
6. If no reply target exists, the reply remains in logs only.
7. The trigger is advanced, completed, or marked with an error depending on execution outcome.

## Security And Privacy Model

OpenPoke is designed as a local assistant daemon with local state.

Important properties:

- Signal inbound messages are allowlisted by sender phone number.
- Calendar integration reads only the configured `.ics` file.
- Email integration reads local Thunderbird mbox files and never writes to them.
- Email and calendar tool responses are redacted from execution-agent logs.
- Conversation logs and working memory are stored locally in plaintext.
- Execution-agent logs are stored locally in plaintext, except for redacted email/calendar tool responses.
- SQLite databases are stored locally without encryption.

If the local machine is shared or untrusted, `server/data/` should be treated as sensitive.

## Current Limitations

- Signal is the only implemented messaging adapter.
- Signal support is text-only; there is no group, attachment, typing indicator, reaction, or rich formatting support in the adapter.
- There is no HTTP API server in the current codebase.
- There is no explicit graceful drain of in-flight execution agents during daemon shutdown.
- Trigger-fired replies may not reach a user if there is no active messaging reply target.
- Calendar access is read-only and limited to one local `.ics` file.
- Email access is read-only and limited to Thunderbird mbox storage.
- Vector search requires the `sqliteai-vector` native extension to load successfully.
- Settings are cached after first load.
- Summarization threshold settings are not currently environment-variable backed.

## Where To Change Things

Common change points:

- Add a messaging channel: `server/messaging/*` plus gateway subscription/delivery logic.
- Change interaction behavior: `server/agents/interaction_agent/system_prompt.md` or runtime/tool logic.
- Add an execution tool: `server/agents/execution_agent/tools/*` and `registry.py`, or the task registry extension point.
- Change local calendar behavior: `server/services/calendar/service.py`.
- Change local email behavior: `server/services/email/service.py`.
- Change trigger recurrence or lifecycle: `server/services/triggers/*` and `server/services/trigger_scheduler.py`.
- Change agent persistence/search: `server/services/execution/*`.
- Change LLM transport: `server/openrouter_client/client.py`.
