import gc
import weakref

import pytest
from sqlalchemy import text

from src.db import get_async_engine
from src.db.session import _session_maker_cache, close_db, get_async_session, get_async_session_maker


@pytest.mark.asyncio
async def test_get_async_session_maker_reuses_maker_for_same_engine():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")

    first = get_async_session_maker(engine)
    second = get_async_session_maker(engine)

    await engine.dispose()

    assert first is second


@pytest.mark.asyncio
async def test_close_db_removes_global_engine_session_maker(monkeypatch):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr("src.db.session._engine", engine)
    maker = get_async_session_maker(engine)

    await close_db()

    assert engine not in _session_maker_cache


@pytest.mark.asyncio
async def test_session_factory_cache_does_not_retain_disposed_engine():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with get_async_session(engine) as session:
        await session.execute(text("SELECT 1"))
    ref = weakref.ref(engine)
    await engine.dispose()
    del session, engine
    gc.collect()

    assert ref() is None


@pytest.mark.asyncio
async def test_default_session_reuses_global_engine_and_commits_or_rolls_back(monkeypatch):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr("src.db.session._engine", engine)
    try:
        async with get_async_session() as session:
            assert session.bind is engine
            await session.execute(text("CREATE TABLE test_values (value INTEGER)"))
            await session.execute(text("INSERT INTO test_values VALUES (1)"))
        with pytest.raises(RuntimeError, match="rollback"):
            async with get_async_session() as session:
                await session.execute(text("INSERT INTO test_values VALUES (2)"))
                raise RuntimeError("rollback")
        async with get_async_session() as session:
            assert session.bind is engine
            assert list((await session.execute(text("SELECT value FROM test_values"))).scalars()) == [1]
    finally:
        await close_db()
