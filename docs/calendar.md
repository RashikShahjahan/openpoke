# Local Calendar Access

OpenPoke can read a local `.ics` calendar file. This integration is read-only: it can list events and check availability, but it cannot create, update, delete, accept, or decline calendar events.

## Requirements

- A local `.ics` file exported or synced from your calendar provider
- OpenPoke running with access to that file path

## Configure OpenPoke

Add the calendar path to `.env`:

```bash
OPENPOKE_CALENDAR_ICS_PATH=/absolute/path/to/calendar.ics
OPENPOKE_CALENDAR_REFRESH_SECONDS=60
```

`OPENPOKE_CALENDAR_REFRESH_SECONDS` controls how long parsed events can be reused before OpenPoke checks the file again. The file modification time is also checked, so updated files are reloaded.

## Behavior

- OpenPoke reads only the configured local `.ics` file.
- Events are filtered by requested time range.
- Naive query timestamps are interpreted in the user's stored timezone, falling back to UTC.
- Availability ignores cancelled and transparent events.
- Calendar tool results are redacted from execution-agent logs to avoid persisting event details unnecessarily.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `OPENPOKE_CALENDAR_ICS_PATH` | unset | Absolute or `~`-expanded path to a local `.ics` file |
| `OPENPOKE_CALENDAR_REFRESH_SECONDS` | `60` | Minimum seconds before rechecking a previously parsed file |
