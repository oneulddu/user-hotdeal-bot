"""FastAPI dependencies for database session, authentication, etc."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import (
    ApiKeyRateLimitRepository,
    ApiKeyRepository,
    ArticleRepository,
    GuestRateLimitRepository,
    Settings,
    SettingsRepository,
    get_async_session,
    get_engine,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    async with get_async_session(get_engine()) as session:
        yield session


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_article_repository(session: DbSession) -> ArticleRepository:
    """Dependency to get ArticleRepository."""
    return ArticleRepository(session)


async def get_api_key_repository(session: DbSession) -> ApiKeyRepository:
    """Dependency to get ApiKeyRepository."""
    return ApiKeyRepository(session)


async def get_settings_repository(session: DbSession) -> SettingsRepository:
    """Dependency to get SettingsRepository."""
    return SettingsRepository(session)


async def get_guest_rate_limit_repository(session: DbSession) -> GuestRateLimitRepository:
    """Dependency to get GuestRateLimitRepository."""
    return GuestRateLimitRepository(session)


# Type aliases for repository injection
ArticleRepo = Annotated[ArticleRepository, Depends(get_article_repository)]
ApiKeyRepo = Annotated[ApiKeyRepository, Depends(get_api_key_repository)]
SettingsRepo = Annotated[SettingsRepository, Depends(get_settings_repository)]
GuestRateLimitRepo = Annotated[GuestRateLimitRepository, Depends(get_guest_rate_limit_repository)]


async def verify_api_key_or_guest(
    request: Request,
    session: DbSession,
    x_api_key: Annotated[str | None, Header()] = None,
    api_key: Annotated[str | None, Query()] = None,
) -> str | None:
    """Verify API key or allow guest access with rate limiting.

    Returns:
        API key string if authenticated, None if guest
    """
    api_key_repo = ApiKeyRepository(session)
    settings_repo = SettingsRepository(session)
    guest_rate_limit_repo = GuestRateLimitRepository(session)

    api_key_value = x_api_key or api_key

    # Check API key if provided
    if api_key_value:
        db_api_key = await api_key_repo.get_by_key(api_key_value)
        if db_api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

        # Rate limit for API key
        api_key_rate_limit_repo = ApiKeyRateLimitRepository(session)
        within_limit = await api_key_rate_limit_repo.check_and_increment(
            db_api_key.id, db_api_key.rate_limit_per_minute
        )
        if not within_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {db_api_key.rate_limit_per_minute} requests per minute.",
            )

        await api_key_repo.update_last_used(api_key_value)
        return api_key_value

    # Guest access
    guest_enabled = await settings_repo.get_bool(Settings.GUEST_ACCESS_ENABLED, default=True)
    if not guest_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Guest access is disabled.",
        )

    # Rate limit for guest
    client_ip = request.client.host if request.client else "unknown"
    guest_rate_limit = await settings_repo.get_int(Settings.GUEST_RATE_LIMIT_PER_MINUTE, default=30)

    within_limit = await guest_rate_limit_repo.check_and_increment(client_ip, guest_rate_limit)
    if not within_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {guest_rate_limit} requests per minute for guests.",
        )

    return None


# Type alias for auth dependency
AuthResult = Annotated[str | None, Depends(verify_api_key_or_guest)]
