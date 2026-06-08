import pytest

from src import crawler
from src.bot import TelegramBot


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
