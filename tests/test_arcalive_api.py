"""App API snapshots, HTML compatibility and failure-safe notification updates."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from src.crawler import ArcaLiveCrawler, ArcaLiveCrawlerV2, ArticleCollection
from src.http_client import HttpClientError, HttpResponse
from src.main import BotManager

URL = "https://arca.live/b/hotdeal"
CHANNEL = {"slug": "hotdeal", "name": "핫딜 채널", "categoryData": [{"id": "pc", "displayName": "PC/하드웨어"}]}


def deal(article_id=100, **updates):
    item = {
        "id": article_id,
        "title": "  SSD & 케이스  ",
        "nickname": "작성자",
        "category": "pc",
        "categoryDisplayName": "PC/하드웨어",
        "ratingUp": 5,
        "viewCount": 100,
        "deal": {
            "isClosed": False,
            "store": "판매처",
            "price": {"currency": "KRW", "number": 14990},
            "delivery": {"currency": "KRW", "number": 0},
        },
    }
    item.update(updates)
    return item


def envelope(articles, channel=None):
    return json.dumps({"channel": channel or CHANNEL, "articles": articles}, ensure_ascii=False)


def response(payload, *, status=200, headers=None):
    return HttpResponse(status, json.dumps(payload).encode(), headers or {"Content-Type": "application/json"}, URL)


class ApiClient:
    closed = False

    def __init__(self, articles=None):
        self.articles = [deal()] if articles is None else articles
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, copy.deepcopy(kwargs)))
        if "/info/" in url:
            slug = url.rsplit("/", 1)[-1]
            return response({"channel": {**CHANNEL, "slug": slug, "name": "핫딜 채널" if slug == "hotdeal" else slug}})
        return response({"articles": self.articles})

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "currency,amount,price",
    [
        ("KRW", 14990, "14,990원"),
        ("USD", 119.99, "$119.99"),
        ("JPY", 18164, "¥18,164"),
        ("EUR", 12.5, "€12.5"),
        ("KRW", 0, "0원"),
    ],
)
@pytest.mark.parametrize("closed", [False, True])
async def test_api_matches_html_snapshot_and_does_not_edit_existing_messages(currency, amount, price, closed):
    client = ApiClient()
    api = ArcaLiveCrawlerV2("same_key", [URL], client=client)
    html_crawler = ArcaLiveCrawler("same_key", [URL], client=client)
    item = deal()
    item["deal"].update(
        isClosed=closed, price={"currency": currency, "number": amount}, delivery={"currency": "KRW", "number": 3500}
    )
    # Reduced paired HTML/JSON fixtures with the production selectors and formats.
    html = f"""<div class="board-title"><span class="title" data-channel-name="핫딜 채널"></span></div>
    <div class="list-table"><div class="vrow hybrid">
    <a class="title" href="/b/hotdeal/100?p=1">  SSD &amp; 케이스  <span>[3]</span></a>
    <span class="badge">PC/하드웨어</span><span class="deal-store">판매처</span>
    <span class="user-info"><span>작성자</span></span><span class="col-rate">5</span><span class="col-view">100</span>
    <span class="deal-price">{price}</span><span class="deal-delivery">3,500원</span>
    {'<span class="deal-close"></span>' if closed else ""}</div></div>"""
    original = await html_crawler.parsing(html)
    parsed = await api.parsing(envelope([item]))
    assert parsed == original
    client.articles = [item]
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {"same_key": ArticleCollection(original)}
    assert await manager._crawling("same_key", api) == {"new": [], "update": [], "remove": []}


@pytest.mark.asyncio
async def test_api_preserves_settings_filters_device_token_and_caches_metadata():
    client = ApiClient()
    original_get = client.get

    async def get(url, **kwargs):
        result = await original_get(url, **kwargs)
        return HttpResponse(result.status, result.body, {"x-device-token": "server-device"}, result.url)

    client.get = get
    cwr = ArcaLiveCrawlerV2(
        "existing",
        [URL + "?category=pc&target=all&keyword=SSD+%26+RAM&p=1"],
        client=client,
        proxy="http://proxy",
        ssl_ca_cert="ca.pem",
        cookie="foo=bar",
        request_headers={"user-agent": "custom-app", "x-device-token": "initial"},
    )
    first = await cwr.get()
    second = await cwr.get()
    assert first == second
    assert first[100]["crawler_name"] == "existing"
    assert len(client.calls) == 3
    url, options = client.calls[0]
    assert urlsplit(url).path == "/api/app/list/channel/hotdeal"
    assert parse_qs(urlsplit(url).query) == {
        "category": ["pc"],
        "target": ["all"],
        "keyword": ["SSD & RAM"],
        "limit": ["30"],
    }
    assert options["proxy"] == "http://proxy"
    assert options["verify"] == "ca.pem"
    assert options["cookies"] == {"foo": "bar"}
    assert options["allow_redirects"] is False
    assert options["headers"]["User-Agent"] == "custom-app"
    assert options["headers"]["X-Device-Token"] == "initial"
    assert client.calls[-1][1]["headers"]["X-Device-Token"] == "server-device"
    assert len(options["headers"].getall("User-Agent")) == 1
    await cwr.close()
    assert not client.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://arca.live/b/hotdeal",
        "https://evil.example/b/hotdeal",
        URL + "/123",
        URL + "?p=2",
        URL + "?sort=rating",
        URL + "?unknown=x",
    ],
)
def test_unsupported_urls_fail_before_transport_allocation(monkeypatch, url):
    factory = AsyncMock()
    monkeypatch.setattr("src.crawler.base_crawler.create_default_http_client", factory)
    with pytest.raises(ValueError):
        ArcaLiveCrawlerV2("test", [url])
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        None,
        {},
        {"isClosed": False},
        {"isClosed": "false"},
        {
            "isClosed": False,
            "price": {"currency": "KRW", "number": float("nan")},
            "delivery": {"currency": "KRW", "number": 0},
        },
    ],
)
async def test_invalid_deal_cannot_create_false_deletions(broken):
    client = ApiClient([deal(100), deal(101, deal=broken)])
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client)
    previous = await cwr.parsing(envelope([deal(100), deal(101)]))
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {"test": ArticleCollection(copy.deepcopy(previous))}
    assert await manager._crawling("test", cwr) == {"new": [], "update": [], "remove": []}
    assert manager.article_cache["test"] == previous


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "<html>blocked</html>",
        "null",
        "{}",
        '{"articles": []}',
        envelope([deal(), deal()]),
        envelope([deal(title=None)]),
        envelope([deal(viewCount=None)]),
    ],
)
async def test_malformed_snapshots_are_rejected(body):
    cwr = ArcaLiveCrawlerV2("test", [URL], client=ApiClient())
    assert await cwr.parsing(body) == {}


@pytest.mark.asyncio
async def test_explicit_notice_skipped_category_fallback_and_free_delivery():
    cwr = ArcaLiveCrawlerV2("test", [URL], client=ApiClient())
    parsed = await cwr.parsing(envelope([{"isNotice": True, "id": 99}, deal(categoryDisplayName=None)]))
    assert list(parsed) == [100]
    assert parsed[100]["category"] == "PC/하드웨어"
    assert parsed[100]["extra"]["delivery"] == "무료"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "429", "malformed", "empty", "duplicate"])
async def test_multiple_urls_fail_as_one_snapshot(failure, monkeypatch):
    client = ApiClient()
    base_get = client.get

    async def get(url, **kwargs):
        if "/list/channel/other" in url:
            if failure == "transport":
                raise HttpClientError("unavailable")
            if failure == "429":
                return response({}, status=429, headers={"Retry-After": "120"})
            if failure == "malformed":
                return response({"articles": [deal(deal=None)]})
            if failure == "empty":
                return response({"articles": []})
        return await base_get(url, **kwargs)

    client.get = get
    cwr = ArcaLiveCrawlerV2("test", [URL, "https://arca.live/b/other"], client=client)
    monkeypatch.setattr(cwr, "dump_http_response", AsyncMock())
    manager = BotManager()
    manager.bots = {}
    previous = await cwr.parsing(envelope([deal(100), deal(101)]))
    manager.article_cache = {"test": ArticleCollection(copy.deepcopy(previous))}
    assert await manager._crawling("test", cwr) == {"new": [], "update": [], "remove": []}
    assert manager.article_cache["test"] == previous
    if failure == "429":
        assert cwr._response_backoff_until
        assert await cwr.get() == {}


@pytest.mark.asyncio
async def test_parallel_channels_keep_their_own_context():
    client = ApiClient()
    base_get = client.get

    async def get(url, **kwargs):
        await asyncio.sleep(0)
        if "/list/channel/other" in url:
            return response({"articles": [deal(101)]})
        return await base_get(url, **kwargs)

    client.get = get
    cwr = ArcaLiveCrawlerV2("test", [URL, "https://arca.live/b/other"], client=client)
    data = await cwr.get()
    assert data[100]["board_name"] == "핫딜 채널"
    assert data[101]["board_name"] == "other"
    assert data[101]["url"] == "https://arca.live/b/other/101"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 200])
async def test_blocked_direct_request_falls_back_to_proxy(status):
    client = ApiClient()
    base_get = client.get
    routes = []

    async def get(url, **kwargs):
        routes.append(kwargs.get("proxy"))
        if kwargs.get("proxy") == "":
            return HttpResponse(status, b"<html>challenge</html>", {}, url)
        return await base_get(url, **kwargs)

    client.get = get
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client, proxy="http://proxy", proxy_mode="fallback")
    assert await cwr.get()
    assert await cwr.get()
    assert routes == ["", "http://proxy", "http://proxy", "http://proxy"]


@pytest.mark.asyncio
async def test_channel_info_failure_does_not_discard_valid_list(monkeypatch):
    client = ApiClient()
    base_get = client.get

    async def get(url, **kwargs):
        if "/info/" in url:
            return response({}, status=429, headers={"Retry-After": "120"})
        return await base_get(url, **kwargs)

    client.get = get
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client)
    monkeypatch.setattr(cwr, "dump_http_response", AsyncMock())
    assert (await cwr.get())[100]["board_name"] == "핫딜 채널"
    assert cwr._channels == {}


@pytest.mark.asyncio
async def test_session_ownership_and_cancellation():
    async with aiohttp.ClientSession() as session:
        cwr = ArcaLiveCrawlerV2("test", [], session=session)
        await cwr.close()
        assert not session.closed
    client = ApiClient()
    client.get = AsyncMock(side_effect=asyncio.CancelledError)
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client)
    with pytest.raises(asyncio.CancelledError):
        await cwr.request(URL)


@pytest.mark.asyncio
async def test_price_and_closed_changes_still_update_existing_article():
    client = ApiClient()
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client)
    previous = await cwr.get()
    client.articles[0]["deal"]["isClosed"] = True
    client.articles[0]["deal"]["price"]["number"] = 3900
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {"test": previous}
    result = await manager._crawling("test", cwr)
    assert len(result["update"]) == 1
    assert result["update"][0]["is_end"] is True
    assert result["update"][0]["extra"]["price"] == "3,900원"
    assert not result["new"] and not result["remove"]


@pytest.mark.asyncio
async def test_overlapping_filters_merge_the_same_post():
    client = ApiClient()
    cwr = ArcaLiveCrawlerV2("test", [URL, URL + "?category=pc"], client=client)
    data = await cwr.get()
    assert list(data) == [100]
    assert data[100]["url"] == URL + "/100"
    assert sum("/info/" in url for url, _ in client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("categories", [[{}], [None, {"id": "pc", "displayName": None}], "bad", None])
async def test_invalid_optional_category_metadata_does_not_poison_cache(categories):
    client = ApiClient()
    original_get = client.get

    async def get(url, **kwargs):
        if "/info/" in url:
            return response({"channel": {**CHANNEL, "categoryData": categories}})
        return await original_get(url, **kwargs)

    client.get = get
    cwr = ArcaLiveCrawlerV2("test", [URL], client=client)
    for _ in range(2):
        assert (await cwr.get())[100]["category"] == "PC/하드웨어"
    assert cwr._channels["hotdeal"]["categoryData"] == []
