from datetime import timedelta

import pytest
from sqlalchemy import select

from src.api.main import cleanup_rate_limit_records
from src.datetime_utils import utc_now
from src.db import (
    ApiKeyRateLimit,
    ApiKeyRateLimitRepository,
    ApiKeyRepository,
    GuestRateLimit,
    GuestRateLimitRepository,
    get_async_engine,
    get_async_session,
    init_db,
)


@pytest.mark.asyncio
async def test_guest_rate_limit_does_not_increment_past_limit():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        repo = GuestRateLimitRepository(session)

        assert await repo.check_and_increment("127.0.0.1", 2) is True
        assert await repo.check_and_increment("127.0.0.1", 2) is True
        assert await repo.check_and_increment("127.0.0.1", 2) is False

        result = await session.execute(select(GuestRateLimit).where(GuestRateLimit.ip_address == "127.0.0.1"))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 2


@pytest.mark.asyncio
async def test_guest_rate_limit_resets_expired_window():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        session.add(
            GuestRateLimit(
                ip_address="127.0.0.1",
                request_count=99,
                window_start=utc_now() - timedelta(minutes=2),
            )
        )
        await session.flush()

        assert await GuestRateLimitRepository(session).check_and_increment("127.0.0.1", 2) is True

        result = await session.execute(select(GuestRateLimit).where(GuestRateLimit.ip_address == "127.0.0.1"))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 1


@pytest.mark.asyncio
async def test_guest_rate_limit_cleanup_deletes_only_old_records():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        session.add_all(
            [
                GuestRateLimit(
                    ip_address="old",
                    request_count=1,
                    window_start=utc_now() - timedelta(minutes=120),
                ),
                GuestRateLimit(
                    ip_address="recent",
                    request_count=1,
                    window_start=utc_now(),
                ),
            ]
        )
        await session.flush()

        deleted_count = await GuestRateLimitRepository(session).cleanup_old_records(older_than_minutes=60)
        result = await session.execute(select(GuestRateLimit.ip_address))

    await engine.dispose()

    assert deleted_count == 1
    assert set(result.scalars()) == {"recent"}


@pytest.mark.asyncio
async def test_api_key_rate_limit_does_not_increment_past_limit():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        repo = ApiKeyRateLimitRepository(session)

        assert await repo.check_and_increment(1, 2) is True
        assert await repo.check_and_increment(1, 2) is True
        assert await repo.check_and_increment(1, 2) is False

        result = await session.execute(select(ApiKeyRateLimit).where(ApiKeyRateLimit.api_key_id == 1))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 2


@pytest.mark.asyncio
async def test_api_key_rate_limit_resets_expired_window():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        session.add(
            ApiKeyRateLimit(
                api_key_id=1,
                request_count=99,
                window_start=utc_now() - timedelta(minutes=2),
            )
        )
        await session.flush()

        assert await ApiKeyRateLimitRepository(session).check_and_increment(1, 2) is True

        result = await session.execute(select(ApiKeyRateLimit).where(ApiKeyRateLimit.api_key_id == 1))
        rate_limit = result.scalar_one()

    await engine.dispose()

    assert rate_limit.request_count == 1


@pytest.mark.asyncio
async def test_api_key_rate_limit_cleanup_deletes_only_old_records():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        session.add_all(
            [
                ApiKeyRateLimit(
                    api_key_id=1,
                    request_count=1,
                    window_start=utc_now() - timedelta(minutes=120),
                ),
                ApiKeyRateLimit(
                    api_key_id=2,
                    request_count=1,
                    window_start=utc_now(),
                ),
            ]
        )
        await session.flush()

        deleted_count = await ApiKeyRateLimitRepository(session).cleanup_old_records(older_than_minutes=60)
        result = await session.execute(select(ApiKeyRateLimit.api_key_id))

    await engine.dispose()

    assert deleted_count == 1
    assert set(result.scalars()) == {2}


@pytest.mark.asyncio
async def test_api_cleanup_rate_limit_records_deletes_guest_and_api_rows(monkeypatch):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    monkeypatch.setattr("src.api.main.get_engine", lambda: engine)

    async with get_async_session(engine) as session:
        api_key = await ApiKeyRepository(session).create("valid-key", "test-client")
        session.add_all(
            [
                GuestRateLimit(
                    ip_address="old",
                    request_count=1,
                    window_start=utc_now() - timedelta(minutes=120),
                ),
                ApiKeyRateLimit(
                    api_key_id=api_key.id,
                    request_count=1,
                    window_start=utc_now() - timedelta(minutes=120),
                ),
            ]
        )

    deleted_count = await cleanup_rate_limit_records()

    async with get_async_session(engine) as session:
        guest_result = await session.execute(select(GuestRateLimit.ip_address))
        api_result = await session.execute(select(ApiKeyRateLimit.api_key_id))

    await engine.dispose()

    assert deleted_count == 2
    assert list(guest_result.scalars()) == []
    assert list(api_result.scalars()) == []
