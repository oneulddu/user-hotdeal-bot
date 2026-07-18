import asyncio
import json
import tomllib

import aiohttp
import pytest

from src import crawler
from src.bot import DummyBot
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


class BrokenCloseCrawler(CloseTrackingCrawler):
    async def close(self):
        self.closed = True
        raise RuntimeError("close failed")


class StaticCrawler:
    def __init__(self, articles: crawler.ArticleCollection):
        self.articles = articles

    async def get(self) -> crawler.ArticleCollection:
        return self.articles


def make_article(article_id: int = 1, extra: dict | None = None) -> crawler.BaseArticle:
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
        extra=extra or {},
    )


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
    assert data["article_tombstones"] == {}
    assert not dump_file.with_suffix(".json.tmp").exists()


@pytest.mark.asyncio
async def test_article_tombstones_survive_dump_and_load(tmp_path):
    dump_file = tmp_path / "dump.json"
    persistence = PersistenceManager()

    await persistence.dump_data(
        {"dummy": crawler.ArticleCollection({1: make_article(1)})},
        {},
        str(dump_file),
        {"dummy": {2}},
    )

    loaded_persistence = PersistenceManager()
    await loaded_persistence.load_data(str(dump_file), {"dummy": object()}, {})

    assert loaded_persistence.article_tombstones == {"dummy": {2}}


@pytest.mark.asyncio
async def test_load_data_without_duplicate_tracking_uses_empty_tombstones(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text(
        json.dumps(
            {
                "version": "2.2.1",
                "crawler": {"dummy": {"3": make_article(3)}},
                "bot": {},
            }
        ),
        encoding="utf-8",
    )
    persistence = PersistenceManager()

    article_cache = await persistence.load_data(str(dump_file), {"dummy": object()}, {})

    assert 3 in article_cache["dummy"]
    assert persistence.article_tombstones == {}


@pytest.mark.asyncio
async def test_load_data_migrates_missing_legacy_high_water_mark_to_tombstone(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text(
        json.dumps(
            {
                "version": "2.2.1",
                "crawler": {"dummy": {"100": make_article(100)}},
                "bot": {},
                "article_high_water_marks": {"dummy": 102},
            }
        ),
        encoding="utf-8",
    )
    persistence = PersistenceManager()

    article_cache = await persistence.load_data(str(dump_file), {"dummy": object()}, {})

    assert persistence.article_tombstones == {"dummy": {102}}

    manager = BotManager()
    manager.bots = {}
    manager.article_cache = article_cache
    manager.article_tombstones = persistence.article_tombstones
    result = await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({100: make_article(100), 101: make_article(101)})),
    )

    assert [article["article_id"] for article in result["new"]] == [101]


@pytest.mark.asyncio
async def test_load_data_uses_legacy_mark_for_malformed_crawler_tombstones(tmp_path):
    dump_file = tmp_path / "dump.json"
    dump_file.write_text(
        json.dumps(
            {
                "version": "2.2.1",
                "crawler": {"dummy": {"100": make_article(100)}},
                "bot": {},
                "article_tombstones": {"dummy": [False]},
                "article_high_water_marks": {"dummy": 102},
            }
        ),
        encoding="utf-8",
    )
    persistence = PersistenceManager()

    await persistence.load_data(str(dump_file), {"dummy": object()}, {})

    assert persistence.article_tombstones == {"dummy": {102}}


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
async def test_close_continues_when_crawler_close_fails(monkeypatch):
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
    broken_crawler = BrokenCloseCrawler("broken", ["https://example.com"], session=session)
    manager.crawlers = {"broken": broken_crawler}

    await manager.close()

    assert broken_crawler.closed is True
    assert session.closed is True
    assert dumped is True
    assert db_closed is True


@pytest.mark.asyncio
async def test_deserialize_bots_logs_loaded_message_count(caplog):
    bot = DummyBot("dummy")

    try:
        with caplog.at_level("INFO", logger="PersistenceManager"):
            await PersistenceManager().deserialize_bots(
                {
                    "dummy": {
                        "queue": [],
                        "cache": {
                            "a": {"1": "message 1", "2": "message 2"},
                            "long-crawler-name": {"3": "message 3"},
                        },
                    }
                },
                {"dummy": bot},
            )
    finally:
        await bot.close()

    assert "dummy: 3 message(s) loaded" in caplog.text


@pytest.mark.asyncio
async def test_crawling_detects_price_added_to_empty_extra():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                1: make_article(1, extra={}),
            }
        )
    }

    result = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    1: make_article(1, extra={"price": "10,000원"}),
                }
            )
        ),
    )

    assert [article["article_id"] for article in result["update"]] == [1]


@pytest.mark.asyncio
async def test_crawling_does_not_resend_latest_article_after_transient_disappearance():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                101: make_article(101),
            }
        )
    }

    disappeared = await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({100: make_article(100)})),
    )
    reappeared = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    100: make_article(100),
                    101: make_article(101),
                }
            )
        ),
    )
    next_article = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    100: make_article(100),
                    101: make_article(101),
                    102: make_article(102),
                }
            )
        ),
    )

    assert [article["article_id"] for article in disappeared["remove"]] == [101]
    assert reappeared["new"] == []
    assert [article["article_id"] for article in next_article["new"]] == [102]
    assert 101 in manager.article_cache["dummy"]
    assert manager.article_tombstones["dummy"] == {101}


@pytest.mark.asyncio
async def test_crawling_notifies_new_article_below_previous_maximum():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                1000: make_article(1000),
            }
        )
    }

    await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({100: make_article(100)})),
    )
    result = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    100: make_article(100),
                    101: make_article(101),
                }
            )
        ),
    )

    assert [article["article_id"] for article in result["new"]] == [101]
    assert manager.article_tombstones["dummy"] == {1000}


@pytest.mark.asyncio
async def test_crawling_does_not_notify_unseen_older_article():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                101: make_article(101),
            }
        )
    }

    result = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    99: make_article(99),
                    100: make_article(100),
                    101: make_article(101),
                }
            )
        ),
    )

    assert result["new"] == []
    assert 99 in manager.article_cache["dummy"]


@pytest.mark.asyncio
async def test_crawling_does_not_notify_older_article_below_reappeared_latest():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                102: make_article(102),
            }
        )
    }

    await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({100: make_article(100)})),
    )
    result = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    100: make_article(100),
                    101: make_article(101),
                    102: make_article(102),
                }
            )
        ),
    )

    assert result["new"] == []
    assert manager.article_tombstones["dummy"] == {102}


@pytest.mark.asyncio
async def test_crawling_preserves_previous_max_when_cache_has_no_overlap():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                101: make_article(101),
            }
        )
    }

    older_only = await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({99: make_article(99)})),
    )
    next_article = await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({99: make_article(99), 102: make_article(102)})),
    )

    assert older_only["new"] == []
    assert [article["article_id"] for article in next_article["new"]] == [102]


@pytest.mark.asyncio
async def test_crawling_prunes_old_tombstones_and_notifies_after_cache_empties():
    manager = BotManager()
    manager.bots = {}
    manager.article_cache = {
        "dummy": crawler.ArticleCollection(
            {
                100: make_article(100),
                101: make_article(101),
            }
        )
    }

    await manager._crawling(
        "dummy",
        StaticCrawler(crawler.ArticleCollection({100: make_article(100)})),
    )
    result = await manager._crawling(
        "dummy",
        StaticCrawler(
            crawler.ArticleCollection(
                {
                    102: make_article(102),
                    103: make_article(103),
                }
            )
        ),
    )

    assert [article["article_id"] for article in result["new"]] == [102, 103]
    assert manager.article_tombstones["dummy"] == set()


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
