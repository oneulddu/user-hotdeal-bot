import asyncio
import json
from unittest.mock import AsyncMock

import aiohttp
import pytest
import yaml

from src import bot, crawler
from src.main import BotManager
from tests.test_main import make_article


class LifecycleBot(bot.DummyBot):
    def __init__(self, name):
        self.operations = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        super().__init__(name)

    async def _send(self, article):
        self.started.set()
        await self.release.wait()
        self.operations.append(("send", article["article_id"]))

    async def _edit(self, article):
        self.operations.append(("edit", article["article_id"]))


def make_manager(bot_instance):
    manager = BotManager()
    manager.bots = {"dummy": bot_instance}
    manager.crawlers = {}
    manager.article_cache = {"dummy": crawler.ArticleCollection({1: make_article(1)})}
    return manager


@pytest.mark.asyncio
async def test_repeated_reload_preserves_in_flight_and_pending_notifications(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "LifecycleBot", LifecycleBot, raising=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"crawlers": {}, "bots": {"dummy": {"bot_name": "LifecycleBot", "kwargs": {}}}})
    )
    instance = LifecycleBot("dummy")
    manager = make_manager(instance)
    try:
        await instance.send(make_article(1))
        await asyncio.wait_for(instance.started.wait(), timeout=2)
        await instance.edit(make_article(1))
        for _ in range(2):
            await manager.reload()
            saved = json.loads((tmp_path / "dump.json").read_text())
            assert [job[0] for job in saved["bot"]["dummy"]["queue"]] == ["send", "edit"]
            assert manager.bots["dummy"] is instance
        instance.release.set()
        await asyncio.wait_for(instance.queue.join(), timeout=2)
        assert instance.operations == [("send", 1), ("edit", 1)]
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_dump_failure_preserves_queue_and_restarts_consumer(tmp_path):
    instance = LifecycleBot("dummy")
    manager = make_manager(instance)
    manager.article_cache["dummy"][1]["extra"] = {"invalid": object()}
    dump_file = tmp_path / "dump.json"
    dump_file.write_text("previous dump")
    try:
        await instance.send(make_article(1))
        await asyncio.wait_for(instance.started.wait(), timeout=2)
        await instance.edit(make_article(1))
        with pytest.raises(TypeError):
            await manager.dump(str(dump_file))
        assert dump_file.read_text() == "previous dump"
        instance.release.set()
        await asyncio.wait_for(instance.queue.join(), timeout=2)
        assert instance.operations == [("send", 1), ("edit", 1)]
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_close_finishes_crawling_before_closing_sessions_and_dumping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.main.close_db", AsyncMock())
    instance = LifecycleBot("dummy")
    manager = make_manager(instance)
    manager.article_cache["dummy"][2] = make_article(2)
    started = asyncio.Event()
    release = asyncio.Event()

    class DelayedCrawler:
        calls = 0
        closed = False

        async def get(self):
            self.calls += 1
            started.set()
            await release.wait()
            assert not self.session.closed
            return crawler.ArticleCollection({1: make_article(1), 3: make_article(3)})

        async def close(self):
            self.closed = True

    async with aiohttp.ClientSession() as session:
        manager.session = session
        instance_crawler = DelayedCrawler()
        instance_crawler.session = session
        manager.crawlers = {"dummy": instance_crawler}
        cycle = manager._schedule_crawling_task(asyncio.get_running_loop())
        await asyncio.wait_for(started.wait(), timeout=2)
        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert not closing.done()
        assert not session.closed
        release.set()
        await asyncio.wait_for(asyncio.gather(cycle, closing), timeout=2)
        assert session.closed
        assert instance_crawler.closed
        saved = json.loads((tmp_path / "dump.json").read_text())
        assert set(saved["crawler"]["dummy"]) == {"1", "3"}
        assert saved["article_tombstones"]["dummy"] == [2]
        assert [(action, article["article_id"]) for action, article in saved["bot"]["dummy"]["queue"]] == [
            ("send", 3),
            ("delete", 2),
        ]
        await manager._run()
        await manager.close()
        assert instance_crawler.calls == 1


@pytest.mark.asyncio
async def test_reload_waits_for_active_cycle(monkeypatch):
    manager = make_manager(None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_cycle():
        started.set()
        await release.wait()

    dump = AsyncMock()
    load = AsyncMock()
    monkeypatch.setattr(manager, "_run_locked", run_cycle)
    monkeypatch.setattr(manager, "dump", dump)
    monkeypatch.setattr(manager, "load_config", load)
    cycle = asyncio.create_task(manager._run())
    await asyncio.wait_for(started.wait(), timeout=2)
    reload_task = asyncio.create_task(manager.reload())
    await asyncio.sleep(0)
    dump.assert_not_awaited()
    load.assert_not_awaited()
    release.set()
    await asyncio.wait_for(asyncio.gather(cycle, reload_task), timeout=2)
    dump.assert_awaited_once()
    load.assert_awaited_once()
