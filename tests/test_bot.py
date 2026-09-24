import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from telegram.error import RetryAfter
from telegram.helpers import escape_markdown as telegram_escape_markdown

from src import crawler
from src.bot import BaseBot, TelegramBot, _retry_after_seconds
from src.util import TelegramHandler, escape_markdown


def make_article() -> crawler.BaseArticle:
    return crawler.BaseArticle(
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


class FakeMessage:
    message_id = 123


class RecordingBot(BaseBot):
    def __init__(self, name: str = "recording"):
        self.sent = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        super().__init__(name)

    async def _send(self, data: crawler.BaseArticle) -> None:
        self.started.set()
        await self.release.wait()
        self.sent.append(data["article_id"])

    async def _edit(self, data: crawler.BaseArticle) -> None:
        self.sent.append(data["article_id"])

    async def _delete(self, data: crawler.BaseArticle) -> None:
        self.sent.append(data["article_id"])

    async def from_dict(self, data) -> None:
        for item in data["queue"]:
            await self.queue.put(item)


@pytest.mark.asyncio
async def test_telegram_send_stores_message_on_success():
    bot = TelegramBot("telegram", token="fake-token", target="target")

    class FakeTelegramClient:
        async def send_message(self, **_kwargs):
            return FakeMessage()

    bot.bot = FakeTelegramClient()

    try:
        msg = await bot._send(make_article())
    finally:
        await bot.close()

    assert msg is not None
    assert await bot.get_msg_obj(make_article()) is msg


@pytest.mark.asyncio
async def test_telegram_send_does_not_swallow_unexpected_exception():
    bot = TelegramBot("telegram", token="fake-token", target="target")

    class BrokenTelegramClient:
        async def send_message(self, **_kwargs):
            raise RuntimeError("boom")

    bot.bot = BrokenTelegramClient()

    try:
        with pytest.raises(RuntimeError, match="boom"):
            await bot._send(make_article())
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_consumer_waits_on_queue_without_polling_delay():
    bot = RecordingBot()

    try:
        await bot.send(make_article())
        await asyncio.wait_for(bot.started.wait(), timeout=0.2)
        bot.release.set()
        await asyncio.wait_for(_wait_until(lambda: bot.sent == [1]), timeout=0.2)
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_consumer_requeues_in_flight_item_on_cancel():
    bot = RecordingBot()

    await bot.send(make_article())
    await asyncio.wait_for(bot.started.wait(), timeout=0.2)
    data = await bot.to_dict()

    assert len(data["queue"]) == 1
    assert data["queue"][0][0] == "send"
    assert data["queue"][0][1]["article_id"] == 1


@pytest.mark.asyncio
async def test_consumer_requeues_in_flight_item_before_pending_items():
    bot = RecordingBot()
    article = make_article()

    await bot.send(article)
    await asyncio.wait_for(bot.started.wait(), timeout=0.2)
    await bot.edit(article)

    data = await bot.to_dict()

    assert [item[0] for item in data["queue"]] == ["send", "edit"]


async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0)


@pytest.mark.parametrize("text", [r"a\b.c", "\\_*[]()~`>#+-=|{}.!"])
def test_escape_markdown_matches_telegram(text):
    assert escape_markdown(text) == telegram_escape_markdown(text, version=2)


def test_telegram_handler_escapes_exception_pre_block_without_changing_cached_text():
    handler = TelegramHandler("fake-token", "target", emoji=False)
    handler.setFormatter(logging.Formatter("%(message)s"))
    error = ValueError(r"bad \path `code`")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), (ValueError, error, None))
    original = handler.formatter.formatException(record.exc_info)
    record.exc_text = original

    mapped = handler.mapLogRecord(record)

    expected = telegram_escape_markdown(original, version=2, entity_type="pre")
    assert mapped["text"] == "failed\n```\n" + expected + "\n```"
    assert record.exc_text == original


@pytest.mark.parametrize("value", [2, 2.5, timedelta(seconds=2.5)])
def test_retry_after_seconds(value):
    assert _retry_after_seconds(value) == (2 if value == 2 else 2.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "edit"])
@pytest.mark.parametrize("use_timedelta", [False, True])
async def test_telegram_retries_with_seconds(monkeypatch, operation, use_timedelta):
    monkeypatch.setenv("PTB_TIMEDELTA", "true" if use_timedelta else "false")
    retry = RetryAfter(timedelta(seconds=2))
    sleep = AsyncMock()
    monkeypatch.setattr("src.bot.asyncio.sleep", sleep)
    bot = TelegramBot("telegram", token="fake-token", target="target")
    message = FakeMessage()
    call = AsyncMock(side_effect=[retry, message])
    article = make_article()
    try:
        if operation == "send":
            bot.bot = type("FakeClient", (), {"send_message": call})()
            assert await bot._send(article) is message
            assert await bot.get_msg_obj(article) is message
        else:
            message.edit_text = call
            await bot.set_msg_obj(article, message)
            await bot._edit(article)
    finally:
        await bot.close()

    sleep.assert_awaited_once_with(2.0)
    assert call.await_count == 2
