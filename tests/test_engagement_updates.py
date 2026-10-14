"""Engagement changes must survive quiet polls, while price/status updates stay immediate."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.crawler import ArticleCollection
from src.db import ArticleRepository, get_async_engine, get_async_session, init_db
from src.main import BotManager, PersistenceManager
from tests.test_main import make_article


def article(article_id=1, *, comments=0, recommend="0", best=False):
    return make_article(article_id, extra={"comment_count": comments, "recommend": recommend, "is_best": best})


@pytest.fixture
def manager_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("src.main.time", SimpleNamespace(monotonic=lambda: clock[0]))
    return BotManager(), clock


async def poll(manager, clock, seconds, *articles):
    clock[0] = seconds
    cwr = SimpleNamespace(
        get=AsyncMock(return_value=ArticleCollection({a["article_id"]: copy.deepcopy(a) for a in articles}))
    )
    return await manager._crawling("dummy", cwr)


@pytest.mark.asyncio
async def test_count_changes_survive_quiet_polls_and_update_db_at_five_minutes(manager_clock):
    manager, clock = manager_clock
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    manager.db_engine = engine
    try:
        assert await poll(manager, clock, 0, article()) == {"new": [], "update": [], "remove": []}
        await manager._backfill_db_from_cache()
        changed = article(comments=17, recommend="8")
        for seconds in (60, 120, 299):
            result = await poll(manager, clock, seconds, changed)
            assert not result["update"]
            assert manager.article_cache["dummy"][1]["extra"]["comment_count"] == 17
        result = await poll(manager, clock, 300, changed)
        assert result == {"new": [], "update": [changed], "remove": []}
        await manager._save_to_db(result)
        async with get_async_session(engine) as session:
            rows, total = await ArticleRepository(session).list_articles(limit=10)
            assert total == 1 and rows[0].extra == changed["extra"]
        assert not (await poll(manager, clock, 600, changed))["update"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_notification_starts_its_own_cooldown(manager_clock):
    manager, clock = manager_clock
    await poll(manager, clock, 0, article())
    result = await poll(manager, clock, 60, article(), article(2))
    assert [a["article_id"] for a in result["new"]] == [2]
    for seconds in (120, 300, 359):
        assert not (await poll(manager, clock, seconds, article(), article(2, comments=1)))["update"]
    assert [
        a["article_id"] for a in (await poll(manager, clock, 360, article(), article(2, comments=1)))["update"]
    ] == [2]


@pytest.mark.asyncio
async def test_best_transitions_and_regular_changes_bypass_cooldown(manager_clock):
    manager, clock = manager_clock
    await poll(manager, clock, 0, article())
    assert not (await poll(manager, clock, 60, article(comments=1)))["update"]
    for seconds, best in ((120, True), (180, False)):
        result = await poll(manager, clock, seconds, article(comments=1, best=best))
        assert len(result["update"]) == 1 and not result["new"]
    changed = article(comments=2)
    changed["extra"]["price"] = "900원"
    assert len((await poll(manager, clock, 240, changed))["update"]) == 1
    changed["is_end"] = True
    assert len((await poll(manager, clock, 250, changed))["update"]) == 1
    assert not (await poll(manager, clock, 600, changed))["update"]


@pytest.mark.asyncio
async def test_failed_snapshot_keeps_pending_counts_and_removed_posts_are_pruned(manager_clock):
    manager, clock = manager_clock
    await poll(manager, clock, 0, article(), article(2))
    await poll(manager, clock, 60, article(comments=1), article(2))
    assert await poll(manager, clock, 300) == {"new": [], "update": [], "remove": []}
    assert len((await poll(manager, clock, 360, article(comments=1), article(2)))["update"]) == 1
    await poll(manager, clock, 420, article(2))
    assert set(manager._engagement_state["dummy"]) == {2}


@pytest.mark.asyncio
async def test_restart_refreshes_counts_once_even_if_dump_is_newer_than_message(manager_clock, tmp_path):
    manager, clock = manager_clock
    await poll(manager, clock, 0, article())
    await poll(manager, clock, 60, article(comments=1))  # Deferred; message still shows zero.
    path = tmp_path / "dump.json"
    await manager.persistence.dump_data(manager.article_cache, {}, str(path))
    restored = BotManager()
    restored.article_cache = await PersistenceManager().load_data(str(path), {"dummy": object()}, {})
    for seconds in (100, 399):
        assert not (await poll(restored, clock, seconds, article(comments=1)))["update"]
    assert len((await poll(restored, clock, 400, article(comments=1)))["update"]) == 1
    assert not (await poll(restored, clock, 700, article(comments=1)))["update"]


@pytest.mark.asyncio
async def test_existing_text_alert_gets_engagement_on_upgrade_without_resending(manager_clock):
    manager, clock = manager_clock
    manager.article_cache = {"dummy": ArticleCollection({1: make_article()})}
    result = await poll(manager, clock, 0, article(comments=17, recommend="8", best=True))
    assert len(result["update"]) == 1
    assert not result["new"]
    assert not (await poll(manager, clock, 300, article(comments=17, recommend="8", best=True)))["update"]


@pytest.mark.asyncio
async def test_other_crawler_recommendation_counts_keep_existing_behavior(manager_clock):
    manager, clock = manager_clock
    await poll(manager, clock, 0, make_article(extra={"recommend": "0"}))
    result = await poll(manager, clock, 300, make_article(extra={"recommend": "8"}))
    assert not result["update"]
    assert not manager._engagement_state["dummy"]
