"""Database layer for user-hotdeal-bot API."""

from .models import ApiKey, ApiKeyRateLimit, Article, Base, GuestRateLimit, Settings
from .repository import (
    ApiKeyRateLimitRepository,
    ApiKeyRepository,
    ArticleRepository,
    GuestRateLimitRepository,
    SettingsRepository,
)
from .session import close_db, get_async_engine, get_async_session, get_database_url, get_engine, get_timezone, init_db

__all__ = [
    # Models
    "Base",
    "Article",
    "ApiKey",
    "ApiKeyRateLimit",
    "GuestRateLimit",
    "Settings",
    # Session
    "get_database_url",
    "get_timezone",
    "get_async_engine",
    "get_async_session",
    "get_engine",
    "init_db",
    "close_db",
    # Repository
    "ArticleRepository",
    "ApiKeyRepository",
    "ApiKeyRateLimitRepository",
    "GuestRateLimitRepository",
    "SettingsRepository",
]
