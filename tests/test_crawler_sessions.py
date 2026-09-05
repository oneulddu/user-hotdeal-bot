import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aiohttp import web
from scrapling.fetchers import AsyncStealthySession

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
async def test_scrapling_real_start_cancellation_stops_partial_driver(monkeypatch):
    from scrapling.engines._browsers import _stealth

    drivers = []
    started = asyncio.Queue()

    async def launch(**kwargs):
        started.put_nowait(True)
        await asyncio.Event().wait()

    def playwright_factory():
        driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=AsyncMock())
        drivers.append(driver)
        return SimpleNamespace(start=AsyncMock(return_value=driver))

    def make_session(**kwargs):
        # Run the installed dependency's real start/close methods without launching
        # a driver. Only the browser launch boundary is replaced.
        session = object.__new__(AsyncStealthySession)
        session.playwright = None
        session.context = None
        session.browser = None
        session._is_alive = False
        session._config = SimpleNamespace(cdp_url=None, proxy_rotator=None)
        session._browser_options = {}
        session._context_options = {}
        session._user_data_dir = "unused"
        return session

    monkeypatch.setattr(_stealth, "async_playwright", playwright_factory)
    monkeypatch.setattr(arcalive, "AsyncStealthySession", make_session)
    instance = ArcaLiveCrawlerV2("test", ["https://example.com"])
    try:
        for _ in range(2):
            task = asyncio.create_task(instance.request("https://example.com"))
            await asyncio.wait_for(started.get(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance._scrapling_session is None
        assert len(drivers) == 2
        for driver in drivers:
            driver.stop.assert_awaited_once()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_scrapling_total_deadline_bounds_real_fetch_retries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = AsyncStealthySession()
    session._is_alive = True
    session._config.retry_delay = 0
    attempts = []
    started = asyncio.Event()

    async def goto(url, **kwargs):
        attempts.append(url)
        started.set()
        if len(attempts) == 1:
            raise TimeoutError("first attempt timed out")
        await asyncio.Event().wait()

    @asynccontextmanager
    async def page_generator(*args):
        yield SimpleNamespace(page=SimpleNamespace(on=Mock(), goto=goto), mark_error=Mock())

    monkeypatch.setattr(session, "_page_generator", page_generator)
    instance = ArcaLiveCrawlerV2("test", ["https://example.com"], scrapling_session=session)
    instance._scrapling_session_started = True
    monkeypatch.setattr(instance, "SCRAPLING_TOTAL_TIMEOUT_SECONDS", 0.05)
    try:
        assert await asyncio.wait_for(instance.request("https://example.com"), timeout=1) is None
        assert len(attempts) == 2
    finally:
        await instance.close()


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
