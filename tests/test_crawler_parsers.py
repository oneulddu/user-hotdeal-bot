import pytest

from src import crawler
from src.tools import crawler as crawler_cli


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "writer_html,expected",
    [
        ('<i class="nlevel lv3"></i>작성자', "작성자"),
        ('<span>작성자</span><img alt="">', "작성자"),
        ('<span>작성자</span><img alt="  ">', "작성자"),
        ("<span>작성자</span><img>", "작성자"),
        ('<img alt="이미지 닉네임">', "이미지 닉네임"),
    ],
)
async def test_ppomppu_parses_text_and_image_writers(writer_html, expected):
    html = """
    <div class="bbs_title"><span class="bname"><a>뽐뿌게시판</a></span></div>
    <input name="id" value="ppomppu">
    <table id="revolution_main_table">
      <tr class="baseList bbs_new1">
        <td>722021</td>
        <td>
          <div class="baseList-box">
            <a class="baseList-title">국내산 쪽파 1kg</a>
            <small class="baseList-small">[식품/건강]</small>
          </div>
        </td>
        <td><a class="baseList-name"><i class="nlevel lv3"></i>작성자</a></td>
        <td class="baseList-rec">1</td>
        <td class="baseList-views">1517</td>
      </tr>
    </table>
    """
    html = html.replace('<i class="nlevel lv3"></i>작성자', writer_html)
    crawler_instance = crawler.PpomppuCrawler("ppomppu", [])

    try:
        data = await crawler_instance.parsing(html)
    finally:
        await crawler_instance.close()

    assert data[722021]["writer_name"] == expected


@pytest.mark.asyncio
async def test_ppomppu_rss_strips_whitespace_before_hits_fields():
    xml = """
    <rss>
      <channel>
        <title>안녕하세요. 뽐뿌입니다 - 뽐뿌게시판</title>
        <item>
          <title>국내산 쪽파 1kg</title>
          <link>http://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&amp;no=722021</link>
          <author>작성자</author>
          <hits> [1|1517|0|0]</hits>
        </item>
      </channel>
    </rss>
    """
    crawler_instance = crawler.PpomppuRSSCrawler("ppomppu_rss", [])

    try:
        data = await crawler_instance.parsing(xml)
    finally:
        await crawler_instance.close()

    assert data[722021]["extra"] == {
        "comments": "1",
        "view": "1517",
        "recommend": "0",
        "not_recommend": "0",
    }


@pytest.mark.asyncio
async def test_fmkorea_parses_nested_ellipsis_title():
    html = """
    <div class="bd_tl"><h1><a href="/hotdeal">핫딜</a></h1></div>
    <div id="content">
      <div class="fm_best_widget">
        <ul>
          <li>
            <h3 class="title">
              <a href="/10118769542">
                <span class="ellipsis-target">닌텐도 프로콘2</span>
                <span class="comment_count">[3]</span>
              </a>
            </h3>
            <div class="hotdeal_info">
              <span>쇼핑몰: <a>SSG</a></span>
              <span>가격: <a>84,537원</a></span>
              <span>배송: <a>무배</a></span>
            </div>
            <span class="category"><a>SW/게임</a></span>
            <span class="author"> / 작성자</span>
          </li>
        </ul>
      </div>
    </div>
    """
    crawler_instance = crawler.FmkoreaCrawler("fmkorea", [])

    try:
        data = await crawler_instance.parsing(html)
    finally:
        await crawler_instance.close()

    assert data[10118769542]["title"] == "닌텐도 프로콘2"


@pytest.mark.asyncio
@pytest.mark.parametrize("ended", [False, True])
@pytest.mark.parametrize("suffix", ["", "?page=1#list"])
async def test_damoang_parses_current_post_rows(ended, suffix):
    html = """
    <meta property="og:title" content="알뜰구매">
    <link rel="canonical" href="https://damoang.net/economy">
    <a class="post-row" href="/economy/77811">
      <div>
        <div><div>22</div></div>
        <div>
          <div>
            <span>종료</span>
            <span><span class="post-title">미친 가성비 백팩 추천</span></span>
          </div>
          <span class="post-meta-text"><span>작성자</span></span>
          <span class="post-meta-text">07.22</span>
          <span class="post-meta-text">2.8k</span>
          <div class="mobile-meta"><span>22</span><span>07.22</span><span>2.8k</span></div>
        </div>
      </div>
    </a>
    """
    html = html.replace('href="https://damoang.net/economy"', f'href="https://damoang.net/economy{suffix}"')
    html = html.replace('href="/economy/77811"', f'href="/economy/77811{suffix}"')
    if not ended:
        html = html.replace("<span>종료</span>", "<span>진행</span>")
    crawler_instance = crawler.DamoangCrawler("damoang", [])

    try:
        data = await crawler_instance.parsing(html)
    finally:
        await crawler_instance.close()

    assert data[77811] == {
        "article_id": 77811,
        "title": "미친 가성비 백팩 추천",
        "category": "종료" if ended else "진행",
        "site_name": "다모앙",
        "board_name": "알뜰구매",
        "writer_name": "작성자",
        "crawler_name": "damoang",
        "url": "https://damoang.net/economy/77811",
        "is_end": ended,
        "extra": {"recommend": "22", "view": "2.8k"},
    }


@pytest.mark.asyncio
async def test_zod_parses_current_definition_list_metadata():
    html = """
    <div class="app-board-title"><a href="/deal">특가</a></div>
    <div id="board-list">
      <ul class="zod-board-list--deal">
        <li>
          <a href="/deal/8473120">
            <span class="app-list-title-item">네이버페이 포인트</span>
            <dl class="app-list-meta zod-board--deal-meta">
              <dt>홈페이지/장소</dt><dd><strong>네이버</strong></dd>
              <dt>가격</dt><dd>가격: <strong>0원</strong></dd>
              <dt>배송비</dt><dd>배송비: <strong>무료</strong></dd>
            </dl>
            <dl class="app-list-meta">
              <dt>작성자 닉네임</dt>
              <dd class="app-list-member"><span>작성자</span></dd>
              <dt>추천수</dt><dd class="app-list__voted-count">0</dd>
            </dl>
          </a>
        </li>
      </ul>
    </div>
    """
    crawler_instance = crawler.ZodCrawler("zod", [])

    try:
        data = await crawler_instance.parsing(html)
    finally:
        await crawler_instance.close()

    assert data[8473120]["writer_name"] == "작성자"
    assert data[8473120]["category"] == "네이버"
    assert data[8473120]["extra"] == {
        "mall": "네이버",
        "price": "0원",
        "delivery": "무료",
        "recommend": 0,
    }


@pytest.mark.asyncio
async def test_damoang_keeps_legacy_layout():
    html = """
    <div class="page-title">알뜰구매</div><input name="bo_table" value="economy">
    <div id="bo_list"><div class="list-group-item">
      <div class="flex-fill"><a href="https://damoang.net/economy/123">상품</a></div>
      <div class="sv_wrap"><span class="sv_name">작성자</span></div>
      <div class="rcmd-box"><span>추천</span>3</div>
      <div class="wr-num order-4"><span>조회</span>100</div><span class="badge">진행</span>
    </div></div>
    """
    instance = crawler.DamoangCrawler("damoang", [])
    try:
        data = await instance.parsing(html)
        assert data[123]["title"] == "상품"
        assert data[123]["is_end"] is False
        assert data[123]["extra"] == {"recommend": "3", "view": "100"}
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_zod_keeps_legacy_metadata_and_ended_status():
    html = """
    <div class="app-board-title"><a href="/deal">특가</a></div>
    <div id="board-list"><ul class="zod-board-list--deal"><li class="zod-board-list--deal-ended">
      <a href="/deal/123"><span class="app-list-title-item">상품</span>
        <span class="app-list-member">작성자</span>
        <span class="zod-board--deal-meta-category">식품</span>
        <div class="app-list-meta zod-board--deal-meta">
          <span><strong>쇼핑몰</strong></span><span>가격: <strong>100원</strong></span>
          <span>배송비: <strong>무료</strong></span>
        </div>
        <span class="app-list__voted-count"><span>2</span></span><span class="app-list-comment">3</span>
      </a>
    </li></ul></div>
    """
    instance = crawler.ZodCrawler("zod", [])
    try:
        data = await instance.parsing(html)
        assert data[123]["category"] == "식품"
        assert data[123]["is_end"] is True
        assert data[123]["extra"] == {
            "mall": "쇼핑몰",
            "price": "100원",
            "delivery": "무료",
            "recommend": 2,
            "comment": 3,
        }
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_coolenjoy_cli_uses_rss_crawler(monkeypatch):
    created = {}

    class FakeRSSCrawler:
        def __init__(self, name, url_list):
            created["name"] = name
            created["url_list"] = url_list

        async def get(self):
            return {}

        async def close(self):
            return None

    def fail_html_crawler(*args, **kwargs):
        pytest.fail("CoolenjoyCrawler must not receive the RSS URL")

    monkeypatch.setattr(crawler_cli.crawler, "CoolenjoyRSSCrawler", FakeRSSCrawler)
    monkeypatch.setattr(crawler_cli.crawler, "CoolenjoyCrawler", fail_html_crawler)

    await crawler_cli.main("coolenjoy")

    assert created == {
        "name": "coolenjoy_crawler",
        "url_list": ["https://coolenjoy.net/bbs/rss.php?bo_table=jirum"],
    }
