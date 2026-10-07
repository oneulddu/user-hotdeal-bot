"""Real signal/process coverage, including SQLite worker cleanup and disk failures."""

import errno
import json
import os
import selectors
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from src.crawler import ArticleCollection
from src.main import BotManager, PersistenceManager
from tests.test_main import make_article


@pytest.mark.asyncio
async def test_configured_path_used_for_load_reload_and_close(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DUMP_FILE_PATH", str(tmp_path / "state" / "dump.json"))
    monkeypatch.setattr("src.main.close_db", AsyncMock())
    manager = BotManager()
    manager.crawlers = {"dummy": object()}
    manager.load_config = AsyncMock()
    original = {"dummy": ArticleCollection({7: make_article(7)})}
    await manager.persistence.dump_data(original, {}, manager.dump_file_path)
    await manager.load()
    assert set(manager.article_cache["dummy"]) == {7}
    manager.article_cache["dummy"][8] = make_article(8)
    await manager.reload()
    assert "8" in json.loads(Path(manager.dump_file_path).read_text())["crawler"]["dummy"]
    manager.crawlers = {}
    manager.article_cache["dummy"][9] = make_article(9)
    await manager.close()
    assert "9" in json.loads(Path(manager.dump_file_path).read_text())["crawler"]["dummy"]
    assert not Path("dump.json").exists()


@pytest.mark.asyncio
async def test_explicit_path_override_and_no_automatic_legacy_restore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DUMP_FILE_PATH", "state/new.json")
    old = {"dummy": ArticleCollection({1: make_article(1)})}
    await PersistenceManager().dump_data(old, {}, "dump.json")
    manager = BotManager(dump_file_path="state/selected.json")
    manager.crawlers = {"dummy": object()}
    manager.load_config = AsyncMock()
    await manager.load()
    assert not manager.article_cache["dummy"]
    await manager.load(dump_file_path="dump.json")
    assert set(manager.article_cache["dummy"]) == {1}
    await manager.dump("manual.json")
    assert Path("manual.json").exists()
    assert not Path("state/selected.json").exists()
    await manager.dump()
    assert Path("state/selected.json").exists()


@pytest.mark.asyncio
async def test_failed_replace_preserves_snapshot_and_closes_database(tmp_path, monkeypatch):
    destination = tmp_path / "dump.json"
    destination.write_text('{"previous":true}')
    manager = BotManager(dump_file_path=str(destination))
    closed = AsyncMock()
    monkeypatch.setattr("src.main.close_db", closed)

    def busy(*args):
        raise OSError(errno.EBUSY, "file mount cannot be replaced")

    monkeypatch.setattr("src.main.os.replace", busy)
    with pytest.raises(ExceptionGroup, match="Application shutdown failed"):
        await manager.close()
    closed.assert_awaited_once()
    assert json.loads(destination.read_text()) == {"previous": True}
    assert list(tmp_path.iterdir()) == [destination]


def test_compose_mounts_dump_parent_directory():
    for filename in ("docker-compose.yml", "docker-compose.prod.example.yml", "docker-compose.local.example.yml"):
        config = yaml.safe_load(Path(filename).read_text())
        crawler = config["services"]["crawler"]
        assert "./state:/app/state" in crawler["volumes"]
        assert all(":/app/dump.json" not in mount for mount in crawler["volumes"])
        assert "DUMP_FILE_PATH=/app/state/dump.json" in crawler["environment"]


PROBE = r"""
import asyncio, errno, logging, os
from pathlib import Path
from sqlalchemy import text
import src.main as app
from src.bot import DummyBot
from tests.test_main import make_article
logging.basicConfig(level=logging.INFO)
mode = os.environ["PROBE_MODE"]

class PendingBot(DummyBot):
    async def _send(self, article):
        await asyncio.Event().wait()

class ProbeManager(app.BotManager):
    async def init_session(self):
        # A real SQLite connection opens a worker thread: failing to close it can
        # leave the interpreter alive even when the main coroutine has finished.
        engine = app.get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if mode == "startup":
            print("LOADING", flush=True)
            await asyncio.Event().wait()
        self.article_cache = {"dummy": app.crawler.ArticleCollection({1: make_article(1)})}
        pending = PendingBot("dummy")
        pending.cache = {"dummy": {1: "message-123"}}
        await pending.send(make_article(2))
        self.bots = {"dummy": pending}
        if mode == "busy":
            def busy(*args):
                raise OSError(errno.EBUSY, "file mount cannot be replaced")
            app.os.replace = busy

    async def run(self, **kwargs):
        print("READY", flush=True)
        await super().run(**kwargs)

    async def _run_locked(self):
        pass

    async def dump(self, *args, **kwargs):
        print("SAVING", flush=True)
        if mode == "repeat":
            await asyncio.sleep(0.25)
        await super().dump(*args, **kwargs)

app.BotManager = ProbeManager
app.main()
"""


def read_marker(process, marker):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=8), f"No {marker} from subprocess"
        assert process.stdout.readline().strip() == marker


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("mode", ["success", "busy", "repeat", "startup"])
def test_real_sigterm_persists_or_reports_failure_without_hanging(tmp_path, mode):
    dump = tmp_path / "state" / "dump.json"
    dump.parent.mkdir()
    dump.write_text('{"previous":true}')
    root = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", PROBE],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": root,
            "TESTING": "1",
            "PROBE_MODE": mode,
            "DUMP_FILE_PATH": str(dump),
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        read_marker(process, "LOADING" if mode == "startup" else "READY")
        process.send_signal(signal.SIGTERM)
        if mode == "repeat":
            read_marker(process, "SAVING")
            process.send_signal(signal.SIGTERM)
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == (1 if mode == "busy" else 0), stderr
        assert "Task exception was never retrieved" not in stderr
        assert "Shutdown finished" in stderr
        data = json.loads(dump.read_text())
        if mode in {"busy", "startup"}:
            assert data == {"previous": True}
            if mode == "busy":
                assert "file mount cannot be replaced" in stderr
        else:
            assert list(data["crawler"]["dummy"]) == ["1"]
            assert data["bot"]["dummy"]["cache"]["dummy"]["1"] == "message-123"
            assert data["bot"]["dummy"]["queue"][0][0] == "send"
            assert data["bot"]["dummy"]["queue"][0][1]["article_id"] == 2
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
