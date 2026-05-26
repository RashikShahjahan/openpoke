from __future__ import annotations

from pathlib import Path

import pytest

from server.services.calendar import LocalIcsCalendarService


def _write_ics(path: Path, body: str) -> None:
    path.write_text(
        "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//OpenPoke Tests//EN",
                body.strip(),
                "END:VCALENDAR",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_list_events_filters_overlapping_timed_events(tmp_path: Path) -> None:
    ics_path = tmp_path / "calendar.ics"
    _write_ics(
        ics_path,
        """
BEGIN:VEVENT
UID:standup-1
SUMMARY:Team Standup
DTSTART:20260523T150000Z
DTEND:20260523T153000Z
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:later-1
SUMMARY:Later Event
DTSTART:20260524T150000Z
DTEND:20260524T153000Z
END:VEVENT
""",
    )
    service = LocalIcsCalendarService(str(ics_path))

    events = service.list_events(
        start_time="2026-05-23T00:00:00Z",
        end_time="2026-05-24T00:00:00Z",
    )

    assert [event.summary for event in events] == ["Team Standup"]
    assert events[0].location == "Zoom"
    assert events[0].start == "2026-05-23T15:00:00Z"
    assert events[0].end == "2026-05-23T15:30:00Z"


def test_list_events_handles_all_day_events(tmp_path: Path) -> None:
    ics_path = tmp_path / "calendar.ics"
    _write_ics(
        ics_path,
        """
BEGIN:VEVENT
UID:ooo-1
SUMMARY:Out of Office
DTSTART;VALUE=DATE:20260523
DTEND;VALUE=DATE:20260524
END:VEVENT
""",
    )
    service = LocalIcsCalendarService(str(ics_path))

    events = service.list_events(
        start_time="2026-05-23T12:00:00Z",
        end_time="2026-05-23T13:00:00Z",
    )

    assert len(events) == 1
    assert events[0].summary == "Out of Office"
    assert events[0].all_day is True


def test_get_availability_ignores_transparent_and_cancelled_events(tmp_path: Path) -> None:
    ics_path = tmp_path / "calendar.ics"
    _write_ics(
        ics_path,
        """
BEGIN:VEVENT
UID:transparent-1
SUMMARY:FYI Hold
DTSTART:20260523T150000Z
DTEND:20260523T153000Z
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:cancelled-1
SUMMARY:Cancelled Meeting
DTSTART:20260523T160000Z
DTEND:20260523T163000Z
STATUS:CANCELLED
END:VEVENT
""",
    )
    service = LocalIcsCalendarService(str(ics_path))

    availability = service.get_availability(
        start_time="2026-05-23T14:00:00Z",
        end_time="2026-05-23T17:00:00Z",
    )

    assert availability.available is True
    assert availability.busy == []


def test_get_availability_reports_busy_events(tmp_path: Path) -> None:
    ics_path = tmp_path / "calendar.ics"
    _write_ics(
        ics_path,
        """
BEGIN:VEVENT
UID:busy-1
SUMMARY:Client Call
DTSTART:20260523T150000Z
DTEND:20260523T153000Z
END:VEVENT
""",
    )
    service = LocalIcsCalendarService(str(ics_path))

    availability = service.get_availability(
        start_time="2026-05-23T14:00:00Z",
        end_time="2026-05-23T17:00:00Z",
    )

    assert availability.available is False
    assert [event.summary for event in availability.busy] == ["Client Call"]


def test_missing_calendar_file_reports_status_and_errors(tmp_path: Path) -> None:
    service = LocalIcsCalendarService(str(tmp_path / "missing.ics"))

    assert service.connection_status()["status"] == "missing_file"
    with pytest.raises(ValueError, match="Calendar file not found"):
        service.list_events(
            start_time="2026-05-23T00:00:00Z",
            end_time="2026-05-24T00:00:00Z",
        )


def test_recurring_events_are_expanded(tmp_path: Path) -> None:
    ics_path = tmp_path / "calendar.ics"
    _write_ics(
        ics_path,
        """
BEGIN:VEVENT
UID:weekly-1
SUMMARY:Weekly Review
DTSTART:20260501T150000Z
DTEND:20260501T153000Z
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
""",
    )
    service = LocalIcsCalendarService(str(ics_path))

    events = service.list_events(
        start_time="2026-05-15T00:00:00Z",
        end_time="2026-05-16T00:00:00Z",
    )

    assert [event.summary for event in events] == ["Weekly Review"]
    assert events[0].start == "2026-05-15T15:00:00Z"
