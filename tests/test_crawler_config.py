import asyncio
import datetime
import os

import aiohttp
import pytest

from src import crawler
from src.crawler import base_crawler


class FakeScraplingResponse:
    status = 200
    html_content = "<html><body>ok</body></html>"


class FakeScraplingSession:
    def __init__(self):
        self.started = False
        self.closed = False
        self.fetch_url = None
        self.fetch_kwargs = None

    async def start(self):
        self.started = True

    async def fetch(self, url, **kwargs):
        self.fetch_url = url
        self.fetch_kwargs = kwargs
        return FakeScraplingResponse()

    async def close(self):
        self.closed = True


class FakeCurlResponse:
    status_code = 200
    text = "<html><body>ok</body></html>"


class FakeCurlSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.get_url = None
        self.get_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def close(self):
        return None

    async def get(self, url, **kwargs):
        self.get_url = url
        self.get_kwargs = kwargs
        return FakeCurlResponse()


class ConcurrentCrawler(crawler.BaseCrawler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_urls = []
        self.second_started = asyncio.Event()

    async def request(self, url: str) -> str | None:
        self.started_urls.append(url)
        if len(self.started_urls) == 1:
            await self.second_started.wait()
        else:
            self.second_started.set()
        return url

    async def parsing(self, html: str) -> dict[int, crawler.BaseArticle]:
        article_id = int(html.rsplit("/", 1)[-1])
        return {
            article_id: crawler.BaseArticle(
                article_id=article_id,
                title=f"Article {article_id}",
                category="category",
                site_name="site",
                board_name="board",
                writer_name="writer",
                crawler_name=self.name,
                url=html,
                is_end=False,
                extra={},
            )
        }


class FakeDumpResponse:
    async def read(self) -> bytes:
        return b"<html>error</html>"


class FakeHTTPResponse:
    def __init__(self, status, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return None

    async def read(self):
        return self.body.encode()

    def get_encoding(self):
        return "utf-8"

    async def text(self, encoding=None):
        return self.body


class FakeHTTPSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    async def get(self, url, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class ConcurrentHTTPSession(FakeHTTPSession):
    def __init__(self, responses):
        super().__init__(responses)
        self.second_started = asyncio.Event()

    async def get(self, url, **kwargs):
        call_index = self.calls
        self.calls += 1
        if call_index == 0:
            await self.second_started.wait()
        else:
            self.second_started.set()
        return self.responses[call_index]


@pytest.mark.asyncio
async def test_dummy_crawler_accepts_base_crawler_options():
    async with aiohttp.ClientSession() as session:
        crawler_instance = crawler.DummyCrawler(
            "dummy",
            ["https://example.com"],
            session=session,
            proxy="http://127.0.0.1:8080",
            ssl_verify=False,
            request_headers={"Referer": "https://example.com"},
            cookie="foo=bar; baz=qux",
        )

        assert crawler_instance.proxy == "http://127.0.0.1:8080"
        assert crawler_instance.ssl_verify is False
        assert crawler_instance.request_headers == {"Referer": "https://example.com"}
        assert crawler_instance.request_cookies == {"foo": "bar", "baz": "qux"}


@pytest.mark.asyncio
async def test_base_crawler_fetches_multiple_urls_concurrently():
    async with aiohttp.ClientSession() as session:
        crawler_instance = ConcurrentCrawler(
            "concurrent",
            ["https://example.com/1", "https://example.com/2"],
            session=session,
        )

        data = await asyncio.wait_for(crawler_instance.get(), timeout=0.2)

    assert set(data) == {1, 2}
    assert crawler_instance.started_urls == ["https://example.com/1", "https://example.com/2"]


@pytest.mark.asyncio
async def test_quasarzone_403_uses_escalating_backoff_and_resets_after_success(monkeypatch):
    clock = [1000.0]
    responses = [
        FakeHTTPResponse(403),
        FakeHTTPResponse(429),
        FakeHTTPResponse(403),
        FakeHTTPResponse(403),
        FakeHTTPResponse(403),
        FakeHTTPResponse(200, body="<html>ok</html>"),
    ]
    session = FakeHTTPSession(responses)
    crawler_instance = crawler.QuasarzoneCrawler(
        "quasarzone_saleinfo",
        ["https://quasarzone.com/bbs/qb_saleinfo"],
        session=session,
    )

    async def skip_dump(response):
        return None

    monkeypatch.setattr(base_crawler.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(crawler_instance, "dump_http_response", skip_dump)
    url = crawler_instance.url_list[0]

    assert await crawler_instance.request(url) is None
    assert session.calls == 1
    assert crawler_instance._response_backoff_until[url] == 1300.0

    clock[0] = 1299.0
    assert await crawler_instance.request(url) is None
    assert session.calls == 1

    clock[0] = 1300.0
    assert await crawler_instance.request(url) is None
    assert session.calls == 2
    assert crawler_instance._response_backoff_until[url] == 3100.0

    clock[0] = 3100.0
    assert await crawler_instance.request(url) is None
    assert crawler_instance._response_backoff_until[url] == 10300.0

    clock[0] = 10300.0
    assert await crawler_instance.request(url) is None
    assert crawler_instance._response_backoff_until[url] == 53500.0

    clock[0] = 53500.0
    assert await crawler_instance.request(url) is None
    assert crawler_instance._response_backoff_until[url] == 96700.0

    clock[0] = 96700.0
    assert await crawler_instance.request(url) == "<html>ok</html>"
    assert session.calls == 6
    assert url not in crawler_instance._response_backoff_until
    assert url not in crawler_instance._response_backoff_failures
    assert all(response.exited for response in responses)


def test_retry_after_http_date_uses_utc_delay():
    now = datetime.datetime(2026, 8, 26, 0, 0, tzinfo=datetime.UTC)

    delay = base_crawler.parse_retry_after_seconds(
        "Wed, 26 Aug 2026 02:00:00 GMT",
        now=now,
    )

    assert delay == 7200
    assert base_crawler.parse_retry_after_seconds("not-a-date", now=now) is None
    assert base_crawler.parse_retry_after_seconds("9" * 5000, now=now) == base_crawler.MAX_RETRY_AFTER_SECONDS
    assert base_crawler.parse_retry_after_seconds("9999999999", now=now) == base_crawler.MAX_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_quasarzone_duplicate_url_counts_one_concurrent_failure(monkeypatch):
    clock = [1000.0]
    response = FakeHTTPResponse(429)
    session = FakeHTTPSession([response])
    url = "https://quasarzone.com/bbs/qb_saleinfo"
    crawler_instance = crawler.QuasarzoneCrawler(
        "quasarzone_saleinfo",
        [url, url],
        session=session,
    )

    async def skip_dump(dump_response):
        return None

    monkeypatch.setattr(base_crawler.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(crawler_instance, "dump_http_response", skip_dump)

    assert await crawler_instance.get() == {}
    assert session.calls == 1
    assert crawler_instance._response_backoff_failures[url] == 1
    assert crawler_instance._response_backoff_until[url] == 1300.0
    assert response.exited is True


@pytest.mark.asyncio
async def test_backoff_scope_is_quasarzone_only(monkeypatch):
    assert crawler.QuasarzoneMobileCrawler.RESPONSE_BACKOFF_STATUS_CODES == frozenset({403, 429})

    responses = [FakeHTTPResponse(403), FakeHTTPResponse(403)]
    session = FakeHTTPSession(responses)
    crawler_instance = crawler.DummyCrawler(
        "dummy",
        ["https://example.com"],
        session=session,
    )

    async def skip_dump(response):
        return None

    monkeypatch.setattr(crawler_instance, "dump_http_response", skip_dump)
    assert await crawler_instance.request("https://example.com") is None
    assert await crawler_instance.request("https://example.com") is None
    assert session.calls == 2
    assert crawler_instance._response_backoff_until == {}


@pytest.mark.asyncio
async def test_non_backoff_crawler_keeps_duplicate_url_requests_concurrent():
    url = "https://example.com"
    session = ConcurrentHTTPSession(
        [
            FakeHTTPResponse(200, body="first"),
            FakeHTTPResponse(200, body="second"),
        ]
    )
    crawler_instance = crawler.DummyCrawler(
        "dummy",
        [url],
        session=session,
    )

    results = await asyncio.wait_for(
        asyncio.gather(crawler_instance.request(url), crawler_instance.request(url)),
        timeout=0.2,
    )

    assert results == ["first", "second"]
    assert session.calls == 2


@pytest.mark.asyncio
async def test_retry_after_value_is_bounded_before_deadline(monkeypatch):
    clock = [1000.0]
    response = FakeHTTPResponse(429, headers={"Retry-After": "9" * 5000})
    session = FakeHTTPSession([response])
    url = "https://quasarzone.com/bbs/qb_saleinfo"
    crawler_instance = crawler.QuasarzoneCrawler("quasarzone_saleinfo", [url], session=session)

    async def skip_dump(dump_response):
        return None

    monkeypatch.setattr(base_crawler.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(crawler_instance, "dump_http_response", skip_dump)

    assert await crawler_instance.request(url) is None
    assert crawler_instance._response_backoff_until[url] == 1000.0 + base_crawler.MAX_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_dump_http_response_keeps_only_recent_error_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    error_dir = tmp_path / "error"
    error_dir.mkdir()
    for i in range(51):
        (error_dir / f"20000101_0000{i:02d}_dummy.html").write_text("old", encoding="utf-8")

    async with aiohttp.ClientSession() as session:
        crawler_instance = crawler.DummyCrawler("dummy", ["https://example.com"], session=session)
        await crawler_instance.dump_http_response(FakeDumpResponse())

    dumps = sorted(os.listdir(error_dir))
    assert len(dumps) == 50
    assert "20000101_000000_dummy.html" not in dumps
    assert "20000101_000001_dummy.html" not in dumps


@pytest.mark.asyncio
async def test_arcalive_v2_uses_scrapling_session_options():
    fake_session = FakeScraplingSession()
    crawler_instance = crawler.ArcaLiveCrawlerV2(
        "arcalive_hotdeal_v2",
        ["https://arca.live/b/hotdeal"],
        request_headers={"Referer": "https://arca.live/b/hotdeal", "User-Agent": "Custom UA"},
        cookie="foo=bar",
        scrapling_session=fake_session,
    )

    html = await crawler_instance.request("https://arca.live/b/hotdeal")
    await crawler_instance.close()

    assert html == FakeScraplingResponse.html_content
    assert fake_session.started is True
    assert fake_session.closed is True
    assert fake_session.fetch_url == "https://arca.live/b/hotdeal"
    assert fake_session.fetch_kwargs["google_search"] is False
    assert fake_session.fetch_kwargs["solve_cloudflare"] is True
    assert fake_session.fetch_kwargs["wait_selector"] == ".list-table"
    assert fake_session.fetch_kwargs["extra_headers"] == {
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": "https://arca.live/b/hotdeal",
    }
    assert crawler_instance._scrapling_session is fake_session


@pytest.mark.asyncio
async def test_arcalive_v2_builds_stealth_session_with_persistent_profile(monkeypatch):
    created_kwargs = {}

    class FakeStealthSession(FakeScraplingSession):
        def __init__(self, **kwargs):
            super().__init__()
            created_kwargs.update(kwargs)

    monkeypatch.setattr(crawler.arcalive, "AsyncStealthySession", FakeStealthSession)
    monkeypatch.setenv("ARCALIVE_SCRAPLING_TIMEOUT_MS", "1234")
    monkeypatch.setenv("ARCALIVE_SCRAPLING_WAIT_MS", "567")

    crawler_instance = crawler.ArcaLiveCrawlerV2(
        "arcalive_hotdeal_v2",
        ["https://arca.live/b/hotdeal"],
        cookie="foo=bar",
    )

    await crawler_instance.request("https://arca.live/b/hotdeal")
    await crawler_instance.close()

    assert created_kwargs["headless"] is True
    assert created_kwargs["disable_resources"] is False
    assert created_kwargs["solve_cloudflare"] is True
    assert created_kwargs["user_data_dir"] == "./data/scrapling-arcalive"
    assert created_kwargs["hide_canvas"] is True
    assert created_kwargs["block_webrtc"] is True
    assert created_kwargs["cookies"] == [{"name": "foo", "value": "bar", "url": "https://arca.live/b/hotdeal"}]
    assert crawler_instance._scrapling_session.fetch_kwargs["timeout"] == 1234
    assert crawler_instance._scrapling_session.fetch_kwargs["wait"] == 567


@pytest.mark.asyncio
async def test_arcalive_v15_uses_chrome_impersonation_options(monkeypatch):
    created_sessions = []

    class RecordingCurlSession(FakeCurlSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created_sessions.append(self)

    monkeypatch.setattr(crawler.arcalive, "CurlAsyncSession", RecordingCurlSession)
    monkeypatch.setenv("ARCALIVE_CURL_TIMEOUT", "12")

    crawler_instance = crawler.ArcaLiveCrawlerV15(
        "arcalive_hotdeal_v15",
        ["https://arca.live/b/hotdeal"],
        request_headers={"Referer": "https://arca.live/b/hotdeal", "User-Agent": "Custom UA"},
        cookie="foo=bar",
        proxy="http://127.0.0.1:8080",
    )

    html = await crawler_instance.request("https://arca.live/b/hotdeal")

    assert html == FakeCurlResponse.text
    session = created_sessions[0]
    assert session.kwargs["discard_cookies"] is True
    assert session.get_kwargs["impersonate"] == "chrome124"
    assert session.get_kwargs["proxies"] == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }
    assert session.get_kwargs["timeout"] == 12
    assert session.get_kwargs["verify"] is True
    assert session.get_url == "https://arca.live/b/hotdeal"
    assert session.get_kwargs["headers"] == {
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": "https://arca.live/b/hotdeal",
    }
    assert session.get_kwargs["cookies"] == {"foo": "bar"}
    assert session.get_kwargs["allow_redirects"] is False
    await crawler_instance.close()


@pytest.mark.asyncio
async def test_arcalive_v15_honors_ssl_options(monkeypatch):
    created_sessions = []

    class RecordingCurlSession(FakeCurlSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created_sessions.append(self)

    monkeypatch.setattr(crawler.arcalive, "CurlAsyncSession", RecordingCurlSession)
    monkeypatch.setattr(base_crawler.ssl, "create_default_context", lambda cafile: object())

    no_verify_crawler = crawler.ArcaLiveCrawlerV15(
        "arcalive_hotdeal_v15",
        ["https://arca.live/b/hotdeal"],
        ssl_verify=False,
    )
    ca_crawler = crawler.ArcaLiveCrawlerV15(
        "arcalive_hotdeal_v15",
        ["https://arca.live/b/hotdeal"],
        ssl_ca_cert="/path/to/ca-bundle.crt",
    )

    await no_verify_crawler.request("https://arca.live/b/hotdeal")
    await ca_crawler.request("https://arca.live/b/hotdeal")

    assert created_sessions[0].get_kwargs["verify"] is False
    assert created_sessions[1].get_kwargs["verify"] == "/path/to/ca-bundle.crt"
    await no_verify_crawler.close()
    await ca_crawler.close()
