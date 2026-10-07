# 아카라이브 핫딜 채널
# https://arca.live/b/hotdeal
import asyncio
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp
from bs4 import BeautifulSoup
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from multidict import CIMultiDict

from src.http_client import HttpClient, HttpResponse

from .base_crawler import ArticleCollection, BaseArticle, BaseCrawler


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
        *,
        client: HttpClient | None = None,
        proxy_mode: str | None = None,
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
            client=client,
            proxy_mode=proxy_mode,
        )
        self.config_request_headers = request_headers or {}

    def _is_challenge(self, response: HttpResponse) -> bool:
        if super()._is_challenge(response):
            return True
        if response.status != 200:
            return False
        try:
            soup = BeautifulSoup(response.text(), "html.parser")
        except (LookupError, UnicodeDecodeError):
            return False
        # A missing board alone may be a layout change, not an IP block.
        # Avoid matching challenge words/links in normal article titles.
        if soup.select_one(".list-table") is not None:
            return False
        title = soup.title.get_text(strip=True).lower() if soup.title else ""
        return (
            title in {"just a moment...", "attention required! | cloudflare"}
            or soup.select_one("#challenge-form, #cf-challenge-running, #cf-error-details") is not None
        )

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
    """Read-only app API crawler; keep the HTML crawlers available as alternatives."""

    DEFAULT_REQUEST_HEADERS = {
        "User-Agent": "net.umanle.arca.android/0.9.85",
        "Accept": "application/json",
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
        *,
        client: HttpClient | None = None,
        proxy_mode: str | None = None,
    ) -> None:
        # Validate before allocating a transport. Never silently broaden a filter.
        for url in url_list:
            self._api_target(url)
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
            client=client,
            proxy_mode=proxy_mode,
        )
        headers = CIMultiDict(self.DEFAULT_REQUEST_HEADERS)
        headers.update(request_headers or {})
        headers.setdefault("X-Device-Token", str(uuid.uuid4()))
        self.request_headers = headers
        self._channels: dict[str, dict] = {}
        self._channel_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _api_target(url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        match = re.fullmatch(r"/b/([A-Za-z0-9_-]+)/?", parts.path)
        if parts.scheme != "https" or parts.netloc != "arca.live" or not match or parts.fragment:
            raise ValueError("ArcaLiveCrawlerV2 requires https://arca.live/b/{channel} URLs")
        query = parse_qsl(parts.query, keep_blank_values=True)
        for key, value in query:
            if key == "p" and value == "1":
                continue
            if key not in {"category", "target", "keyword"}:
                raise ValueError(f"Unsupported ArcaLiveCrawlerV2 query parameter: {key}")
        params = [(key, value) for key, value in query if key != "p"]
        params.append(("limit", "30"))
        slug = match.group(1)
        return slug, f"https://arca.live/api/app/list/channel/{slug}?{urlencode(params)}"

    def _is_challenge(self, response: HttpResponse) -> bool:
        if BaseCrawler._is_challenge(self, response):
            return True
        if response.status != 200:
            return False
        # HTML interstitials sometimes return 200; do not mark direct access restored.
        try:
            return not isinstance(json.loads(response.text()), dict)
        except (ValueError, LookupError):
            return True

    async def _request(self, url: str, *, proxy: str | None = None) -> HttpResponse | None:
        response = await super()._request(url, proxy=proxy)
        if response is not None and response.status == 200:
            token = response.headers.get("X-Device-Token")
            if token:
                self.request_headers["X-Device-Token"] = token
        return response

    async def _channel(self, slug: str) -> dict:
        async with self._channel_locks.setdefault(slug, asyncio.Lock()):
            if slug not in self._channels:
                body = await super().request(f"https://arca.live/api/app/info/channel/{slug}")
                try:
                    channel = json.loads(body or "null")["channel"]
                    if channel["slug"] == slug and isinstance(channel["name"], str) and channel["name"].strip():
                        self._channels[slug] = {
                            "slug": slug,
                            "name": channel["name"],
                            "categoryData": [
                                {"id": key, "displayName": value}
                                for key, value in self._category_names(channel).items()
                            ],
                        }
                except (ValueError, TypeError, KeyError):
                    self.logger.warning("Cannot read channel metadata: %s", slug)
            # Metadata failure must not discard otherwise valid deal data.
            return self._channels.get(
                slug,
                {
                    "slug": slug,
                    "name": "핫딜 채널" if slug == "hotdeal" else slug,
                    "categoryData": [],
                },
            )

    async def request(self, url: str) -> str | None:
        slug, api_url = self._api_target(url)
        body = await super().request(api_url)
        if body is None:
            return None
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
                raise ValueError("Missing article list")
        except ValueError:
            self.logger.error("Invalid app API response: %s", api_url)
            return None
        # Carry context with each response: parallel channels must not share a slug.
        return json.dumps({"channel": await self._channel(slug), "articles": payload["articles"]}, ensure_ascii=False)

    @staticmethod
    def _money(value: dict, *, delivery: bool = False) -> str:
        if not isinstance(value, dict) or type(value.get("number")) not in (int, float):
            raise ValueError("Invalid deal amount")
        amount = Decimal(str(value["number"]))
        currency = value.get("currency")
        if not amount.is_finite() or amount < 0 or not isinstance(currency, str) or not currency:
            raise ValueError("Invalid deal currency or amount")
        if delivery and amount == 0:
            return "무료"
        number = format(amount, ",f")
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        if currency == "KRW":
            return f"{number}원"
        symbol = {"USD": "$", "JPY": "¥", "EUR": "€"}.get(currency)
        return f"{symbol}{number}" if symbol else f"{number} {currency}"

    @staticmethod
    def _category_names(channel: dict) -> dict[str, str]:
        entries = channel.get("categoryData")
        if not isinstance(entries, list):
            return {}
        return {
            entry["id"]: entry["displayName"]
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry["id"]
            and isinstance(entry.get("displayName"), str)
            and entry["displayName"].strip()
        }

    async def parsing(self, body: str) -> dict[int, BaseArticle]:
        try:
            payload = json.loads(body)
            channel = payload["channel"]
            slug, board_name = channel["slug"], channel["name"]
            categories = self._category_names(channel)
            if not isinstance(payload["articles"], list):
                raise ValueError("Invalid article list")
            data: dict[int, BaseArticle] = {}
            for item in payload["articles"]:
                if not isinstance(item, dict):
                    raise ValueError("Invalid article")
                if item.get("isNotice") is True:
                    continue
                article_id = item["id"]
                deal = item["deal"]
                if type(article_id) is not int or article_id <= 0 or article_id in data:
                    raise ValueError("Invalid or duplicate article ID")
                if not isinstance(deal, dict) or type(deal.get("isClosed")) is not bool:
                    raise ValueError("Missing deal status")
                title, writer = item["title"], item["nickname"]
                if not isinstance(title, str) or not title.strip() or not isinstance(writer, str) or not writer.strip():
                    raise ValueError("Missing title or writer")
                category = (
                    item.get("categoryDisplayName") or categories.get(item.get("category")) or item.get("category")
                )
                if not isinstance(category, str) or not category:
                    raise ValueError("Missing category")
                if any(type(item.get(key)) is not int or item[key] < 0 for key in ("ratingUp", "viewCount")):
                    raise ValueError("Missing article counters")
                data[article_id] = {
                    "article_id": article_id,
                    "title": title.strip(),
                    "category": category.strip(),
                    "site_name": "아카라이브",
                    "board_name": board_name,
                    "writer_name": writer.strip(),
                    "crawler_name": self.name,
                    "url": f"https://arca.live/b/{slug}/{article_id}",
                    "is_end": deal["isClosed"],
                    "extra": {
                        "recommend": str(item["ratingUp"]),
                        "view": str(item["viewCount"]),
                        "price": self._money(deal["price"]),
                        "delivery": self._money(deal["delivery"], delivery=True),
                    },
                }
            return data
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
            # Partial snapshots would make BotManager delete still-existing posts.
            self.logger.error("Invalid app API deal snapshot; keeping previous articles")
            return {}

    async def get(self) -> ArticleCollection:
        responses = await asyncio.gather(*(self.request(url) for url in self.url_list), return_exceptions=True)
        data = ArticleCollection()
        for response in responses:
            if not isinstance(response, str):
                return ArticleCollection()
            articles = await self.parsing(response)
            if not articles:
                return ArticleCollection()
            # Overlapping filters can return the same post at slightly different
            # times. Merge those; only conflicting channel identities are unsafe.
            if any(data[key]["url"] != articles[key]["url"] for key in data.keys() & articles.keys()):
                return ArticleCollection()
            data.update(articles)
        return data


class ArcaLiveCrawlerV15(ArcaLiveCrawler):
    """curl_cffi-based ArcaLive crawler with Chrome TLS impersonation."""

    CURL_IMPERSONATE = "chrome124"
    TRANSPORT_ATTEMPTS = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._curl_session: CurlAsyncSession | None = None
        self._curl_session_users: dict[CurlAsyncSession, int] = {}

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

    def _curl_verify(self) -> bool | str:
        if not self.ssl_verify:
            return False
        if self.ssl_ca_cert:
            return self.ssl_ca_cert
        return True

    async def _request(self, url: str, *, proxy: str | None = None) -> HttpResponse | None:
        self.logger.debug("Send curl_cffi request to %s", url)
        session = None
        try:
            if self._curl_session is None:
                # Reuse connections without carrying response cookies into the
                # next request. Configured cookies remain request-specific.
                self._curl_session = CurlAsyncSession(
                    discard_cookies=True,
                    curl_options={CurlOpt.NOPROXY: ""} if self.proxy is not None else {},
                )
            session = self._curl_session
            self._curl_session_users[session] = self._curl_session_users.get(session, 0) + 1
            response = await session.get(
                url,
                impersonate=os.getenv("ARCALIVE_CURL_IMPERSONATE", self.CURL_IMPERSONATE),
                proxies={"all": proxy} if proxy is not None else None,
                timeout=self._env_int("ARCALIVE_CURL_TIMEOUT", 30),
                verify=self._curl_verify(),
                headers=self._curl_headers(),
                cookies=self.request_cookies or None,
                allow_redirects=False,
            )
        except (Exception, asyncio.CancelledError) as e:
            # Option-setup errors can consume a curl-cffi pool slot without
            # returning it. Retire this session, letting other active users finish.
            if self._curl_session is session:
                self._curl_session = None
            if isinstance(e, asyncio.CancelledError):
                raise
            self.logger.error("curl_cffi request failed: %s (%s)", e, url)
            return None
        finally:
            if session is not None:
                remaining = self._curl_session_users[session] - 1
                if remaining:
                    self._curl_session_users[session] = remaining
                else:
                    self._curl_session_users.pop(session)
                    if session is not self._curl_session:
                        await session.close()

        return HttpResponse(
            status=response.status_code,
            body=response.content,
            headers=response.headers,
            url=str(response.url),
            charset=response.encoding,
        )

    async def close(self):
        try:
            if self._curl_session is not None:
                session = self._curl_session
                self._curl_session = None
                if session not in self._curl_session_users:
                    await session.close()
        finally:
            await super().close()
