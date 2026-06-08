import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from src.api.deps import verify_api_key_or_guest
from src.db import (
    ApiKeyRateLimit,
    ApiKeyRepository,
    GuestRateLimit,
    Settings,
    SettingsRepository,
    get_async_engine,
    get_async_session,
    init_db,
)


def make_request() -> Request:
    return Request({"type": "http", "client": ("127.0.0.1", 12345), "headers": []})


@pytest.mark.asyncio
async def test_query_api_key_auth_works_when_guest_access_is_disabled():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        await ApiKeyRepository(session).create("valid-key", "test-client")
        await SettingsRepository(session).set(Settings.GUEST_ACCESS_ENABLED, "false")

        auth_result = await verify_api_key_or_guest(
            request=make_request(),
            session=session,
            api_key="valid-key",
        )

    await engine.dispose()

    assert auth_result == "valid-key"


@pytest.mark.asyncio
async def test_guest_rate_limit_increment_survives_route_error():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    with pytest.raises(HTTPException):
        async with get_async_session(engine) as session:
            await verify_api_key_or_guest(request=make_request(), session=session)
            raise HTTPException(status_code=404, detail="missing")

    async with get_async_session(engine) as session:
        result = await session.execute(select(GuestRateLimit).where(GuestRateLimit.ip_address == "127.0.0.1"))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 1


@pytest.mark.asyncio
async def test_api_key_rate_limit_increment_survives_route_error():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        api_key = await ApiKeyRepository(session).create("valid-key", "test-client")

    with pytest.raises(HTTPException):
        async with get_async_session(engine) as session:
            await verify_api_key_or_guest(
                request=make_request(),
                session=session,
                api_key="valid-key",
            )
            raise HTTPException(status_code=404, detail="missing")

    async with get_async_session(engine) as session:
        result = await session.execute(select(ApiKeyRateLimit).where(ApiKeyRateLimit.api_key_id == api_key.id))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 1
