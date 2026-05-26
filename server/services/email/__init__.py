from __future__ import annotations

from ...config import get_settings
from .models import EmailFolder, EmailMessage
from .service import EmailTextCleaner, ThunderbirdEmailService


def get_email_service() -> ThunderbirdEmailService:
    settings = get_settings()
    return ThunderbirdEmailService(
        settings.email_thunderbird_profile_path,
        refresh_seconds=settings.email_refresh_seconds,
    )


__all__ = [
    "EmailFolder",
    "EmailMessage",
    "EmailTextCleaner",
    "ThunderbirdEmailService",
    "get_email_service",
]
