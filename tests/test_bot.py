import asyncio
import logging
import re
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, RetryAfter, TimedOut
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
@pytest.mark.parametrize("operation", ["send", "edit", "edit_photo"])
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
            if operation == "edit_photo":
                message.photo = (object(),)
                message.edit_caption = call
            else:
                message.edit_text = call
            await bot.set_msg_obj(article, message)
            await bot._edit(article)
    finally:
        await bot.close()

    sleep.assert_awaited_once_with(2.0)
    assert call.await_count == 2


@pytest.mark.asyncio
async def test_photo_send_keeps_caption_button_and_message_cache():
    bot = TelegramBot("telegram", token="fake-token", target="target")
    bot.bot = AsyncMock()
    article = make_article()
    article["extra"] = {"image_url": "https://ac.arca.live/a.jpg?key=signed", "price": "1,000원", "delivery": "무료"}
    try:
        message = await bot._send(article)
        assert await bot.get_msg_obj(article) is message
        kwargs = bot.bot.send_photo.call_args.kwargs
        assert kwargs["photo"] == article["extra"]["image_url"]
        assert kwargs["caption"] == "\\[category\\] Article 1\n1,000원 / 무료"
        assert kwargs["reply_markup"].inline_keyboard[0][0].url == article["url"]
        assert "text" not in kwargs
        bot.bot.send_message.assert_not_awaited()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_rejected_photo_falls_back_to_text_and_caches_text_message():
    bot = TelegramBot("telegram", token="fake-token", target="target")
    bot.bot = AsyncMock()
    bot.bot.send_photo.side_effect = BadRequest("Failed to get HTTP URL content")
    article = make_article()
    article["extra"]["image_url"] = "https://ac.arca.live/expired.jpg"
    try:
        message = await bot._send(article)
        bot.bot.send_photo.assert_awaited_once()
        bot.bot.send_message.assert_awaited_once_with(chat_id="target", **bot._make_message(article))
        assert message is bot.bot.send_message.return_value
        assert await bot.get_msg_obj(article) is message
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_photo_timeout_does_not_send_a_duplicate_text_notification():
    bot = TelegramBot("telegram", token="fake-token", target="target")
    bot.bot = AsyncMock()
    bot.bot.send_photo.side_effect = TimedOut()
    article = make_article()
    article["extra"]["image_url"] = "https://ac.arca.live/a.jpg"
    try:
        assert await bot._send(article) is None
        assert await bot.get_msg_obj(article) is None
        bot.bot.send_photo.assert_awaited_once()
        bot.bot.send_message.assert_not_awaited()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_photo_rate_limit_retries_photo(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("src.bot.asyncio.sleep", sleep)
    bot = TelegramBot("telegram", token="fake-token", target="target")
    bot.bot = AsyncMock()
    message = FakeMessage()
    bot.bot.send_photo.side_effect = [RetryAfter(2), message]
    article = make_article()
    article["extra"]["image_url"] = "https://ac.arca.live/a.jpg"
    try:
        assert await bot._send(article) is message
        sleep.assert_awaited_once_with(2)
        assert bot.bot.send_photo.await_count == 2
        bot.bot.send_message.assert_not_awaited()
    finally:
        await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("photo", [False, True])
async def test_restored_message_edits_match_actual_message_type(photo):
    bot = TelegramBot("telegram", token="fake-token", target="target")
    bot.bot = AsyncMock(spec=type(bot.bot))
    article = make_article()
    article["is_end"] = True
    article["extra"] = {"image_url": "https://ac.arca.live/expired.jpg", "price": "900원", "delivery": "무료"}
    saved = {"message_id": 123, "date": 1, "chat": {"id": 456, "type": "private"}}
    if photo:
        saved.update(
            photo=[{"file_id": "photo", "file_unique_id": "unique", "width": 1280, "height": 751}], caption="old"
        )
    else:
        saved["text"] = "old"
    try:
        await bot.from_dict({"cache": {"dummy": {"1": saved}}, "queue": []})
        await bot._edit(article)
        if photo:
            kwargs = bot.bot.edit_message_caption.call_args.kwargs
            assert kwargs["caption"] == "~\\[category\\] Article 1\n900원 / 무료~"
            bot.bot.edit_message_text.assert_not_awaited()
        else:
            kwargs = bot.bot.edit_message_text.call_args.kwargs
            assert kwargs["text"] == "~\\[category\\] Article 1\n900원 / 무료~"
            bot.bot.edit_message_caption.assert_not_awaited()
        assert kwargs["reply_markup"].inline_keyboard[0][0].url == article["url"]
        bot.bot.send_photo.assert_not_awaited()
        bot.bot.edit_message_media.assert_not_awaited()
        await bot._delete(article)
        bot.bot.delete_message.assert_awaited_once()
    finally:
        await bot.close()


@pytest.mark.parametrize("closed", [False, True])
def test_photo_caption_limit_preserves_unicode_and_markdown(closed):
    article = make_article()
    article["title"] = "🔔_\\" * 400
    article["is_end"] = closed
    kwargs = TelegramBot._make_message(None, article, photo=True)
    caption = kwargs["caption"]
    if closed:
        assert caption.startswith("~") and caption.endswith("~")
        caption = caption[1:-1]
    plain = re.sub(r"\\(.)", r"\1", caption)
    assert plain.endswith("…")
    assert len(plain.encode("utf-16-le")) <= 2048
    assert "\ufffd" not in plain
    assert caption == telegram_escape_markdown(plain, version=2)
    assert len(TelegramBot._make_message(None, article)["text"]) > len(kwargs["caption"])


@pytest.mark.parametrize("photo", [False, True])
@pytest.mark.parametrize("best", [False, True])
@pytest.mark.parametrize("comments, recommend", [(0, "0"), (17, "8")])
def test_engagement_footer_matches_preview_and_keeps_zero_counts(photo, best, comments, recommend):
    article = make_article()
    article["extra"] = {"comment_count": comments, "recommend": recommend, "is_best": best}
    kwargs = TelegramBot._make_message(None, article, photo=photo)
    footer = f"👍 {recommend} · 💬 {comments}" + (" · 🔥 초핫딜" if best else "")
    assert kwargs["caption" if photo else "text"] == "\\[category\\] Article 1\n\n" + footer
    assert kwargs["reply_markup"].inline_keyboard[0][0].url == article["url"]


def test_closed_long_photo_keeps_engagement_footer_outside_strikethrough():
    article = make_article()
    article["title"] = "🔔_\\" * 400
    article["is_end"] = True
    article["extra"] = {"comment_count": 17, "recommend": "8", "is_best": True}
    caption = TelegramBot._make_message(None, article, photo=True)["caption"]
    body, footer = caption.split("\n\n")
    assert body.startswith("~") and body.endswith("…~")
    assert footer == "👍 8 · 💬 17 · 🔥 초핫딜"
    plain = re.sub(r"\\(.)", r"\1", body[1:-1]) + "\n\n" + footer
    assert len(plain.encode("utf-16-le")) <= 2048


@pytest.mark.parametrize("photo", [False, True])
def test_quasarzone_popular_label_does_not_change_arcalive_default(photo):
    article = make_article()
    article["extra"] = {"recommend": "13", "comment_count": 33, "is_best": True, "best_label": "인기"}
    key = "caption" if photo else "text"
    assert TelegramBot._make_message(None, article, photo=photo)[key].endswith("👍 13 · 💬 33 · 🔥 인기")
    article["extra"].pop("best_label")
    assert TelegramBot._make_message(None, article, photo=photo)[key].endswith("👍 13 · 💬 33 · 🔥 초핫딜")
