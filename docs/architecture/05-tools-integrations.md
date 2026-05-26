# Tools And Integrations

Execution agents use tools to interact with local data and external resources. Tool schemas are exposed to the LLM, while Python callables perform the actual work.

The registry boundary lives in `server/agents/execution_agent/tools/registry.py`.

## Tool Registry

`get_tool_schemas()` combines schemas from:

- Task tools from `server/agents/execution_agent/tasks`.
- Calendar tools.
- Email tools.
- Web tools.
- Trigger tools.

`get_tool_registry(agent_name)` returns executable callables keyed by tool name. Calendar, email, web, and trigger tools are all bound to the current `agent_name` so they can write execution-log entries under the correct agent.

## Task Registry Extension Point

`server/agents/execution_agent/tasks/__init__.py` currently returns no task schemas and no task callables.

This is the intended extension point for first-class task tools that do not belong to calendar, email, web, or triggers.

To add task tools, implement:

- `get_task_schemas()` to return OpenAI/OpenRouter-compatible tool schemas.
- `get_task_registry(agent_name)` to return matching Python callables.

The central execution tool registry already includes both functions.

## Calendar Tools

Calendar tool definitions are in `server/agents/execution_agent/tools/calendar.py`.

They use `get_calendar_service()` from `server/services/calendar/__init__.py`, which creates a `LocalIcsCalendarService` from settings.

Available tools:

- `calendarConnectionStatus`: checks whether the configured local calendar file is present and readable.
- `listCalendarEvents`: lists events overlapping an ISO 8601 time range.
- `getCalendarAvailability`: checks whether any non-transparent, non-cancelled events overlap a range.

### Calendar Service

`LocalIcsCalendarService` is implemented in `server/services/calendar/service.py`.

It is read-only and backed by a local `.ics` file configured with `OPENPOKE_CALENDAR_ICS_PATH`.

Important behavior:

- Reads and parses one local `.ics` file.
- Caches parsed events based on file modification time and `OPENPOKE_CALENDAR_REFRESH_SECONDS`.
- Interprets naive query timestamps in the stored user timezone.
- Returns event timestamps normalized to UTC ISO strings.
- Supports all-day events.
- Treats missing `DTEND` as one day for all-day events or same start time for timed events.
- Expands basic recurring events using `RRULE`, capped at 1000 occurrences.
- Availability excludes transparent and cancelled events.

Calendar DTOs live in `server/services/calendar/models.py`:

- `CalendarEvent`
- `Availability`

Calendar event payloads include `id`, `summary`, `start`, `end`, `all_day`, and optional `location` and `status`.

## Email Tools

Email tool definitions are in `server/agents/execution_agent/tools/email.py`.

They use `get_email_service()` from `server/services/email/__init__.py`, which creates a `ThunderbirdEmailService` from settings.

Available tools:

- `emailConnectionStatus`: checks whether local Thunderbird email access is configured and has folders.
- `listEmailFolders`: lists discovered local mbox folders, optionally with message counts.
- `searchEmails`: searches local messages and returns lightweight summaries.
- `getEmailMessage`: reads one full message by OpenPoke email ID or RFC Message-ID.

### Email Service

`ThunderbirdEmailService` is implemented in `server/services/email/service.py`.

It is read-only and backed by local Thunderbird mbox files.

Important behavior:

- Uses `OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH` when configured.
- Auto-detects common Thunderbird profile locations when the env var is unset.
- Discovers mbox files under `Mail/` and `ImapMail/`.
- Ignores Thunderbird `.msf` index files and metadata-like files.
- Caches discovered folders based on `OPENPOKE_EMAIL_REFRESH_SECONDS`.
- Searches newest messages first within each mbox.
- Caps search results to 50.
- Caps full message body reads to 100,000 characters.
- Converts email timestamps to the stored user timezone.

Search supports:

- Full-text terms across subject, sender, recipients, and body.
- Folder matching by ID or name.
- Sender, recipient, and subject substrings.
- Start and end timestamps.
- Attachment presence.
- Canonical filters: `inbox`, `sent`, `spam`, `read`, `unread`, `unarchived`, and `trash`.

Email DTOs live in `server/services/email/models.py`:

- `EmailFolder`
- `EmailMessage`

`searchEmails` omits full bodies and returns snippets. `getEmailMessage` includes `clean_text`.

## Email Text Cleaning

`EmailTextCleaner` extracts readable text from RFC 822 email messages.

It prefers `text/plain` body parts. If no plain text exists, it converts `text/html` using a small HTML parser that skips script, style, head, title, metadata, and noscript content.

Post-processing normalizes whitespace and removes common email boilerplate patterns such as browser-view prompts and unsubscribe preference text.

## Web Tool

The web tool is defined in `server/agents/execution_agent/tools/web.py`.

Available tool:

- `fetchUrl`: fetches text content from an absolute HTTP or HTTPS URL.

Important behavior:

- Rejects non-HTTP(S) URLs and relative URLs.
- Uses `httpx.AsyncClient` with redirects enabled.
- Sends `User-Agent: OpenPoke/1.0`.
- Defaults to 100,000 response bytes.
- Caps responses at 200,000 bytes.
- Returns URL, final URL, status code, content type, content, and whether the response was truncated.

The tool does not parse pages into markdown or structured data. It returns decoded response text.

## Trigger Tools

Trigger tool definitions are in `server/agents/execution_agent/tools/triggers.py`.

They use:

- `get_trigger_service()` from `server/services/triggers/__init__.py`.
- `get_timezone_store()` from `server/services/timezone_store.py`.
- `get_execution_agent_logs()` for execution-log action summaries.

Available tools:

- `createTrigger`: create a reminder trigger for the current execution agent.
- `updateTrigger`: update, pause, resume, or complete an existing trigger owned by the current execution agent.
- `listTriggers`: list triggers belonging to the current execution agent.

### Trigger Payloads

Triggers are scoped to an execution agent and contain:

- `agent_name`
- `payload`
- `start_time`
- `next_trigger`
- `recurrence_rule`
- `timezone`
- `status`
- `last_error`
- timestamps

The trigger payload is raw instruction text that will be sent back to the same execution agent when the trigger fires.

### Recurrence Handling

`TriggerService` in `server/services/triggers/service.py` computes first and next fire times.

Recurring schedules use iCalendar `RRULE` text. `build_recurrence()` in `server/services/triggers/utils.py` embeds a `DTSTART` line into the stored recurrence text so future occurrences can be computed reliably.

Statuses are normalized to one of:

- `active`
- `paused`
- `completed`

Invalid statuses raise `ValueError`.

## Trigger Scheduler

`TriggerScheduler` is covered in [Runtime](./01-runtime.md), but it is tightly connected to trigger tools.

When a due trigger is found, the scheduler formats instructions like:

```text
Trigger fired at <timestamp> (UTC).
Scheduled occurrence time: <timestamp>.

Metadata:
- Trigger ID: <id>
- Recurrence: <rrule>
- Timezone: <timezone>
- Start Time (UTC): <timestamp>

Payload:
<trigger payload>
```

Those instructions are sent to `ExecutionBatchManager.execute_agent(trigger.agent_name, instructions)`.

On success:

- Recurring triggers advance to the next occurrence.
- One-shot triggers are marked completed.

On failure:

- `last_error` is recorded.
- Recurring triggers still advance.
- One-shot triggers have `next_trigger` cleared.

## Tool Execution Semantics

Execution-agent tool calls are handled by `ExecutionAgentRuntime._execute_tool()`.

The runtime:

- Looks up the callable by tool name.
- Returns an error payload for unknown tools.
- Wraps execution in `asyncio.wait_for()` with a 30-second timeout.
- Calls async functions directly.
- Runs sync functions in a worker thread with `asyncio.to_thread()`.
- Converts exceptions into structured error payloads.

Each tool result is returned to the LLM as JSON containing the tool name, status, arguments, and either `result` or `error`.

## Tool Logging And Redaction

Execution agents record tool activity through `ExecutionAgent.record_tool_execution()`.

Calendar and email tool responses are redacted in execution logs:

- Calendar responses become `<calendar result redacted>`.
- Email responses become `<email result redacted>`.

Other tool responses are truncated to 500 characters before being stored.

This gives future executions useful action history without persisting sensitive local event or email contents unnecessarily.
