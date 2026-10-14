from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup

from src.crawler.quasarzone import QuasarzoneCrawler
from src.http_client import HttpResponse

TITLE = "[쇼핑몰] 할인 상품 (9,900원/무료배송)"


def make_page(layout, title, ended=False, locked=False):
    lock = '<i class="fa-lock"></i>' if locked else ""
    link = f'<a class="subject-link" href="/bbs/qb_saleinfo/views/123">{lock}{title}</a>'
    if layout == "v2":
        return f"""
        <a class="v2-board-head__title">핫딜 게시판</a>
        <div class="v2-notice-row"><a href="/bbs/qb_saleinfo/views/999">공지</a></div>
        <div class="v2-list">
          <div class="v2-list-row v2-list-row--hotdeal v2-partner-row">
            <a class="subject-link" href="/bbs/qb_partnersaleinfo/views/999">파트너 광고</a>
          </div>
          <div class="v2-list-row v2-list-row--hotdeal {"is-done" if ended else ""}">
            <span class="qc-count-good">0</span>
            <div class="v2-list-row__stack">
              <div class="v2-list-row__line1"><span class="v2-badge">생활/식품</span></div>
              <div class="v2-list-row__line2"><div class="tit-with-badge"><p class="tit">
                {link}<span class="board-list-comment"><span class="ctn-count">12</span></span>
              </p></div></div>
              <div class="v2-list-row__line3">
                <div class="v2-list-row__price-group">
                  <span class="v2-list-row__ship"><img alt="쇼핑몰"></span>
                  <span class="v2-list-row__price">￦9,900</span>
                  <span class="v2-list-row__ship">배송비 무배</span>
                </div>
                <div class="v2-list-row__meta-group">
                  <span class="v2-nick" data-nick="작성자"><span class="user-nick-label">작성자</span></span>
                  <span class="v2-list-row__hit">조회 <span class="qc-count-hit">1.1천</span></span>
                </div>
              </div>
            </div>
          </div>
        </div>
        """
    return f"""
    <div class="l-title"><h2>핫딜 게시판</h2></div>
    <div class="market-info-type-list"><table><tbody><tr>
      <td><span class="num">0</span></td><td>
        <span class="label">{"종료" if ended else "진행중"}</span>
        {link}<span class="board-list-comment">12</span>
        <span class="nick" data-nick="작성자">작성자</span><span class="count">1.1천</span>
        <div class="market-info-sub"><p>
          <span class="category">생활/식품</span>
          <span>가격 <span>￦9,900</span></span><span>배송비 무배</span>
        </p></div>
      </td>
    </tr></tbody></table></div>
    """


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["v2", "legacy"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("ended", [False, True])
async def test_quasarzone_parses_title_and_metadata(layout, nested, ended):
    title = f'<span class="ellipsis-with-reply-cnt">{TITLE}</span>' if nested else TITLE
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        data = await instance.parsing(make_page(layout, f"\n  {title}\n ", ended))
    finally:
        await instance.close()
    assert data == {
        123: {
            "article_id": 123,
            "title": TITLE,
            "category": "생활/식품",
            "site_name": "퀘이사존",
            "board_name": "핫딜 게시판",
            "writer_name": "작성자",
            "crawler_name": "quasarzone",
            "url": "https://quasarzone.com/bbs/qb_saleinfo/views/123",
            "is_end": ended,
            "extra": {
                "recommend": "0",
                "view": "1.1천",
                "price": "￦9,900",
                "delivery": "무배",
                "comment_count": 12,
                "best_label": "인기",
                **({"is_best": False} if layout == "v2" else {}),
            },
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["v2", "legacy"])
@pytest.mark.parametrize("title,locked", [(" \n ", False), (TITLE, True)])
async def test_quasarzone_skips_empty_titles_and_locked_articles(layout, title, locked):
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        assert await instance.parsing(make_page(layout, title, locked=locked)) == {}
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_quasarzone_excludes_partner_row_with_complete_metadata():
    soup = BeautifulSoup(make_page("v2", TITLE), "html.parser")
    row = soup.select_one(".v2-list-row:not(.v2-partner-row)")
    partner = deepcopy(row)
    partner["class"].append("v2-partner-row")
    partner.select_one(".subject-link")["href"] = "/bbs/qb_partnersaleinfo/views/999"
    soup.select_one(".v2-list").append(partner)
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        assert set(await instance.parsing(str(soup))) == {123}
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    ['<span class="brand"><img alt="쇼핑몰"></span>', "<span></span>", "<span>가격</span>"],
)
async def test_quasarzone_tolerates_missing_metadata_text(metadata):
    html = make_page("legacy", TITLE).replace('<span class="category">', metadata + '<span class="category">')
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        data = await instance.parsing(html)
    finally:
        await instance.close()
    assert data[123]["extra"]["price"] == "￦9,900"
    assert data[123]["category"] == "생활/식품"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("가능", True), ("불가능", False)])
async def test_quasarzone_direct_delivery_requires_exact_status(status, expected):
    html = make_page("legacy", TITLE).replace("</p>", f"<span>직배 <span> {status} </span></span></p>")
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        data = await instance.parsing(html)
    finally:
        await instance.close()
    assert data[123]["extra"]["direct_delivery"] is expected


@pytest.mark.parametrize(
    "source", ["https://img2.quasarzone.com", "//img2.quasarzone.com", "https://img2.quasarzone.co.kr"]
)
def test_image_alias_preserves_thumbnail_path_and_query(source):
    path = "/qb_saleinfo/2026/10/14/qt_example.webp?key=a%2fb%2B+c&size=small"
    assert QuasarzoneCrawler._image_url(source + path) == "https://img2.quasarzone.co.kr" + path
    assert QuasarzoneCrawler._image_url(source + "/a.webp?") == "https://img2.quasarzone.co.kr/a.webp?"


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        [],
        "http://img2.quasarzone.com/a.jpg",
        "https://other.example/a.jpg",
        "https://img2.quasarzone.com.evil.example/a.jpg",
        "https://user@img2.quasarzone.com/a.jpg",
        "https://img2.quasarzone.com:bad/a.jpg",
        "https://[invalid/a.jpg",
        "//img2.quasarzone.com/a\nb.jpg",
    ],
)
def test_invalid_optional_image_url_is_ignored(source):
    assert QuasarzoneCrawler._image_url(source) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("count, expected", [("0", 0), ("1,234", 1234), ("-1", None), ("?", None), ("", None)])
async def test_comments_keep_hidden_zero_and_ignore_invalid_values(count, expected):
    soup = BeautifulSoup(make_page("v2", TITLE), "html.parser")
    comment = soup.select_one(".ctn-count")
    comment.string = count
    comment.parent["style"] = "display:none"
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        data = await instance.parsing(str(soup))
    finally:
        await instance.close()
    assert 123 in data
    assert data[123]["extra"].get("comment_count") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("popular", [False, True])
async def test_photo_and_popularity_are_read_from_list_without_additional_requests(popular):
    soup = BeautifulSoup(make_page("v2", TITLE), "html.parser")
    row = soup.select_one(".v2-list-row:not(.v2-partner-row)")
    row["data-preview"] = "https://img2.quasarzone.com/editor/2026/10/14/qt_image.webp?"
    row.select_one(".qc-count-good").string = "100"
    if popular:
        badge = soup.new_tag("span", attrs={"class": "v2-badge--hot"})
        badge.string = "🔥인기"
        row.select_one(".v2-list-row__line1").append(badge)
    url = "https://quasarzone.com/bbs/qb_saleinfo"
    client = SimpleNamespace(get=AsyncMock(return_value=HttpResponse(200, str(soup).encode(), {}, url)))
    instance = QuasarzoneCrawler("quasarzone", [url], client=client)
    try:
        data = await instance.get()
    finally:
        await instance.close()
    assert data[123]["extra"]["image_url"] == "https://img2.quasarzone.co.kr/editor/2026/10/14/qt_image.webp?"
    assert data[123]["extra"]["comment_count"] == 12
    assert data[123]["extra"]["recommend"] == "100"
    assert data[123]["extra"]["is_best"] is popular  # Never infer popularity from recommendation count.
    assert data[123]["category"] == "생활/식품"
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_image_does_not_use_shop_logo_or_drop_the_deal():
    instance = QuasarzoneCrawler("quasarzone", [])
    try:
        data = await instance.parsing(make_page("v2", TITLE))
    finally:
        await instance.close()
    assert "image_url" not in data[123]["extra"]
    assert data[123]["extra"]["comment_count"] == 12
