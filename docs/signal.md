# Signal Messaging

OpenPoke can receive and send Signal DMs through the signal-cli direct HTTP daemon.

Signal support is disabled by default. V1 supports text-only direct messages: no groups, attachments, typing indicators, reactions, or formatting.

## Requirements

- `signal-cli` installed and linked to a Signal account
- OpenPoke server running normally
- Signal sender allowlist configured explicitly

## Start signal-cli

Run signal-cli with its direct HTTP daemon enabled:

```bash
signal-cli --http 127.0.0.1:8080 daemon
```

Check that the daemon is reachable:

```bash
curl http://127.0.0.1:8080/api/v1/check
```

## Configure OpenPoke

Add these values to `.env`:

```bash
OPENPOKE_SIGNAL_ENABLED=1
OPENPOKE_SIGNAL_HTTP_URL=http://127.0.0.1:8080
OPENPOKE_SIGNAL_ACCOUNT=+15551234567
OPENPOKE_SIGNAL_ALLOWED_SENDERS=+15557654321
```

`OPENPOKE_SIGNAL_ALLOWED_SENDERS` is comma-separated:

```bash
OPENPOKE_SIGNAL_ALLOWED_SENDERS=+15557654321,+15559876543
```

An empty allowlist denies all inbound Signal messages.

## Behavior

- OpenPoke health-checks signal-cli on startup.
- If Signal is enabled but the daemon is unavailable, OpenPoke logs a warning and continues without Signal.
- Allowed inbound Signal DMs are routed through the same conversation processor as web chat.
- Replies produced by `record_reply(...)`, including delayed execution-agent results, are delivered back to the originating Signal sender.
- Existing web chat does not trigger Signal sends unless the active turn originated from Signal.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `OPENPOKE_SIGNAL_ENABLED` | `0` | Enable Signal integration when set to `1`, `true`, `yes`, or `on` |
| `OPENPOKE_SIGNAL_HTTP_URL` | `http://127.0.0.1:8080` | Base URL for the signal-cli direct HTTP daemon |
| `OPENPOKE_SIGNAL_ACCOUNT` | unset | Signal account phone number used by signal-cli |
| `OPENPOKE_SIGNAL_ALLOWED_SENDERS` | empty | Comma-separated allowlist of sender phone numbers |
