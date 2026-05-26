# Runtime

OpenPoke runs as an async Python daemon. The process starts background services, waits for a termination signal, and stops those services in reverse order.

## Entrypoint

The CLI entrypoint is `server/server.py`.

`main()` performs two actions:

- Configures logging with `configure_logging()` from `server/logging_config.py`.
- Starts the async runtime with `asyncio.run(run_daemon())`.

`run_daemon()` creates and starts the two long-running services:

- `TriggerScheduler` from `server/services/trigger_scheduler.py`.
- `MessagingGateway` from `server/messaging/gateway.py`.

It also registers `SIGINT` and `SIGTERM` handlers that set an `asyncio.Event`. The daemon remains alive until that event is set.

## Startup Order

Startup order is intentional:

1. `get_trigger_scheduler()` returns the singleton scheduler.
2. `get_messaging_gateway()` returns the singleton gateway.
3. `scheduler.start()` launches the trigger polling task.
4. `gateway.start()` launches messaging listeners.
5. The process waits for a shutdown signal.

The scheduler is started before the gateway, so stored triggers can begin polling even if Signal is unavailable.

## Shutdown Order

Shutdown reverses startup:

1. `gateway.stop()` unsubscribes reply callbacks, cancels the Signal listener task, and closes the Signal HTTP client.
2. `scheduler.stop()` cancels the trigger scheduler task.

The code does not explicitly drain in-flight execution-agent tasks on process shutdown. Long-running task work is coordinated by `ExecutionBatchManager`, but daemon shutdown only stops the gateway and scheduler services directly.

## Logging

`server/logging_config.py` configures the root logging setup with `INFO` level and a timestamped format. The shared application logger is `openpoke.server`.

The HTTP client libraries `httpx` and `httpcore` are reduced to `WARNING` level to avoid noisy request logs.

## Runtime Components

### TriggerScheduler

`TriggerScheduler` is defined in `server/services/trigger_scheduler.py`.

It polls stored triggers every 10 seconds by default. On every poll, it asks `TriggerService` for due active triggers and starts a background task for each due trigger that is not already in flight.

The scheduler is independent of inbound messaging. It can dispatch execution agents without a user message currently being processed.

### MessagingGateway

`MessagingGateway` is defined in `server/messaging/gateway.py`.

It owns the configured messaging adapters. In the current codebase, that means a `SignalAdapter` when `OPENPOKE_SIGNAL_ACCOUNT` is configured.

On startup, the gateway:

- Health-checks the Signal HTTP daemon.
- Subscribes its `deliver_reply()` callback to the global reply publisher.
- Starts `signal_adapter.listen()` as an async task named `signal-listener`.

If Signal is configured but the daemon is unavailable, OpenPoke logs a warning and continues without Signal.

## Singleton Factories

Several runtime services are module-level singletons:

- `get_messaging_gateway()` in `server/messaging/gateway.py`.
- `get_trigger_scheduler()` in `server/services/trigger_scheduler.py`.
- `get_conversation_processor()` in `server/services/conversation/processor.py`.
- `get_conversation_log()` in `server/services/conversation/log.py`.
- `get_agent_roster()` in `server/services/execution/roster.py`.
- `get_agent_search_index()` in `server/services/execution/agent_search.py`.
- `get_execution_agent_logs()` in `server/services/execution/log_store.py`.
- `get_trigger_service()` in `server/services/triggers/__init__.py`.
- `get_timezone_store()` in `server/services/timezone_store.py`.

The service constructors typically create local directories, initialize SQLite schemas, or prepare file-backed state at import/construction time.

## Runtime Threading And Async Model

The top-level process is asyncio-based. Local file and SQLite services use thread locks for consistency, while agent runtimes and network calls are async.

Important concurrency patterns:

- `SignalAdapter.listen()` runs continuously in an asyncio task.
- Inbound Signal events create async processing tasks through the adapter callback path.
- `ExecutionBatchManager` uses an `asyncio.Lock` to batch concurrent execution-agent results.
- File-backed logs use `threading.Lock` or `threading.RLock` around reads and writes.
- Synchronous tool functions are run in worker threads by `ExecutionAgentRuntime._call_tool()`.
