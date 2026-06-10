# 아카라이브 핫딜 채널
# https://arca.live/b/hotdeal
# API 문서화되면 전환 예정
import datetime
import os
import re

import aiohttp
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from scrapling.fetchers import AsyncStealthySession

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


class ArcaLiveCrawlerV2(ArcaLiveCrawler):
    """Scrapling-based ArcaLive crawler for Cloudflare-protected responses."""

    SCRAPLING_WAIT_SELECTOR = ".list-table"
    SCRAPLING_USER_DATA_DIR = "./data/scrapling-arcalive"

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
        scrapling_session: AsyncStealthySession | None = None,
    ) -> None:
        super().__init__(
            name,
            url_list,
            session=session,
            proxy=proxy,
            ssl_verify=ssl_verify,
            ssl_ca_cert=ssl_ca_cert,
            request_headers=request_headers,
            cookie=cookie,
            cookie_env=cookie_env,
        )
        self._scrapling_session = scrapling_session
        self._scrapling_session_started = False
        self._scrapling_useragent = self._configured_useragent(request_headers or {})

    @staticmethod
    def _configured_useragent(headers: dict[str, str]) -> str | None:
        for key, value in headers.items():
            if key.lower() == "user-agent":
                return value
        return None

    def _scrapling_extra_headers(self) -> dict[str, str]:
        return {key: value for key, value in self.request_headers.items() if key.lower() != "user-agent"}

    def _scrapling_cookies(self) -> list[dict[str, str]]:
        if not self.request_cookies or not self.url_list:
            return []
        return [{"name": key, "value": value, "url": self.url_list[0]} for key, value in self.request_cookies.items()]

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    async def _ensure_scrapling_session(self) -> AsyncStealthySession:
        if self._scrapling_session is None:
            kwargs = {
                "max_pages": 1,
                "headless": self._env_bool("ARCALIVE_SCRAPLING_HEADLESS", True),
                "disable_resources": self._env_bool("ARCALIVE_SCRAPLING_DISABLE_RESOURCES", False),
                "network_idle": True,
                "load_dom": True,
                "google_search": False,
                "solve_cloudflare": True,
                "locale": "ko-KR",
                "timezone_id": "Asia/Seoul",
                "proxy": self.proxy,
                "cookies": self._scrapling_cookies() or None,
                "user_data_dir": os.getenv("ARCALIVE_SCRAPLING_USER_DATA_DIR", self.SCRAPLING_USER_DATA_DIR),
                "hide_canvas": True,
                "block_webrtc": True,
            }
            if self._scrapling_useragent:
                kwargs["useragent"] = self._scrapling_useragent
            self._scrapling_session = AsyncStealthySession(**kwargs)

        if not self._scrapling_session_started:
            await self._scrapling_session.start()
            self._scrapling_session_started = True
        return self._scrapling_session

    async def request(self, url: str) -> str | None:
        self.logger.debug("Send Scrapling request to %s", url)
        try:
            scrapling_session = await self._ensure_scrapling_session()
            response = await scrapling_session.fetch(
                url,
                extra_headers=self._scrapling_extra_headers(),
                google_search=False,
                solve_cloudflare=True,
                wait_selector=self.SCRAPLING_WAIT_SELECTOR,
                wait_selector_state="attached",
                timeout=self._env_int("ARCALIVE_SCRAPLING_TIMEOUT_MS", 90_000),
                wait=self._env_int("ARCALIVE_SCRAPLING_WAIT_MS", 5_000),
            )
        except Exception as e:
            self.logger.error("Scrapling request failed: %s (%s)", e, url)
            return None

        if response.status != 200:
            if response.status != self._prev_status:
                self.logger.error("Scrapling response error: %s (%s)", response.status, url)
                await self.dump_scrapling_response(response)
            else:
                self.logger.info("Scrapling response error [skip]: %s (%s)", response.status, url)
            self._prev_status = response.status
            return None

        self._prev_status = response.status
        return response.html_content

    async def dump_scrapling_response(self, response) -> None:
        current_datetime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join("error", f"{current_datetime}_{self.name}.html")

        if not os.path.exists("error"):
            os.makedirs("error")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(response.html_content)

    async def close(self):
        if self._scrapling_session_started and self._scrapling_session is not None:
            await self._scrapling_session.close()
            self._scrapling_session_started = False
        await super().close()


class ArcaLiveCrawlerV15(ArcaLiveCrawler):
    """curl_cffi-based ArcaLive crawler with Chrome TLS impersonation."""

    CURL_IMPERSONATE = "chrome124"

    def _curl_headers(self) -> dict[str, str]:
        return {key: value for key, value in self.request_headers.items() if key.lower() != "user-agent"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _curl_proxies(self) -> dict[str, str] | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _curl_verify(self) -> bool | str:
        if not self.ssl_verify:
            return False
        if self.ssl_ca_cert:
            return self.ssl_ca_cert
        return True

    async def request(self, url: str) -> str | None:
        self.logger.debug("Send curl_cffi request to %s", url)
        try:
            async with CurlAsyncSession(
                impersonate=os.getenv("ARCALIVE_CURL_IMPERSONATE", self.CURL_IMPERSONATE),
                proxies=self._curl_proxies(),
                timeout=self._env_int("ARCALIVE_CURL_TIMEOUT", 30),
                verify=self._curl_verify(),
            ) as session:
                response = await session.get(
                    url,
                    headers=self._curl_headers(),
                    cookies=self.request_cookies or None,
                    allow_redirects=False,
                )
        except Exception as e:
            self.logger.error("curl_cffi request failed: %s (%s)", e, url)
            return None

        if response.status_code != 200:
            if response.status_code != self._prev_status:
                self.logger.error("curl_cffi response error: %s (%s)", response.status_code, url)
                await self.dump_curl_response(response)
            else:
                self.logger.info("curl_cffi response error [skip]: %s (%s)", response.status_code, url)
            self._prev_status = response.status_code
            return None

        self._prev_status = response.status_code
        return response.text

    async def dump_curl_response(self, response) -> None:
        current_datetime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join("error", f"{current_datetime}_{self.name}.html")

        if not os.path.exists("error"):
            os.makedirs("error")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(response.text)
