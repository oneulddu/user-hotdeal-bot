import asyncio
import datetime
from contextlib import asynccontextmanager
from email.utils import format_datetime
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from curl_cffi import AsyncSession

from src import crawler, http_client
from src.http_client import AiohttpClient, CurlCffiClient, HttpClientError, HttpResponse, HttpTimeoutError
from src.main import BotManager
from tests.test_http_client import FakeAiohttpResponse, FakeCurlResponse, FakeHttpClient


@asynccontextmanager
async def local_server(handler):
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_shared_curl_reuses_connections_and_isolates_crawler_cookies():
    requests, transports = [], []

    async def handler(request):
        requests.append((request.headers.get("X-Crawler"), dict(request.cookies)))
        transports.append(request.transport)
        response = web.Response(text="본문")
        response.set_cookie("response_cookie", "must-not-leak")
        return response

    async with local_server(handler) as url:
        client = CurlCffiClient(trust_env=False)
        first = crawler.DummyCrawler(
            "first", [url], client=client, request_headers={"X-Crawler": "first"}, cookie="id=first"
        )
        second = crawler.DummyCrawler(
            "second", [url], client=client, request_headers={"X-Crawler": "second"}, cookie="id=second"
        )
        try:
            for instance in (first, second, first):
                assert await instance.request(url) == "본문"
            await first.close()
            assert not client.closed
            assert requests == [("first", {"id": "first"}), ("second", {"id": "second"}), ("first", {"id": "first"})]
            assert len(set(transports)) == 1
        finally:
            await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AiohttpClient, CurlCffiClient])
async def test_crawler_routes_requests_through_configured_proxy(client_type):
    paths = []

    async def proxy(request):
        paths.append(request.raw_path)
        assert request.headers["X-Crawler"] == "proxy-test"
        assert request.cookies["configured"] == "value"
        return web.Response(text="proxied")

    async with local_server(proxy) as proxy_url:
        client = client_type(trust_env=False)
        instance = crawler.DummyCrawler(
            "proxy",
            [],
            client=client,
            proxy=proxy_url,
            request_headers={"X-Crawler": "proxy-test"},
            cookie="configured=value",
        )
        try:
            assert await instance.request("http://upstream.invalid/deals") == "proxied"
            assert paths == ["http://upstream.invalid/deals"]
        finally:
            await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AiohttpClient, CurlCffiClient])
@pytest.mark.parametrize("verify", [False, "/test/ca.pem"])
async def test_crawler_ssl_options_reach_native_transport_and_reuse_context(monkeypatch, client_type, verify):
    contexts = []
    calls = []

    def create_context(*, cafile):
        assert cafile == verify
        contexts.append(object())
        return contexts[-1]

    monkeypatch.setattr(http_client.ssl, "create_default_context", create_context)

    class Session:
        closed = False

        async def get(self, url, **kwargs):
            calls.append(kwargs)
            return FakeAiohttpResponse(b"ok") if client_type is AiohttpClient else FakeCurlResponse(b"ok")

        async def close(self):
            self.closed = True

    client = client_type(session=Session())
    instance = crawler.DummyCrawler(
        "ssl",
        [],
        client=client,
        ssl_verify=verify is not False,
        ssl_ca_cert=verify if isinstance(verify, str) else None,
    )
    try:
        assert await instance.request("https://example.com") == "ok"
        assert await instance.request("https://example.com") == "ok"
        option = "ssl" if client_type is AiohttpClient else "verify"
        expected = contexts[0] if contexts else verify
        assert calls[0][option] is expected or calls[0][option] == expected
        assert calls[1][option] is calls[0][option]
        assert len(contexts) == int(client_type is AiohttpClient and isinstance(verify, str))
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [AiohttpClient, CurlCffiClient])
@pytest.mark.parametrize("date_header", [False, True])
async def test_native_retry_after_header_reaches_crawler_backoff(monkeypatch, client_type, date_header):
    value = (
        format_datetime(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1), usegmt=True)
        if date_header
        else "3600"
    )

    async def handler(request):
        return web.Response(status=429, headers={"rEtRy-AfTeR": value})

    async with local_server(handler) as url:
        client = client_type(trust_env=False)
        instance = crawler.QuasarzoneCrawler("quasarzone", [url], client=client)
        monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
        try:
            assert await instance.request(url) is None
            delay = instance._response_backoff_until[url] - asyncio.get_running_loop().time()
            assert 3590 < delay <= 3600
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_curl_option_failures_do_not_exhaust_pool_and_next_valid_request_recovers():
    async def handler(request):
        return web.Response(text="recovered")

    async with local_server(handler) as url:
        client = CurlCffiClient(trust_env=False)
        try:
            async with asyncio.timeout(3):
                for _ in range(12):
                    with pytest.raises(HttpClientError):
                        await client.get(url, headers={"bad": object()})
                assert (await client.get(url)).text() == "recovered"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_curl_deadline_includes_native_pool_wait():
    native = AsyncSession(max_clients=1, trust_env=False)
    # Consume the only placeholder; no socket or native handle is opened.
    await native.pool.get()
    client = CurlCffiClient(session=native, timeout=0.02)
    try:
        async with asyncio.timeout(1):
            with pytest.raises(HttpTimeoutError):
                await client.get("http://127.0.0.1/")
        assert client.closed
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "cancel", "timeout"])
async def test_curl_retires_failed_pool_after_other_active_request_finishes(monkeypatch, failure):
    created = []
    slow_started, fail_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Session:
        closed = False

        def __init__(self, **kwargs):
            created.append(self)

        async def get(self, url, **kwargs):
            if url == "slow":
                slow_started.set()
                await release.wait()
                assert not self.closed
            elif url == "fail":
                fail_started.set()
                if failure == "error":
                    raise ValueError("bad option")
                await asyncio.Event().wait()
            return FakeCurlResponse(b"ok")

        async def close(self):
            assert not self.closed
            self.closed = True

    monkeypatch.setattr(http_client, "AsyncSession", Session)
    client = CurlCffiClient(timeout=2)
    slow = asyncio.create_task(client.get("slow"))
    try:
        await asyncio.wait_for(slow_started.wait(), 1)
        if failure == "timeout":
            client._timeout = 0.02
        failed = asyncio.create_task(client.get("fail"))
        await asyncio.wait_for(fail_started.wait(), 1)
        if failure == "cancel":
            failed.cancel()
        error = (
            asyncio.CancelledError
            if failure == "cancel"
            else HttpTimeoutError
            if failure == "timeout"
            else HttpClientError
        )
        with pytest.raises(error):
            await failed
        assert not created[0].closed
        assert (await client.get("replacement")).text() == "ok"
        assert len(created) == 2
        release.set()
        assert (await slow).text() == "ok"
        assert created[0].closed
    finally:
        release.set()
        await slow
        await client.close()
    assert created[1].closed


@pytest.mark.asyncio
async def test_manager_reuses_live_shared_client_and_closes_private_crawler_resources(monkeypatch):
    client = FakeHttpClient(HttpResponse(200, b"ok", {}, "https://example.com"))
    manager = BotManager(http_client=client)
    manager.crawlers = {}
    manager.bots = {}
    config = {"dummy": {"crawler_name": "DummyCrawler", "url_list": ["https://example.com"]}}
    await manager.init_crawlers(config)
    first = manager.crawlers["dummy"]
    await manager.init_crawlers(config)
    assert manager.crawlers["dummy"] is first
    assert first.client is client
    assert await first.request("https://example.com") == "ok"
    first.close = AsyncMock()
    # Private crawler/browser resources must close even if the shared client closed early.
    await client.close()
    manager.dump = AsyncMock()
    monkeypatch.setattr("src.main.close_db", AsyncMock())
    await manager.close()
    first.close.assert_awaited_once()


@pytest.mark.parametrize(
    "charset,body,expected",
    [("EUC_KR", "똠".encode("cp949"), "똠"), ("utf-8", "한글".encode(), "한글"), (None, "한글".encode(), "한글")],
)
def test_http_response_decoding(charset, body, expected):
    assert HttpResponse(200, body, {}, "", charset).text() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("charset", [None, "unknown-charset"])
async def test_undecodable_response_is_dumped_without_mojibake(monkeypatch, charset):
    response = HttpResponse(200, "한글".encode("cp949"), {}, "https://example.com", charset)
    instance = crawler.DummyCrawler("decode", [], client=FakeHttpClient(response))
    dump = AsyncMock()
    monkeypatch.setattr(instance, "dump_http_response", dump)
    assert await instance.request(response.url) is None
    dump.assert_awaited_once_with(response)


@pytest.mark.asyncio
async def test_aiohttp_preserves_injected_charset_resolver_and_session_ownership():
    async def handler(request):
        return web.Response(body="한글".encode("cp949"), content_type="text/html")

    async with local_server(handler) as url:
        async with aiohttp.ClientSession(fallback_charset_resolver=lambda *_: "cp949") as session:
            instance = crawler.DummyCrawler("legacy", [url], session=session)
            assert await instance.request(url) == "한글"
            await instance.close()
            assert not session.closed


@pytest.mark.asyncio
async def test_aiohttp_connector_closes_cloudflare_resolver_once(monkeypatch):
    connector = http_client.CloudflareDNSConnector()
    close = AsyncMock(wraps=connector.dns_resolver.close)
    monkeypatch.setattr(connector.dns_resolver, "close", close)
    await connector.close()
    await connector.close()
    close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_aiohttp_releases_native_response_on_read_failure(cancel):
    response = FakeAiohttpResponse(b"")
    response.read = AsyncMock(
        side_effect=asyncio.CancelledError() if cancel else aiohttp.ClientPayloadError("truncated")
    )
    session = AsyncMock()
    session.get.return_value = response
    client = AiohttpClient(session=session)
    with pytest.raises(asyncio.CancelledError if cancel else HttpClientError):
        await client.get("https://example.com")
    assert response.released
