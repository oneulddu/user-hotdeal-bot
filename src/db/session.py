"""Database session management for async SQLAlchemy."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# Default database URL (SQLite for development)
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/hotdeal.db"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# Cached configs by path
_config_cache: dict[str, dict[str, Any]] = {}


def _load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    """Load config from YAML file.

    Args:
        config_path: Path to config file

    Returns:
        Config dictionary, empty dict if file not found
    """
    path = Path(config_path)
    cache_key = str(path.resolve()) if path.exists() else str(path)
    if cache_key in _config_cache:
        return _config_cache[cache_key]

    if path.exists():
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    _config_cache[cache_key] = config
    return config


def get_database_url(config_path: str = "config.yaml") -> str:
    """Get database URL from config file, environment variable, or use default.

    Priority: config.yaml > DATABASE_URL env var > default

    Examples:
        - SQLite: sqlite+aiosqlite:///./hotdeal.db
        - MariaDB: mysql+aiomysql://user:pass@localhost/hotdeal
    """
    config = _load_config(config_path)
    db_config = config.get("database", {})

    # Priority: config.yaml > env var > default
    if url := db_config.get("url"):
        return url

    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_database_echo(config_path: str = "config.yaml") -> bool:
    """Get database echo setting from config file or environment variable."""
    config = _load_config(config_path)
    db_config = config.get("database", {})

    # Priority: config.yaml > env var > default (False)
    if "echo" in db_config:
        return _parse_bool(db_config["echo"])

    return _parse_bool(os.getenv("DATABASE_ECHO"), default=False)


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Parse config/env boolean values without treating 'false' as truthy."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return default


def ensure_sqlite_database_parent(database_url: str) -> None:
    """Create the parent directory for file-based SQLite URLs."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return

    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return

    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def get_timezone(config_path: str = "config.yaml") -> str:
    """Get timezone from config file or environment variable.

    Priority: config.yaml > TZ env var > default (UTC)
    """
    config = _load_config(config_path)
    app_config = config.get("app", {})

    # Priority: config.yaml > env var > default
    if tz := app_config.get("timezone"):
        return tz

    return os.getenv("TZ", "UTC")


def get_async_engine(database_url: str | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: Database connection URL. If None, uses get_database_url().

    Returns:
        AsyncEngine instance
    """
    url = database_url or get_database_url()
    ensure_sqlite_database_parent(url)

    # SQLite specific settings
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    # Pool settings for connection stability
    # - pool_pre_ping: Check connection validity before use (handles stale connections)
    # - pool_recycle: Recreate connections after 1 hour (before MariaDB's wait_timeout)
    pool_kwargs = {}
    if not url.startswith("sqlite"):
        pool_kwargs = {
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "pool_size": 5,
            "max_overflow": 10,
        }

    return create_async_engine(
        url,
        echo=get_database_echo(),
        connect_args=connect_args,
        **pool_kwargs,
    )


def get_async_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session maker.

    Args:
        engine: AsyncEngine instance

    Returns:
        async_sessionmaker instance
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


@asynccontextmanager
async def get_async_session(engine: AsyncEngine | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Context manager for async database sessions.

    Args:
        engine: AsyncEngine instance. If None, creates a new engine.

    Yields:
        AsyncSession instance

    Example:
        async with get_async_session() as session:
            result = await session.execute(select(Article))
            articles = result.scalars().all()
    """
    if engine is None:
        engine = get_async_engine()

    session_maker = get_async_session_maker(engine)
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Global engine instance (lazy initialization)
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Get or create the global async engine instance."""
    global _engine
    if _engine is None:
        _engine = get_async_engine()
    return _engine


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Initialize database tables.

    Args:
        engine: AsyncEngine instance. If None, uses global engine.

    Note:
        This should only be used for development/testing.
        Use Alembic migrations for production.
    """
    from .models import Base

    if engine is None:
        engine = get_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close the global database engine."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
