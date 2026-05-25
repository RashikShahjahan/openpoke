# Local Email Access

OpenPoke can read locally stored Thunderbird email folders. This integration is read-only: it can list folders, search messages, and read message content, but it cannot send, draft, reply, forward, delete, move, archive, mark, or otherwise modify email.

## Requirements

- Thunderbird storing mail locally in mbox folders
- OpenPoke running with filesystem access to the Thunderbird profile directory

## Configure OpenPoke

Add the Thunderbird profile path to `.env`:

```bash
OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH=/absolute/path/to/thunderbird/profile
OPENPOKE_EMAIL_REFRESH_SECONDS=60
```

On macOS, Thunderbird profiles are commonly under:

```text
~/Library/Thunderbird/Profiles/<profile-name>
```

If `OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH` is unset, OpenPoke attempts to auto-detect a profile from standard Thunderbird locations.

## Behavior

- OpenPoke reads Thunderbird mbox files under `Mail/` and `ImapMail/`.
- Thunderbird `.msf` index files and metadata files are ignored.
- Searches can filter by text, folder, sender, recipient, subject, date range, and attachment presence.
- Email tool results are redacted from execution-agent logs to avoid persisting message details unnecessarily.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `OPENPOKE_EMAIL_THUNDERBIRD_PROFILE_PATH` | auto-detect | Absolute or `~`-expanded path to a Thunderbird profile directory |
| `OPENPOKE_EMAIL_REFRESH_SECONDS` | `60` | Minimum seconds before rechecking previously discovered folders |
