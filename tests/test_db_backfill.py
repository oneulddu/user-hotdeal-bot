import asyncio

import pytest

from src import crawler
from src.db import ArticleRepository, get_async_engine, get_async_session, init_db
from src.main import BotManager


def make_article(article_id: int, crawler_name: str = "dummy") -> crawler.BaseArticle:
    return crawler.BaseArticle(
        article_id=article_id,
        title=f"Article {article_id}",
        category="category",
        site_name="site",
        board_name="board",
        writer_name="writer",
        crawler_name=crawler_name,
        url=f"https://example.com/{article_id}",
        is_end=False,
        extra={},
    )


@pytest.mark.asyncio
async def test_db_backfill_saves_initialized_article_cache():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    manager = BotManager()
    manager.db_engine = engine
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                1: make_article(1),
                2: make_article(2),
            }
        )
    }

    await manager._backfill_db_from_cache()

    async with get_async_session(engine) as session:
        articles, total = await ArticleRepository(session).list_articles(limit=10)

    await engine.dispose()

    assert total == 2
    assert {article.article_id for article in articles} == {1, 2}


@pytest.mark.asyncio
async def test_run_backfills_cache_without_creating_send_delta(monkeypatch):
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    manager = BotManager()
    manager.db_engine = engine
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                1: make_article(1),
            }
        )
    }
    sent_results = []

    async def crawling():
        return {"new": [], "update": [], "remove": []}

    async def send(result):
        sent_results.append(result)

    monkeypatch.setattr(manager, "crawling", crawling)
    monkeypatch.setattr(manager, "send", send)

    await manager._run()

    async with get_async_session(engine) as session:
        articles, total = await ArticleRepository(session).list_articles(limit=10)

    await engine.dispose()

    assert total == 1
    assert articles[0].article_id == 1
    assert sent_results == [{"new": [], "update": [], "remove": []}]


@pytest.mark.asyncio
async def test_db_backfill_only_saves_new_cache_keys():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    manager = BotManager()
    manager.db_engine = engine
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                1: make_article(1),
            }
        )
    }

    await manager._backfill_db_from_cache()
    manager.article_cache["dummy"][2] = make_article(2)
    await manager._backfill_db_from_cache()

    async with get_async_session(engine) as session:
        articles, total = await ArticleRepository(session).list_articles(limit=10)

    await engine.dispose()

    assert total == 2
    assert manager._db_backfilled_article_keys == {("dummy", 1), ("dummy", 2)}
    assert {article.article_id for article in articles} == {1, 2}


@pytest.mark.asyncio
async def test_crawling_keeps_successful_results_when_one_crawler_fails(monkeypatch):
    manager = BotManager()
    manager.crawlers = {"ok": object(), "broken": object()}

    async def crawling(name, _crawler):
        if name == "broken":
            raise RuntimeError("boom")
        return {
            "new": [make_article(1, crawler_name=name)],
            "update": [make_article(2, crawler_name=name)],
            "remove": [make_article(3, crawler_name=name)],
        }

    monkeypatch.setattr(manager, "_crawling", crawling)

    result = await manager.crawling()

    assert [article["article_id"] for article in result["new"]] == [1]
    assert [article["article_id"] for article in result["update"]] == [2]
    assert [article["article_id"] for article in result["remove"]] == [3]


@pytest.mark.asyncio
async def test_run_skips_overlapping_cycle(monkeypatch):
    manager = BotManager()
    started = asyncio.Event()
    release = asyncio.Event()
    run_count = 0

    async def run_locked():
        nonlocal run_count
        run_count += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(manager, "_run_locked", run_locked)

    first_task = asyncio.create_task(manager._run())
    await started.wait()
    await manager._run()
    release.set()
    await first_task

    assert run_count == 1
