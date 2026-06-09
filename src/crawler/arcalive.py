# 아카라이브 핫딜 채널
# https://arca.live/b/hotdeal
# API 문서화되면 전환 예정
import re

import aiohttp
from bs4 import BeautifulSoup

from .base_crawler import BaseArticle, BaseCrawler


class ArcaLiveCrawler(BaseCrawler):
    DEFAULT_REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    def __init__(
        self,
        name: str,
        url_list: list[str],
        session: aiohttp.ClientSession | None = None,
        proxy: str | None = None,
        ssl_verify: bool = True,
        ssl_ca_cert: str | None = None,
        request_headers: dict[str, str] | None = None,
        cookie: str | None = None,
        cookie_env: str | None = None,
    ) -> None:
        headers = {**self.DEFAULT_REQUEST_HEADERS, **(request_headers or {})}
        if url_list and "Referer" not in headers:
            headers["Referer"] = url_list[0]

        super().__init__(
            name,
            url_list,
            session=session,
            proxy=proxy,
            ssl_verify=ssl_verify,
            ssl_ca_cert=ssl_ca_cert,
            request_headers=headers,
            cookie=cookie,
            cookie_env=cookie_env,
        )
        self.config_request_headers = request_headers or {}

    async def parsing(self, html: str) -> dict[int, BaseArticle]:
        soup = BeautifulSoup(html, "html.parser")

        # 채널 이름
        if (_board_name := soup.select_one(".board-title .title")) is None or (
            board_name := _board_name.attrs.get("data-channel-name")
        ) is None:
            self.logger.error("Can't find board name, skip parsing")
            return {}

        # 게시글 목록
        if (table := soup.select_one(".list-table")) is None:
            self.logger.error("Can't find article list, skip parsing")
            return {}
        rows = table.select(".vrow.hybrid")

        data: dict[int, BaseArticle] = {}
        for row in rows:
            # 하나라도 실패할 경우 건너뛰기
            if (_title_tag := row.select_one(".title")) is None:
                self.logger.warning("Cannot get article title tag")
                continue
            if (_title := _title_tag.find_all(string=True, recursive=False)) is None:
                self.logger.warning("Cannot get article title")
                continue
            else:
                title = "".join(_title).strip()
            if (_url := _title_tag.attrs.get("href")) is None or (
                re_id := re.match(r"\/b\/([\w\d]+)\/(\d+)\??.*", _url)
            ) is None:
                self.logger.warning("Cannot parse article url")
                continue
            else:
                _board_id = re_id.group(1)
                _id = int(re_id.group(2))
            if (_category_tag := row.select_one(".badge")) is None:
                # self.logger.warning("Cannot get category tag")
                continue
            if (_store_name_tag := row.select_one(".deal-store")) is None:
                continue
            if (_writer_tag := row.select_one(".user-info span:first-child")) is None:
                self.logger.warning("Cannot get writer tag")
                continue
            if (_recommend_tag := row.select_one(".col-rate")) is None:
                self.logger.warning("Cannot get recommend value tag")
                continue
            if (_view_tag := row.select_one(".col-view")) is None:
                self.logger.warning("Cannot get view count tag")
                continue
            if (_price_tag := row.select_one(".deal-price")) is None:
                self.logger.warning("Cannot get price tag")
                continue
            if (_delivery_tag := row.select_one(".deal-delivery")) is None:
                self.logger.warning("Cannot get delivery price tag")
                continue
            is_end = True if (row.select_one(".deal-close") is not None) else False

            data[_id] = {
                "article_id": _id,
                "title": title,
                "category": _category_tag.text.strip() if _category_tag is not None else "",
                "site_name": "아카라이브",
                "board_name": board_name,
                "writer_name": _writer_tag.text.strip(),
                "crawler_name": self.name,
                "url": f"https://arca.live/b/{_board_id}/{_id}",
                "is_end": is_end,
                "extra": {
                    "recommend": _recommend_tag.text,
                    "view": _view_tag.text,
                    "price": _price_tag.text.strip(),
                    "delivery": _delivery_tag.text.strip(),
                },
            }
        return data
