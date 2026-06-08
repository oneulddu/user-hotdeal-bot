from datetime import datetime
from zoneinfo import ZoneInfo

from src.api.routes.feed import _fill_entry_common
from src.db import Article


class FakeFeedEntry:
    def __init__(self):
        self.values = {}

    def id(self, value):
        self.values["id"] = value

    def title(self, value):
        self.values["title"] = value

    def link(self, href):
        self.values["link"] = href

    def author(self, name):
        self.values["author"] = name

    def published(self, value):
        self.values["published"] = value

    def updated(self, value):
        self.values["updated"] = value

    def category(self, term):
        self.values["category"] = term


def test_fill_entry_common_converts_aware_datetime_to_utc():
    article = Article(
        id="01HX0000000000000000000000",
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
        created_at=datetime(2026, 6, 8, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        updated_at=datetime(2026, 6, 8, 13, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    entry = FakeFeedEntry()

    _fill_entry_common(entry, article)

    assert entry.values["published"].isoformat() == "2026-06-08T03:00:00+00:00"
    assert entry.values["updated"].isoformat() == "2026-06-08T04:00:00+00:00"
