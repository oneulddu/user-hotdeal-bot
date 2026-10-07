import asyncio

import aiohttp
import pytest
from aiohttp import web

from src.crawler import ArcaLiveCrawlerV15, arcalive
from tests.test_crawler_config import FakeCurlSession


@pytest.mark.asyncio
async def test_curl_session_reused_for_parallel_requests_after_failure(monkeypatch):
    created = []

    class Session(FakeCurlSession):
        calls = 0
        closes = 0

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

        async def get(self, url, **kwargs):
            self.calls += 1
            if self is created[0] and self.calls == 1:
                raise RuntimeError("request failed")
            await asyncio.sleep(0)
            return await super().get(url, **kwargs)

        async def close(self):
            self.closes += 1

    monkeypatch.setattr(arcalive, "CurlAsyncSession", Session)
    async with aiohttp.ClientSession() as shared:
        instance = ArcaLiveCrawlerV15("test", ["https://example.com"], session=shared)
        assert await instance.request("https://example.com") is None
        results = await asyncio.gather(*(instance.request("https://example.com") for _ in range(3)))
        assert all(results)
        await instance.close()
        await instance.close()
        assert not shared.closed
    assert len(created) == 2
    assert [session.calls for session in created] == [1, 3]
    assert [session.closes for session in created] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_requests", [0, 12])
async def test_real_curl_reuses_connection_without_carrying_response_cookies(monkeypatch, invalid_requests):
    transports = []
    cookies = []

    async def handler(request):
        transports.append(request.transport)
        cookies.append(dict(request.cookies))
        response = web.Response(text="ok")
        response.set_cookie("response_cookie", "do-not-reuse")
        return response

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    instance = ArcaLiveCrawlerV15("test", [url], cookie="configured=value")
    try:
        if invalid_requests:
            monkeypatch.setenv("ARCALIVE_CURL_IMPERSONATE", "unsupported-browser")
            async with asyncio.timeout(2):
                for _ in range(invalid_requests):
                    assert await instance.request(url) is None
            assert transports == []
            monkeypatch.setenv("ARCALIVE_CURL_IMPERSONATE", "chrome124")
        for _ in range(3):
            assert await instance.request(url) == "ok"
        assert len(set(transports)) == 1
        assert cookies == [{"configured": "value"}] * 3
    finally:
        await instance.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_retiring_curl_session_does_not_close_other_active_requests(monkeypatch):
    created = []
    second_started = asyncio.Event()
    release = asyncio.Event()

    class Session(FakeCurlSession):
        closed = False

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

        async def get(self, url, **kwargs):
            if url.endswith("fail"):
                await second_started.wait()
                raise ValueError("bad option")
            if url.endswith("waiting"):
                second_started.set()
                await release.wait()
                assert not self.closed
            return await super().get(url, **kwargs)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(arcalive, "CurlAsyncSession", Session)
    instance = ArcaLiveCrawlerV15("test", ["https://example.com"])
    first = asyncio.create_task(instance.request("https://example.com/fail"))
    second = asyncio.create_task(instance.request("https://example.com/waiting"))
    try:
        assert await asyncio.wait_for(first, timeout=2) is None
        assert not created[0].closed
        assert await instance.request("https://example.com/new")
        assert len(created) == 2
        release.set()
        assert await asyncio.wait_for(second, timeout=2)
        assert created[0].closed
        assert not created[1].closed
        assert instance._curl_session_users == {}
    finally:
        await instance.close()
    assert created[1].closed
