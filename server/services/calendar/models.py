from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CalendarEvent:
    """Normalized read-only calendar event."""

    id: str
    summary: str
    start: str
    end: str
    all_day: bool = False
    location: str | None = None
    status: str | None = None
    transparent: bool = False

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "summary": self.summary,
            "start": self.start,
            "end": self.end,
            "all_day": self.all_day,
        }
        if self.location:
            payload["location"] = self.location
        if self.status:
            payload["status"] = self.status
        return payload


@dataclass(frozen=True)
class Availability:
    """Availability result for a queried calendar range."""

    available: bool
    start: str
    end: str
    busy: list[CalendarEvent]

    def to_payload(self) -> dict[str, object]:
        return {
            "available": self.available,
            "start": self.start,
            "end": self.end,
            "busy": [event.to_payload() for event in self.busy],
        }


__all__ = ["Availability", "CalendarEvent"]
