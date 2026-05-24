from __future__ import annotations

from ...config import get_settings
from .models import Availability, CalendarEvent
from .service import LocalIcsCalendarService


def get_calendar_service() -> LocalIcsCalendarService:
    settings = get_settings()
    return LocalIcsCalendarService(
        settings.calendar_ics_path,
        refresh_seconds=settings.calendar_refresh_seconds,
    )


__all__ = [
    "Availability",
    "CalendarEvent",
    "LocalIcsCalendarService",
    "get_calendar_service",
]
