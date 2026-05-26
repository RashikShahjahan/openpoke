# Messaging

The messaging layer adapts external channels into OpenPoke's internal conversation processor and routes user-visible replies back to the channel that produced the current turn.

The current implementation supports Signal through signal-cli's direct HTTP daemon.

## Data Types

Messaging DTOs live in `server/messaging/types.py`.

`InboundMessage` is the normalized inbound message shape:

- `source`: messaging source name, currently `signal`.
- `sender`: source-specific sender identifier, currently a phone number.
- `text`: stripped message text.
- `raw`: original adapter payload for debugging or future use.

`ReplyTarget` identifies where a reply should be delivered:

- `source`: messaging source name.
- `destination`: source-specific destination identifier.

These types intentionally keep the rest of the application independent of Signal's event format.

## Signal Adapter

`SignalAdapter` is defined in `server/messaging/signal.py`.

It talks to signal-cli's direct HTTP daemon through these endpoints:

- `GET /api/v1/check` for startup health checks.
- `GET /api/v1/events?account=...` for the server-sent events stream.
- `POST /api/v1/rpc` with JSON-RPC method `send` for outbound messages.

### Inbound Filtering

`parse_event()` receives decoded Signal event payloads and returns an `InboundMessage` only when the event should enter OpenPoke.

It filters out:

- Events without a sender.
- Senders not present in `OPENPOKE_SIGNAL_ALLOWED_SENDERS`.
- Messages sent by the configured Signal account, except supported Note to Self sync messages.
- Events without a `dataMessage`.
- Empty message text.

Note to Self support is implemented by `_parse_note_to_self()`. It accepts self-sent sync messages where the source and destination are both the configured account. It also records recently sent timestamps so OpenPoke does not process its own outbound replies as new inbound messages.

### Event Streaming

`listen()` opens the Signal event stream and loops while the adapter is running. For each SSE line beginning with `data:`, it removes the prefix and calls `dispatch_event()`.

`dispatch_event()` parses JSON, calls `parse_event()`, and invokes `on_message` when a valid inbound message is produced. If `on_message` returns an awaitable, it is scheduled as an async task.

## Messaging Gateway

`MessagingGateway` is defined in `server/messaging/gateway.py`.

It connects adapters to the shared `ConversationProcessor`.

On construction, if a `SignalAdapter` is present, the gateway sets `signal_adapter.on_message = self.handle_inbound`.

`handle_inbound()` performs the channel-to-conversation bridge:

1. Builds `ReplyTarget(source=message.source, destination=message.sender)`.
2. Stores that target in the messaging context.
3. Calls `processor.process_user_message(message.text)`.
4. Clears the reply target in a `finally` block.

This is the point where source-specific message details stop mattering to the conversation and agent layers.

## Reply Context

Reply routing is implemented in `server/messaging/context.py`.

The active reply target is stored in a `ContextVar` named `_reply_target`. This allows code deeper in the async call chain to publish a reply without receiving an explicit `ReplyTarget` parameter.

The public functions are:

- `set_reply_target(target)` sets or clears the current target.
- `get_reply_target()` reads the current target.
- `publish_reply(content)` sends content to all subscribers if a target is active.
- `subscribe(callback)` registers a reply subscriber.
- `unsubscribe(callback)` removes a subscriber.

`publish_reply()` is a no-op when no reply target is active. This matters for background trigger executions, which may produce conversation replies without a currently active messaging turn.

## Outbound Delivery

The gateway subscribes `deliver_reply()` when it starts successfully with Signal.

`deliver_reply(content, target)` checks the target source. If the target is Signal, it schedules `signal_adapter.send(target, content)` as a background task.

`SignalAdapter.send()` sends JSON-RPC to `/api/v1/rpc`:

```json
{
  "jsonrpc": "2.0",
  "method": "send",
  "params": {
    "account": "<configured account>",
    "message": "<reply content>",
    "recipient": ["<target destination>"]
  },
  "id": 1
}
```

After a successful send, the adapter records the returned timestamp so Note to Self reply echoes can be ignored.

## End-To-End Signal Flow

```text
signal-cli event stream
  -> SignalAdapter.listen
  -> SignalAdapter.dispatch_event
  -> SignalAdapter.parse_event
  -> MessagingGateway.handle_inbound
  -> set_reply_target(ReplyTarget("signal", sender))
  -> ConversationProcessor.process_user_message
  -> agent/conversation layers
  -> ConversationLog.record_reply
  -> publish_reply
  -> MessagingGateway.deliver_reply
  -> SignalAdapter.send
```

## Adding Another Messaging Channel

To add another messaging adapter, follow the same boundary used by Signal:

- Normalize incoming events into `InboundMessage`.
- Call `MessagingGateway.handle_inbound()` or a gateway-equivalent handler.
- Use a distinct `source` value.
- Subscribe an outbound delivery callback that handles `ReplyTarget` values for that source.

No conversation or agent code should need to know about the new channel's raw event format.
