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

    async def get(self, url, **kwargs):
        self.get_url = url
        self.get_kwargs = kwargs
        return FakeCurlResponse()


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
    assert session.kwargs["impersonate"] == "chrome124"
    assert session.kwargs["proxies"] == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }
    assert session.kwargs["timeout"] == 12
    assert session.kwargs["verify"] is True
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

    assert created_sessions[0].kwargs["verify"] is False
    assert created_sessions[1].kwargs["verify"] == "/path/to/ca-bundle.crt"
    await no_verify_crawler.close()
    await ca_crawler.close()
