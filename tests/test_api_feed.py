import asyncio
from datetime import datetime, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import event

from src import crawler
from src.api.routes import feed
from src.api.routes.feed import (
    FEED_CACHE_MAX_SIZE,
    _feed_cache,
    _fill_entry_common,
    _set_cached_feed,
    get_atom_feed,
    get_rss_feed,
)
from src.datetime_utils import as_utc
from src.db import Article, ArticleRepository, get_async_engine, get_async_session, init_db


@pytest.fixture(autouse=True)
def clear_feed_cache():
    _feed_cache.clear()
    yield
    _feed_cache.clear()


class FakeFeedEntry:
    def __init__(self):
        self.values = {}

    def id(self, value):
        self.values["id"] = value

    def title(self, value):
        self.values["title"] = value

    def link(self, href):
        self.values["link"] = href

    def author(self, name):
        self.values["author"] = name

    def published(self, value):
        self.values["published"] = value

    def updated(self, value):
        self.values["updated"] = value

    def category(self, term):
        self.values["category"] = term


def test_fill_entry_common_converts_aware_datetime_to_utc():
    article = Article(
        id="01HX0000000000000000000000",
        article_id=1,
        title="Article 1",
        category="category",
        site_name="site",
        board_name="board",
        writer_name="writer",
        crawler_name="dummy",
        url="https://example.com/1",
        is_end=False,
        extra={},
        created_at=datetime(2026, 6, 8, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        updated_at=datetime(2026, 6, 8, 13, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    entry = FakeFeedEntry()

    _fill_entry_common(entry, article)

    assert entry.values["published"].isoformat() == "2026-06-08T03:00:00+00:00"
    assert entry.values["updated"].isoformat() == "2026-06-08T04:00:00+00:00"


def make_article(article_id: int) -> crawler.BaseArticle:
    return crawler.BaseArticle(
        article_id=article_id,
        title=f"Article {article_id}",
        category="category",
        site_name="site",
        board_name="board",
        writer_name="writer",
        crawler_name="dummy",
        url=f"https://example.com/{article_id}",
        is_end=False,
        extra={},
    )


@pytest.mark.asyncio
async def test_feed_cache_reuses_xml_for_same_parameters():
    _feed_cache.clear()

    class CountingRepo:
        def __init__(self):
            self.calls = 0

        async def list_feed_articles(self, **_kwargs):
            self.calls += 1
            return [
                Article(
                    id="01HX0000000000000000000000",
                    article_id=1,
                    title="Article 1",
                    category="category",
                    site_name="site",
                    board_name="board",
                    writer_name="writer",
                    crawler_name="dummy",
                    url="https://example.com/1",
                    is_end=False,
                    extra={},
                    created_at=datetime(2026, 6, 8, 12, 0),
                    updated_at=datetime(2026, 6, 8, 12, 0),
                )
            ]

    repo = CountingRepo()

    first = await get_rss_feed(None, repo, crawler="dummy", site=None, limit=50)
    second = await get_rss_feed(None, repo, crawler="dummy", site=None, limit=50)

    assert repo.calls == 1
    assert first.body == second.body
    assert first.headers["Cache-Control"] == "public, max-age=60"


def test_feed_cache_prunes_expired_entries_and_caps_size():
    _feed_cache.clear()
    _feed_cache.update({("rss", f"expired-{i}", None, 50): (0, b"expired") for i in range(3)})

    for i in range(FEED_CACHE_MAX_SIZE + 1):
        _set_cached_feed(("rss", f"crawler-{i}", None, 50), b"feed")

    assert len(_feed_cache) <= FEED_CACHE_MAX_SIZE
    assert all("expired" not in (cache_key[1] or "") for cache_key in _feed_cache)


@pytest.mark.asyncio
async def test_db_article_timestamps_are_stored_as_utc_naive_for_feed():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    before = datetime.now(timezone.utc)

    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        await repo.bulk_upsert([dict(make_article(1))])
        article = await repo.get_by_crawler_and_article_id("dummy", 1)

    after = datetime.now(timezone.utc)
    await engine.dispose()

    assert article is not None
    assert article.created_at.tzinfo is None
    assert before <= as_utc(article.created_at) <= after


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint, item_path", [(get_rss_feed, "./channel/item"), (get_atom_feed, "{*}entry")])
async def test_feed_uses_one_query_and_filters_deleted_ended_and_other_sites(endpoint, item_path):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    try:
        async with get_async_session(engine) as session:
            repo = ArticleRepository(session)
            for i in range(1, 7):
                await repo.create(
                    {
                        **make_article(i),
                        "id": f"{i:026d}",
                        "is_end": i == 3,
                        "deleted_at": datetime(2026, 1, 1) if i == 4 else None,
                        "crawler_name": "other" if i == 5 else "dummy",
                        "site_name": "other" if i == 6 else "site",
                    }
                )
        statements = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        async with get_async_session(engine) as session:
            response = await endpoint(None, ArticleRepository(session), crawler="dummy", site="site", limit=1)
        items = ElementTree.fromstring(response.body).findall(item_path)
        assert len(items) == 1
        assert items[0].find("{*}title").text == "Article 2"
        assert len(statements) == 1
        assert "count(" not in statements[0].lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_rss_feed, get_atom_feed])
async def test_concurrent_identical_feed_requests_share_generation(endpoint):
    class CountingRepo:
        calls = 0

        async def list_feed_articles(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return []

    repo = CountingRepo()
    responses = await asyncio.gather(*(endpoint(None, repo, crawler=None, site=None, limit=50) for _ in range(20)))

    assert repo.calls == 1
    assert len({response.body for response in responses}) == 1
    assert len(feed._feed_locks) == 0


@pytest.mark.asyncio
async def test_distinct_feed_keys_can_generate_concurrently():
    started = asyncio.Event()

    class CountingRepo:
        calls = 0

        async def list_feed_articles(self, **_kwargs):
            self.calls += 1
            if self.calls == 5:
                started.set()
            await started.wait()
            return []

    repo = CountingRepo()
    await asyncio.wait_for(
        asyncio.gather(
            get_rss_feed(None, repo, crawler=None, site=None, limit=50),
            get_atom_feed(None, repo, crawler=None, site=None, limit=50),
            get_rss_feed(None, repo, crawler="dummy", site=None, limit=50),
            get_rss_feed(None, repo, crawler=None, site="site", limit=50),
            get_rss_feed(None, repo, crawler=None, site=None, limit=10),
        ),
        timeout=2,
    )
    assert repo.calls == 5
    assert len(_feed_cache) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_leader", [False, True])
async def test_failed_or_cancelled_feed_generation_allows_waiter_to_retry(cancel_leader):
    started = asyncio.Event()
    release = asyncio.Event()

    class FailingRepo:
        calls = 0

        async def list_feed_articles(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
                raise RuntimeError("query failed")
            return []

    repo = FailingRepo()
    first = asyncio.create_task(get_rss_feed(None, repo, crawler=None, site=None, limit=50))
    await started.wait()
    second = asyncio.create_task(get_rss_feed(None, repo, crawler=None, site=None, limit=50))
    await asyncio.sleep(0)
    if cancel_leader:
        first.cancel()
    else:
        release.set()
    with pytest.raises(asyncio.CancelledError if cancel_leader else RuntimeError):
        await first
    response = await asyncio.wait_for(second, timeout=2)
    assert response.status_code == 200
    assert repo.calls == 2
    assert len(feed._feed_locks) == 0


@pytest.mark.asyncio
async def test_feed_is_rebuilt_after_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(feed.time, "monotonic", lambda: now[0])

    class CountingRepo:
        calls = 0

        async def list_feed_articles(self, **_kwargs):
            self.calls += 1
            return []

    repo = CountingRepo()
    await get_rss_feed(None, repo, crawler=None, site=None, limit=50)
    now[0] += 59
    await get_rss_feed(None, repo, crawler=None, site=None, limit=50)
    assert repo.calls == 1
    now[0] += 1
    await get_rss_feed(None, repo, crawler=None, site=None, limit=50)
    assert repo.calls == 2


@pytest.mark.asyncio
async def test_cached_feed_still_requires_auth_and_applies_rate_limit():
    from src.api.deps import get_db_session
    from src.api.main import app
    from src.db import ApiKeyRepository

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async def get_test_session():
        async with get_async_session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_session
    try:
        async with get_async_session(engine) as session:
            await ApiKeyRepository(session).create("valid-key", "feed-test", rate_limit_per_minute=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/feed/rss.xml", headers={"X-API-Key": "valid-key"})).status_code == 200
            assert _feed_cache
            assert (await client.get("/feed/rss.xml", headers={"X-API-Key": "valid-key"})).status_code == 429
            assert (await client.get("/feed/rss.xml", headers={"X-API-Key": "invalid"})).status_code == 401
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await engine.dispose()
