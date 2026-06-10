from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src import crawler
from src.api.routes.feed import FEED_CACHE_MAX_SIZE, _feed_cache, _fill_entry_common, _set_cached_feed, get_rss_feed
from src.datetime_utils import as_utc
from src.db import Article, ArticleRepository, get_async_engine, get_async_session, init_db


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

        async def list_articles(self, **_kwargs):
            self.calls += 1
            return (
                [
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
                ],
                1,
            )

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
