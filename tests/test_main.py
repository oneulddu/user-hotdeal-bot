import asyncio
import json
import tomllib

import aiohttp
import pytest

from src import crawler
from src.main import BotManager, PersistenceManager


class CloseTrackingCrawler(crawler.BaseCrawler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False

    async def parsing(self, html: str) -> dict[int, crawler.BaseArticle]:
        return {}

    async def close(self):
        self.closed = True
        await super().close()


def test_version():
    from src.api.main import VERSION
    from src.main import __version__
    from src.version import get_version

    with open("pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    project_version = pyproject["project"]["version"]
    assert project_version == __version__
    assert project_version == VERSION
    assert project_version == get_version()


@pytest.mark.asyncio
async def test_load_data_ignores_dump_with_missing_required_keys(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text(json.dumps({"version": "2.2.1"}), encoding="utf-8")
    crawlers = {"dummy": object()}

    article_cache = await PersistenceManager().load_data(str(dump_file), crawlers, {})

    assert set(article_cache) == {"dummy"}
    assert isinstance(article_cache["dummy"], crawler.ArticleCollection)
    assert not article_cache["dummy"]


@pytest.mark.asyncio
async def test_dump_data_replaces_file_atomically_without_temp_leftovers(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text("old data", encoding="utf-8")
    article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                1: crawler.BaseArticle(
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
                )
            }
        )
    }

    await PersistenceManager().dump_data(article_cache, {}, str(dump_file))

    data = json.loads(dump_file.read_text(encoding="utf-8"))
    assert data["crawler"]["dummy"]["1"]["title"] == "Article 1"
    assert not dump_file.with_suffix(".json.tmp").exists()


@pytest.mark.asyncio
async def test_dump_data_keeps_existing_file_when_serialization_fails(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text("old data", encoding="utf-8")

    class BrokenBot:
        async def to_dict(self):
            return {"bad": object()}

    with pytest.raises(TypeError):
        await PersistenceManager().dump_data({}, {"broken": BrokenBot()}, str(dump_file))

    assert dump_file.read_text(encoding="utf-8") == "old data"
    assert not dump_file.with_suffix(".json.tmp").exists()


@pytest.mark.asyncio
async def test_load_config_handles_missing_required_top_level_keys(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("bots: {}\n", encoding="utf-8")
    manager = BotManager()
    manager.crawlers = {}
    manager.bots = {}

    await manager.load_config(str(config_file))

    assert manager.crawlers == {}
    assert manager.bots == {}


@pytest.mark.asyncio
async def test_init_bots_skips_disabled_before_class_lookup(caplog):
    manager = BotManager()
    manager.bots = {}

    with caplog.at_level("WARNING"):
        await manager.init_bots(
            {
                "disabled": {
                    "bot_name": "MissingBotClass",
                    "description": "disabled bot",
                    "kwargs": {},
                    "enabled": False,
                }
            }
        )

    assert manager.bots == {}
    assert "Unknown bot class" not in caplog.text


@pytest.mark.asyncio
async def test_init_crawlers_rebuilds_when_cookie_env_value_changes(monkeypatch):
    manager = BotManager()
    manager.crawlers = {}

    crawler_config = {
        "dummy": {
            "url_list": ["https://example.com"],
            "crawler_name": "DummyCrawler",
            "description": "dummy crawler",
            "enabled": True,
            "cookie_env": "HOTDEAL_TEST_COOKIE",
        }
    }

    async with aiohttp.ClientSession() as session:
        manager.session = session

        monkeypatch.setenv("HOTDEAL_TEST_COOKIE", "foo=old")
        await manager.init_crawlers(crawler_config)
        first_crawler = manager.crawlers["dummy"]

        monkeypatch.setenv("HOTDEAL_TEST_COOKIE", "foo=new")
        await manager.init_crawlers(crawler_config)
        second_crawler = manager.crawlers["dummy"]

    assert second_crawler is not first_crawler
    assert second_crawler.request_cookies == {"foo": "new"}


@pytest.mark.asyncio
async def test_init_crawlers_closes_replaced_and_removed_crawlers(monkeypatch):
    manager = BotManager()
    manager.crawlers = {}
    monkeypatch.setattr(crawler, "CloseTrackingCrawler", CloseTrackingCrawler, raising=False)

    crawler_config = {
        "tracked": {
            "url_list": ["https://example.com"],
            "crawler_name": "CloseTrackingCrawler",
            "description": "tracked crawler",
            "enabled": True,
        }
    }

    async with aiohttp.ClientSession() as session:
        manager.session = session

        await manager.init_crawlers(crawler_config)
        first_crawler = manager.crawlers["tracked"]

        await manager.init_crawlers(
            {**crawler_config, "tracked": {**crawler_config["tracked"], "proxy": "http://proxy"}}
        )
        second_crawler = manager.crawlers["tracked"]

        await manager.init_crawlers({**crawler_config, "tracked": {**crawler_config["tracked"], "enabled": False}})

        assert first_crawler.closed is True
        assert second_crawler.closed is True
        assert session.closed is False


@pytest.mark.asyncio
async def test_close_closes_manager_owned_shared_session(monkeypatch):
    manager = BotManager()
    dumped = False
    db_closed = False

    async def dump():
        nonlocal dumped
        dumped = True

    async def close_db():
        nonlocal db_closed
        db_closed = True

    monkeypatch.setattr("src.main.close_db", close_db)
    manager.dump = dump
    manager.bots = {}

    session = aiohttp.ClientSession()
    manager.session = session
    tracking_crawler = CloseTrackingCrawler("tracked", ["https://example.com"], session=session)
    manager.crawlers = {"tracked": tracking_crawler}

    await manager.close()

    assert tracking_crawler.closed is True
    assert session.closed is True
    assert dumped is True
    assert db_closed is True


@pytest.mark.asyncio
async def test_schedule_crawling_task_keeps_reference_until_done():
    manager = BotManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_locked():
        started.set()
        await release.wait()

    manager._run_locked = run_locked

    task = manager._schedule_crawling_task(asyncio.get_running_loop())
    await started.wait()

    assert task in manager._bg_tasks

    release.set()
    await task
    await asyncio.sleep(0)

    assert task not in manager._bg_tasks
