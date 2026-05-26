"""Service layer components."""

from .conversation import (
    ConversationLog,
    ConversationProcessor,
    SummaryState,
    get_conversation_log,
    get_conversation_processor,
    get_working_memory_log,
    schedule_summarization,
)
from .calendar import CalendarEvent, LocalIcsCalendarService, get_calendar_service
from .email import EmailFolder, EmailMessage, ThunderbirdEmailService, get_email_service
from .execution import AgentRoster, ExecutionAgentLogStore, get_agent_roster, get_execution_agent_logs
from .triggers import get_trigger_service
from .timezone_store import TimezoneStore, get_timezone_store


def get_trigger_scheduler():
    """Return the trigger scheduler without importing execution agents at package load."""

    from .trigger_scheduler import get_trigger_scheduler as _get_trigger_scheduler

    return _get_trigger_scheduler()


__all__ = [
    "ConversationLog",
    "ConversationProcessor",
    "SummaryState",
    "get_conversation_log",
    "get_conversation_processor",
    "get_working_memory_log",
    "schedule_summarization",
    "CalendarEvent",
    "LocalIcsCalendarService",
    "get_calendar_service",
    "EmailFolder",
    "EmailMessage",
    "ThunderbirdEmailService",
    "get_email_service",
    "AgentRoster",
    "ExecutionAgentLogStore",
    "get_agent_roster",
    "get_execution_agent_logs",
    "get_trigger_scheduler",
    "get_trigger_service",
    "TimezoneStore",
    "get_timezone_store",
]
