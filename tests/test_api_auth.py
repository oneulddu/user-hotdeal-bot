import pytest
from starlette.requests import Request

from src.api.deps import verify_api_key_or_guest
from src.db import ApiKeyRepository, Settings, SettingsRepository, get_async_engine, get_async_session, init_db


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
