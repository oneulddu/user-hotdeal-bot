import asyncio

import aiohttp
import pytest
from aiohttp import web

from src.crawler import ArcaLiveCrawlerV2, ArcaLiveCrawlerV15, arcalive
from tests.test_crawler_config import FakeCurlSession, FakeScraplingResponse, FakeScraplingSession


@pytest.mark.asyncio
async def test_scrapling_parallel_requests_start_one_session_and_close_once():
    class SlowSession(FakeScraplingSession):
        start_count = 0
        close_count = 0

        async def start(self):
            self.start_count += 1
            await asyncio.sleep(0)

        async def close(self):
            self.close_count += 1

    session = SlowSession()
    instance = ArcaLiveCrawlerV2("test", ["https://example.com"], scrapling_session=session)
    try:
        responses = await asyncio.gather(*(instance.request(f"https://example.com/{i}") for i in range(20)))
        assert responses == [FakeScraplingResponse.html_content] * 20
        assert session.start_count == 1
    finally:
        await instance.close()
        await instance.close()
    assert session.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_start", [False, True])
async def test_scrapling_failed_or_cancelled_start_cleans_up_and_can_retry(monkeypatch, cancel_start):
    started = asyncio.Event()
    release = asyncio.Event()

    class BrokenSession(FakeScraplingSession):
        async def start(self):
            started.set()
            await release.wait()
            raise RuntimeError("browser start failed")

    failed = BrokenSession()
    replacement = FakeScraplingSession()
    monkeypatch.setattr(arcalive, "AsyncStealthySession", lambda **kwargs: replacement)
    instance = ArcaLiveCrawlerV2("test", ["https://example.com"], scrapling_session=failed)
    first = asyncio.create_task(instance.request("https://example.com"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        if cancel_start:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            release.set()
            assert await first is None
        assert failed.closed
        assert await instance.request("https://example.com") == FakeScraplingResponse.html_content
        assert replacement.started
    finally:
        await instance.close()
    assert replacement.closed


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
            if self.calls == 1:
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
    assert len(created) == 1
    assert created[0].calls == 4
    assert created[0].closes == 1


@pytest.mark.asyncio
async def test_real_curl_reuses_connection_without_carrying_response_cookies():
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
        for _ in range(3):
            assert await instance.request(url) == "ok"
        assert len(set(transports)) == 1
        assert cookies == [{"configured": "value"}] * 3
    finally:
        await instance.close()
        await runner.cleanup()
