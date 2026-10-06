"""Direct-first routing, failover limits and recovery against local HTTP endpoints."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from src.crawler import (
    ArcaLiveCrawler,
    ArcaLiveCrawlerV2,
    ArcaLiveCrawlerV15,
    DummyCrawler,
    QuasarzoneCrawler,
    base_crawler,
)
from src.http_client import AiohttpClient, CurlCffiClient, HttpClientError, HttpResponse
from tests.test_http_integration import local_server


@pytest.fixture(params=["aiohttp", "curl", "arcalive-v15"])
def transport(request):
    return request.param


def make_crawler(transport, url, proxy, **kwargs):
    if transport == "arcalive-v15":
        return ArcaLiveCrawlerV15("test", [url], proxy=proxy, **kwargs)
    cls = AiohttpClient if transport == "aiohttp" else CurlCffiClient
    instance = ArcaLiveCrawler("test", [url], proxy=proxy, client=cls(), **kwargs)
    # These test instances own the otherwise caller-owned client.
    instance._owns_client = True
    return instance


@pytest.mark.asyncio
async def test_real_routing_recovers_after_fixed_cooldown(monkeypatch, transport):
    requests = []
    blocked = [True]
    now = [1000.0]
    monkeypatch.setattr(base_crawler.time, "monotonic", lambda: now[0])

    async def direct(request):
        requests.append("direct")
        assert request.headers["X-Test"] == "kept"
        assert request.cookies["configured"] == "yes"
        return web.Response(status=403 if blocked[0] else 200, text="direct")

    async def proxy(request):
        requests.append("proxy")
        assert request.headers["X-Test"] == "kept"
        assert request.cookies["configured"] == "yes"
        return web.Response(text="proxy")

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        url += "/deals"
        instance = make_crawler(transport, url, proxy_url, request_headers={"X-Test": "kept"}, cookie="configured=yes")
        try:
            assert await instance.request(url) == "proxy"
            now[0] += 100
            assert await instance.request(url + "/other-board") == "proxy"
            # Successful proxied requests must not extend the original deadline.
            blocked[0] = False
            now[0] = 2200.0
            assert await instance.request(url) == "direct"
            assert await instance.request(url) == "direct"
            assert requests == ["direct", "proxy", "proxy", "direct", "direct"]
            assert instance._proxy_until == {}
        finally:
            await instance.close()


@pytest.mark.asyncio
async def test_direct_ignores_environment_proxy_but_explicit_fallback_ignores_no_proxy(monkeypatch, transport):
    calls = []
    blocked = [False]

    async def direct(request):
        calls.append("direct")
        return web.Response(status=403 if blocked[0] else 200, text="direct")

    async def proxy(request):
        calls.append("proxy")
        return web.Response(text="proxy")

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        url += "/deals"
        for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(key, proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        instance = make_crawler(transport, url, proxy_url)
        try:
            assert await instance.request(url) == "direct"
            blocked[0] = True
            monkeypatch.setenv("NO_PROXY", "127.0.0.1")
            monkeypatch.setenv("no_proxy", "127.0.0.1")
            assert await instance.request(url) == "proxy"
            assert calls == ["direct", "direct", "proxy"]
        finally:
            await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 500])
async def test_http_errors_do_not_trigger_proxy(monkeypatch, transport, status):
    calls = []

    async def direct(request):
        calls.append("direct")
        return web.Response(status=status, headers={"Retry-After": "120"})

    async def proxy(request):
        calls.append("proxy")
        return web.Response(text="proxy")

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        url += "/deals"
        instance = make_crawler(transport, url, proxy_url)
        monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
        try:
            assert await instance.request(url) is None
            assert instance._proxy_until == {}
            if status == 429:
                assert await instance.request(url) is None
                delay = instance._response_backoff_until[url] - base_crawler.time.monotonic()
                assert 119 < delay <= 120
            assert calls == ["direct"]
        finally:
            await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,challenge",
    [
        ("<title>Just a moment...</title><form id='challenge-form'></form>", True),
        ("<title>Attention Required! | Cloudflare</title>", True),
        ("<html>Changed board layout</html>", False),
        ("<div class='list-table'>Just a moment... /cdn-cgi/challenge-platform/</div>", False),
    ],
)
async def test_arcalive_challenge_detection_is_not_a_parser_retry(transport, body, challenge):
    calls = []

    async def direct(request):
        calls.append("direct")
        return web.Response(text=body)

    async def proxy(request):
        calls.append("proxy")
        return web.Response(text="proxy")

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        url += "/deals"
        instance = make_crawler(transport, url, proxy_url)
        try:
            assert await instance.request(url) == ("proxy" if challenge else body)
            assert calls == (["direct", "proxy"] if challenge else ["direct"])
        finally:
            await instance.close()


@pytest.mark.asyncio
async def test_parallel_requests_only_probe_direct_once(transport):
    calls = []

    async def direct(request):
        calls.append("direct")
        await asyncio.sleep(0.01)
        return web.Response(status=403)

    async def proxy(request):
        calls.append("proxy")
        return web.Response(text="proxy")

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        url += "/deals"
        instance = make_crawler(transport, url, proxy_url)
        try:
            assert (
                await asyncio.wait_for(asyncio.gather(*(instance.request(url + f"/{i}") for i in range(5))), timeout=5)
                == ["proxy"] * 5
            )
            assert calls == ["direct"] + ["proxy"] * 5
        finally:
            await instance.close()


class ScriptedClient:
    closed = False

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(kwargs.get("proxy"))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status=200, body=b"ok", headers=None):
    return HttpResponse(status, body, headers or {}, "https://example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeds_on_retry", [True, False])
async def test_transport_error_retries_direct_before_single_proxy_attempt(succeeds_on_retry):
    client = ScriptedClient(
        HttpClientError("timeout"),
        response() if succeeds_on_retry else HttpClientError("connection failed"),
        response(),
    )
    instance = DummyCrawler("test", [], client=client, proxy="http://proxy")
    assert await instance.request("https://example.com") == "ok"
    assert client.calls == (["", ""] if succeeds_on_retry else ["", "", "http://proxy"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [HttpClientError("offline"), response(403), response(200, b"challenge", {"cf-mitigated": "challenge"})]
)
async def test_failed_proxy_is_not_cached_or_retried(monkeypatch, failure):
    client = ScriptedClient(response(403), failure, response())
    instance = DummyCrawler("test", [], client=client, proxy="http://proxy")
    monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
    assert await instance.request("https://example.com") is None
    assert instance._proxy_until == {}
    assert await instance.request("https://example.com") == "ok"
    assert client.calls == ["", "http://proxy", ""]


@pytest.mark.asyncio
async def test_cancellation_does_not_trigger_failover():
    client = ScriptedClient(asyncio.CancelledError())
    instance = DummyCrawler("test", [], client=client, proxy="http://proxy")
    with pytest.raises(asyncio.CancelledError):
        await instance.request("https://example.com")
    assert client.calls == [""]
    assert not instance._proxy_locks["https://example.com"].locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_response", [response(403), HttpClientError("proxy offline")])
async def test_quasarzone_backoff_applies_after_fallback_is_exhausted(monkeypatch, proxy_response):
    client = ScriptedClient(response(403), proxy_response)
    instance = QuasarzoneCrawler("test", [], client=client, proxy="http://proxy")
    monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
    url = "https://example.com"
    assert await instance.request(url) is None
    assert await instance.request(url) is None
    assert client.calls == ["", "http://proxy"]
    assert instance._response_backoff_failures[url] == 1
    assert 299 < instance._response_backoff_until[url] - base_crawler.time.monotonic() <= 300


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers", [(403, {}), (200, {"cf-mitigated": "challenge"})])
async def test_failed_proxy_connection_preserves_direct_retry_after(monkeypatch, status, headers):
    client = ScriptedClient(
        response(status, headers={**headers, "Retry-After": "600"}), HttpClientError("proxy offline")
    )
    instance = QuasarzoneCrawler("test", [], client=client, proxy="http://proxy")
    monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
    url = "https://example.com"
    assert await instance.request(url) is None
    assert await instance.request(url) is None
    assert client.calls == ["", "http://proxy"]
    assert instance._response_backoff_failures[url] == 1
    assert 599 < instance._response_backoff_until[url] - base_crawler.time.monotonic() <= 600


@pytest.mark.asyncio
async def test_cooldown_is_isolated_per_origin_and_crawler():
    client = ScriptedClient(response(403), response(), response(), response())
    first = DummyCrawler("first", [], client=client, proxy="http://proxy")
    second = DummyCrawler("second", [], client=client, proxy="http://proxy")
    assert await first.request("https://example.com") == "ok"
    assert await first.request("https://other.example.com") == "ok"
    assert await second.request("https://example.com") == "ok"
    assert client.calls == ["", "http://proxy", "", ""]


def test_invalid_proxy_mode_and_unsupported_browser_mode_are_rejected():
    with pytest.raises(ValueError, match="proxy_mode"):
        DummyCrawler("test", [], proxy_mode="typo")
    with pytest.raises(ValueError, match="only supports"):
        ArcaLiveCrawlerV2("test", [], proxy_mode="fallback")


@pytest.mark.asyncio
async def test_real_transport_failure_reaches_proxy(transport):
    proxy_calls = []

    async def handler(request):
        proxy_calls.append(request.raw_path)
        return web.Response(text="proxy")

    # Closing the listener gives a real connection refusal without external I/O.
    async with local_server(handler) as unreachable_url:
        pass
    async with local_server(handler) as proxy_url:
        if unreachable_url == proxy_url:
            pytest.skip("OS reused the closed port")
        instance = make_crawler(transport, unreachable_url, proxy_url)
        try:
            assert await instance.request(unreachable_url + "/deals") == "proxy"
            assert len(proxy_calls) == 1
        finally:
            await instance.close()


@pytest.mark.asyncio
async def test_real_proxy_failure_does_not_prefer_broken_proxy(monkeypatch, transport):
    calls = []
    blocked = [True]

    async def direct(request):
        calls.append("direct")
        return web.Response(status=403 if blocked[0] else 200, text="direct")

    async def proxy(request):
        calls.append("proxy")
        return web.Response(status=403)

    async with local_server(direct) as url, local_server(proxy) as proxy_url:
        instance = make_crawler(transport, url, proxy_url)
        monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
        try:
            assert await instance.request(url + "/deals") is None
            assert instance._proxy_until == {}
            blocked[0] = False
            assert await instance.request(url + "/deals") == "direct"
            assert calls == ["direct", "proxy", "direct"]
        finally:
            await instance.close()


@pytest.mark.asyncio
async def test_in_flight_cached_proxy_success_does_not_extend_deadline(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(base_crawler.time, "monotonic", lambda: now[0])

    class SlowClient(ScriptedClient):
        async def get(self, url, **kwargs):
            now[0] = 1002.0
            return await super().get(url, **kwargs)

    client = SlowClient(response(), response())
    instance = DummyCrawler("test", [], client=client, proxy="http://proxy")
    instance._proxy_until["https://example.com"] = 1001.0
    assert await instance.request("https://example.com") == "ok"
    assert instance._proxy_until["https://example.com"] == 1001.0
    assert await instance.request("https://example.com") == "ok"
    assert client.calls == ["http://proxy", ""]


@pytest.mark.asyncio
async def test_undecodable_response_does_not_trigger_proxy(monkeypatch):
    client = ScriptedClient(response(body=b"\xff"))
    instance = ArcaLiveCrawler("test", [], client=client, proxy="http://proxy")
    monkeypatch.setattr(instance, "dump_http_response", AsyncMock())
    assert await instance.request("https://example.com") is None
    assert client.calls == [""]
    assert instance._proxy_until == {}
