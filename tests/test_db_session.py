import pytest

from src.db import get_async_engine
from src.db.session import get_async_session_maker


@pytest.mark.asyncio
async def test_get_async_session_maker_reuses_maker_for_same_engine():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")

    first = get_async_session_maker(engine)
    second = get_async_session_maker(engine)

    await engine.dispose()

    assert first is second
