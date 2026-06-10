"""Datetime helpers shared across the application."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a naive UTC datetime for DB storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Return a datetime with UTC timezone information."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
