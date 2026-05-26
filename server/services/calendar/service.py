from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dateutil import parser as date_parser
from dateutil.rrule import rrulestr
from icalendar import Calendar
from zoneinfo import ZoneInfo

from ...logging_config import logger
from ...utils.timezones import get_user_timezone_name
from .models import Availability, CalendarEvent

UTC = timezone.utc


@dataclass(frozen=True)
class _ParsedEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None
    status: str | None
    transparent: bool

    def to_public_event(self) -> CalendarEvent:
        return CalendarEvent(
            id=self.id,
            summary=self.summary,
            start=_to_iso(self.start),
            end=_to_iso(self.end),
            all_day=self.all_day,
            location=self.location,
            status=self.status,
            transparent=self.transparent,
        )


def _to_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_query_datetime(value: str, tz: ZoneInfo) -> datetime:
    parsed = date_parser.isoparse(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


class LocalIcsCalendarService:
    """Read-only calendar service backed by a local .ics file."""

    def __init__(self, ics_path: Optional[str], refresh_seconds: int = 60) -> None:
        self._ics_path = Path(ics_path).expanduser() if ics_path else None
        self._refresh_seconds = max(refresh_seconds, 0)
        self._cached_mtime: float | None = None
        self._cached_loaded_at: datetime | None = None
        self._cached_events: list[_ParsedEvent] = []

    def connection_status(self) -> dict[str, object]:
        if self._ics_path is None:
            return {"configured": False, "status": "missing_path"}
        if not self._ics_path.is_file():
            return {
                "configured": True,
                "status": "missing_file",
                "path": str(self._ics_path),
            }
        return {
            "configured": True,
            "status": "connected",
            "path": str(self._ics_path),
        }

    def list_events(
        self,
        *,
        start_time: str,
        end_time: str,
        max_results: int = 20,
    ) -> list[CalendarEvent]:
        start, end = self._parse_range(start_time, end_time)
        events = [
            event.to_public_event()
            for event in self._load_events(start, end)
            if _overlaps(event.start, event.end, start, end)
        ]
        events.sort(key=lambda event: event.start)
        return events[: max(1, int(max_results))]

    def get_availability(self, *, start_time: str, end_time: str) -> Availability:
        start, end = self._parse_range(start_time, end_time)
        busy = [
            event.to_public_event()
            for event in self._load_events(start, end)
            if _overlaps(event.start, event.end, start, end)
            and not event.transparent
            and (event.status or "").upper() != "CANCELLED"
        ]
        busy.sort(key=lambda event: event.start)
        return Availability(
            available=not busy,
            start=_to_iso(start),
            end=_to_iso(end),
            busy=busy,
        )

    def _parse_range(self, start_time: str, end_time: str) -> tuple[datetime, datetime]:
        tz = ZoneInfo(get_user_timezone_name())
        start = _parse_query_datetime(start_time, tz)
        end = _parse_query_datetime(end_time, tz)
        if end <= start:
            raise ValueError("end_time must be after start_time")
        return start, end

    def _load_events(self, window_start: datetime, window_end: datetime) -> list[_ParsedEvent]:
        if self._ics_path is None:
            raise ValueError("Calendar is not configured. Set OPENPOKE_CALENDAR_ICS_PATH.")
        if not self._ics_path.is_file():
            raise ValueError(f"Calendar file not found: {self._ics_path}")

        stat = self._ics_path.stat()
        now = datetime.now(UTC)
        cache_fresh = (
            self._cached_loaded_at is not None
            and (now - self._cached_loaded_at).total_seconds() < self._refresh_seconds
        )
        if self._cached_mtime == stat.st_mtime and cache_fresh:
            return self._expand_recurring_events(self._cached_events, window_start, window_end)

        try:
            raw = self._ics_path.read_bytes()
            calendar = Calendar.from_ical(raw)
        except Exception as exc:
            raise ValueError(f"Failed to read calendar file: {exc}") from exc

        events = list(self._parse_calendar(calendar))
        self._cached_mtime = stat.st_mtime
        self._cached_loaded_at = now
        self._cached_events = events
        logger.info("calendar loaded", extra={"events": len(events), "path": str(self._ics_path)})
        return self._expand_recurring_events(events, window_start, window_end)

    def _parse_calendar(self, calendar: Calendar) -> Iterable[_ParsedEvent]:
        tz = ZoneInfo(get_user_timezone_name())
        for component in calendar.walk("VEVENT"):
            start_raw = component.get("DTSTART")
            if start_raw is None:
                continue

            start, all_day = self._coerce_ical_datetime(start_raw.dt, tz)
            end_raw = component.get("DTEND")
            if end_raw is not None:
                end, _ = self._coerce_ical_datetime(end_raw.dt, tz)
            elif all_day:
                end = start + timedelta(days=1)
            else:
                end = start

            if end <= start:
                end = start + (timedelta(days=1) if all_day else timedelta(minutes=30))

            summary = str(component.get("SUMMARY") or "Untitled event")
            uid = str(component.get("UID") or "")
            location = str(component.get("LOCATION") or "") or None
            status = str(component.get("STATUS") or "") or None
            transparent = str(component.get("TRANSP") or "").upper() == "TRANSPARENT"
            event_id = uid or _event_id(summary, start, end)

            recurrence = component.get("RRULE")
            parsed = _ParsedEvent(
                id=event_id,
                summary=summary,
                start=start,
                end=end,
                all_day=all_day,
                location=location,
                status=status,
                transparent=transparent,
            )
            if recurrence:
                yield from self._expand_rrule(parsed, recurrence.to_ical().decode("utf-8"))
            else:
                yield parsed

    def _expand_rrule(self, event: _ParsedEvent, rule_text: str) -> Iterable[_ParsedEvent]:
        duration = event.end - event.start
        dtstart = event.start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        rule = rrulestr(f"DTSTART:{dtstart}\nRRULE:{rule_text}")
        # Store a bounded representative set. Query-time filtering handles overlap.
        for index, occurrence in enumerate(rule[:1000]):
            if occurrence.tzinfo is None:
                occurrence = occurrence.replace(tzinfo=UTC)
            start = occurrence.astimezone(event.start.tzinfo or UTC)
            yield _ParsedEvent(
                id=f"{event.id}:{index}",
                summary=event.summary,
                start=start,
                end=start + duration,
                all_day=event.all_day,
                location=event.location,
                status=event.status,
                transparent=event.transparent,
            )

    def _expand_recurring_events(
        self,
        events: list[_ParsedEvent],
        window_start: datetime,
        window_end: datetime,
    ) -> list[_ParsedEvent]:
        return [event for event in events if _overlaps(event.start, event.end, window_start, window_end)]

    def _coerce_ical_datetime(self, value: Any, tz: ZoneInfo) -> tuple[datetime, bool]:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=tz), False
            return value.astimezone(tz), False
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=tz), True
        raise ValueError(f"Unsupported calendar timestamp: {value!r}")


def _overlaps(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> bool:
    return start < window_end and end > window_start


def _event_id(summary: str, start: datetime, end: datetime) -> str:
    source = f"{summary}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
    return hashlib.sha1(source).hexdigest()[:16]


__all__ = ["LocalIcsCalendarService"]
