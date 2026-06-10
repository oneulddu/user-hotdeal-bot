import asyncio

import pytest

from src import crawler
from src.bot import BaseBot, TelegramBot


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


async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0)
