import pytest

from src.db import get_async_engine
from src.db.session import _session_maker_cache, close_db, get_async_session_maker


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
    get_async_session_maker(engine)

    await close_db()

    assert engine not in _session_maker_cache
