import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import event, select

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repo_type, model, key",
    [
        (GuestRateLimitRepository, GuestRateLimit, "127.0.0.1"),
        (ApiKeyRateLimitRepository, ApiKeyRateLimit, 1),
    ],
)
async def test_new_rate_limit_key_starts_at_one(repo_type, model, key):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    try:
        async with get_async_session(engine) as session:
            assert await repo_type(session).check_and_increment(key, 2) is True
            row = (await session.execute(select(model))).scalar_one()
            assert row.request_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_type, key", [(GuestRateLimitRepository, "127.0.0.1"), (ApiKeyRateLimitRepository, 1)])
async def test_rate_limit_hot_path_only_updates(repo_type, key):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    try:
        async with get_async_session(engine) as session:
            repo = repo_type(session)
            assert await repo.check_and_increment(key, 2) is True
            event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
            assert await repo.check_and_increment(key, 2) is True
            event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
            assert statements == ["UPDATE"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, expected", [(1, [False, True]), (2, [True, True])])
async def test_concurrent_first_requests_respect_limit(tmp_path, limit, expected):
    engine = get_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rate_limits.db'}")
    await init_db(engine)

    async def request():
        async with get_async_session(engine) as session:
            return await GuestRateLimitRepository(session).check_and_increment("new", limit)

    try:
        assert sorted(await asyncio.gather(request(), request())) == expected
        async with get_async_session(engine) as session:
            row = (await session.execute(select(GuestRateLimit))).scalar_one()
            assert row.request_count == limit
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_type, key", [(GuestRateLimitRepository, "127.0.0.1"), (ApiKeyRateLimitRepository, 1)])
async def test_mysql_rate_limit_ensures_row_before_increment(monkeypatch, repo_type, key):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    calls = []
    try:
        async with get_async_session(engine) as session:
            repo = repo_type(session)
            ensure_row = repo._ensure_row
            increment = repo._increment_active_window
            monkeypatch.setattr("src.db.repository._is_mysql_session", lambda session: True)

            async def record_ensure(key, now):
                calls.append("ensure")
                # Execute the upsert using SQLite while testing MySQL ordering.
                with monkeypatch.context() as patch:
                    patch.setattr("src.db.repository._is_mysql_session", lambda session: False)
                    await ensure_row(key, now)

            async def record_increment(key, cutoff, limit):
                calls.append("increment")
                return await increment(key, cutoff, limit)

            monkeypatch.setattr(repo, "_ensure_row", record_ensure)
            monkeypatch.setattr(repo, "_increment_active_window", record_increment)
            assert await repo.check_and_increment(key, 1) is True
            assert calls == ["ensure", "increment"]
            calls.clear()
            assert await repo.check_and_increment(key, 1) is False
            assert calls == ["ensure", "increment", "increment"]
    finally:
        await engine.dispose()
