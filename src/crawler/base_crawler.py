import asyncio
import datetime
import logging
import math
import os
import time
from abc import ABCMeta, abstractmethod
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from typing import Any, Self, TypedDict

import aiohttp
import logfire

from src.http_client import (
    AiohttpClient,
    HttpClient,
    HttpClientError,
    HttpResponse,
    HttpTimeoutError,
    create_default_http_client,
)

MAX_ERROR_DUMPS = 50
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


def parse_retry_after_seconds(value, *, now: datetime.datetime | None = None) -> int | None:
    """Parse Retry-After delta-seconds or HTTP-date into a non-negative delay."""
    if value is None:
        return None

    raw_value = str(value).strip()
    if raw_value.isdigit():
        if len(raw_value) > 10:
            return MAX_RETRY_AFTER_SECONDS
        try:
            return min(int(raw_value), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            return None

    try:
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=datetime.UTC)

    current_time = now or datetime.datetime.now(datetime.UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=datetime.UTC)
    delay_seconds = max(0, math.ceil((retry_at - current_time).total_seconds()))
    return min(delay_seconds, MAX_RETRY_AFTER_SECONDS)


class BaseArticle(TypedDict):
    article_id: int  # 게시글 번호
    title: str  # 게시글 제목
    category: str  # 게시글 카테고리
    site_name: str  # 커뮤니티 사이트 이름
    board_name: str  # 게시판 이름
    writer_name: str  # 작성자 이름 (닉네임)
    crawler_name: str  # 크롤러 (객체) 이름
    url: str  # 게시글 URL
    is_end: bool  # 핫딜 종료 여부
    extra: dict[str, Any]  # 기타 데이터 저장용


class ArticleCollection(dict[int, BaseArticle]):
    def __init__(self, data: dict[int, BaseArticle] | None = None):
        data = data or {}
        for k, v in data.items():
            self[k] = v

    def __setitem__(self, __key: int | str, __value: BaseArticle) -> None:
        return super().__setitem__(int(__key), __value)

    def __getitem__(self, __key: int) -> BaseArticle:
        return super().__getitem__(__key)

    def __sub__(self, b: Self) -> "ArticleCollection":
        return ArticleCollection({k: v for k, v in self.items() if k not in b})

    def remove_expired(self, i: int) -> None:
        """article_id 값이 i보다 작은 게시글들을 삭제

        Args:
            i (int): 비교할 article_id 값
        """
        # {n: m for n, m in self.article_cache[name].items() if n >= id_min}
        remove_list = [k for k in self.keys() if k < i]
        for k in remove_list:
            self.pop(k)

    def get_new(self, i: int) -> "ArticleCollection":
        """article_id 값이 i보다 큰 게시글들을 모아 새 객체로 반환

        Args:
            i (int): 비교할 article_id 값

        Returns:
            ArticleCollection: 새로운 게시글 모음
        """
        return ArticleCollection({k: v for k, v in self.items() if k > i})


class BaseCrawler(metaclass=ABCMeta):
    RESPONSE_BACKOFF_STATUS_CODES: frozenset[int] = frozenset()
    RESPONSE_BACKOFF_DELAYS_SECONDS: tuple[int, ...] = ()

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
    ) -> None:
        if session is not None and client is not None:
            raise ValueError("Pass either session or client, not both")
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else (
                AiohttpClient(session=session, owns_session=False)
                if session is not None
                else create_default_http_client()
            )
        )
        self.url_list: list[str] = url_list
        self.cls_name = self.__class__.__name__
        self.name = name
        self.proxy = proxy
        self.ssl_verify = ssl_verify
        self.ssl_ca_cert = ssl_ca_cert
        self.config_request_headers = request_headers or {}
        self.config_cookie = cookie
        self.request_headers = request_headers or {}
        self.cookie_env = cookie_env
        self.cookie = self.resolve_cookie(cookie, cookie_env)
        self.request_cookies = self._parse_cookie_header(self.cookie)
        self.logger = logging.getLogger(f"crawler.{self.__class__.__name__}")
        self._prev_status = 200
        self._response_backoff_failures: dict[str, int] = {}
        self._response_backoff_until: dict[str, float] = {}
        self._response_backoff_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def resolve_cookie(cookie: str | None, cookie_env: str | None) -> str:
        if cookie is not None:
            return cookie
        if cookie_env:
            return os.getenv(cookie_env, "")
        return ""

    @staticmethod
    def _parse_cookie_header(cookie_header: str) -> dict[str, str]:
        if not cookie_header.strip():
            return {}

        cookie = SimpleCookie()
        cookie.load(cookie_header)
        return {key: morsel.value for key, morsel in cookie.items()}

    async def get(self) -> ArticleCollection:
        """게시글 데이터를 크롤링 및 파싱하여 ArticleCollection 객체로 반환

        Returns:
            ArticleCollection: 게시글 목록
        """
        with logfire.span(
            f"crawler_get_{self.name}", crawler_name=self.__class__.__name__, url_count=len(self.url_list)
        ):
            responses = await asyncio.gather(
                *(self.request(url) for url in self.url_list),
                return_exceptions=True,
            )
            html_list: list[str] = []
            for url, result in zip(self.url_list, responses):
                if isinstance(result, Exception):
                    self.logger.error("Request failed: %s (%s)", result, url)
                    continue
                if result:
                    html_list.append(result)

            data = ArticleCollection()
            for html in html_list:
                data.update(await self.parsing(html))

            logfire.info(
                "Crawler completed",
                crawler_name=self.__class__.__name__,
                urls_processed=len(html_list),
                articles_found=len(data),
            )

            return data

    async def _request(self, url: str) -> HttpResponse | None:
        """Forward crawler-specific settings through the shared HTTP transport."""
        self.logger.debug("Send request to %s", url)
        request_kwargs: dict[str, Any] = {"allow_redirects": False}
        if self.proxy is not None:
            request_kwargs["proxy"] = self.proxy
        if not self.ssl_verify:
            request_kwargs["verify"] = False
        elif self.ssl_ca_cert:
            request_kwargs["verify"] = self.ssl_ca_cert
        if self.request_headers:
            request_kwargs["headers"] = self.request_headers
        if self.request_cookies:
            request_kwargs["cookies"] = self.request_cookies
        try:
            return await self.client.get(url, **request_kwargs)
        except HttpTimeoutError as e:
            self.logger.error("HTTP request timeout error: %s (%s)", e, url)
        except HttpClientError as e:
            self.logger.error("HTTP client error: %s (%s)", e, url)
        return None

    async def request(self, url: str) -> str | None:
        """주어진 URL로부터 HTML 문자열을 반환

        Args:
            url (str): 요청할 URL

        Returns:
            str | None: HTML 문자열 (실패한 경우 None 반환)
        """
        if not self.RESPONSE_BACKOFF_STATUS_CODES or not self.RESPONSE_BACKOFF_DELAYS_SECONDS:
            return await self._request_with_backoff(url)

        lock = self._response_backoff_locks.setdefault(url, asyncio.Lock())
        async with lock:
            return await self._request_with_backoff(url)

    async def _request_with_backoff(self, url: str) -> str | None:
        """Request one URL after serializing its response-backoff state."""
        if self._response_backoff_until.get(url, 0) > time.monotonic():
            return

        retry_count = 2
        for _ in range(retry_count):
            resp = await self._request(url)
            if resp is not None:
                break
        else:
            self.logger.error("Client connection failed: %s", url)
            return

        if resp.status != 200:
            self._schedule_response_backoff(url, resp.status, resp.headers)
            if resp.status != self._prev_status:
                self.logger.error("Client response error: %s (%s)", resp.status, url)
                await self.dump_http_response(resp)
            else:
                self.logger.info("Client response error [skip]: %s (%s)", resp.status, url)
            self._prev_status = resp.status
            return
        self._prev_status = resp.status
        self._clear_response_backoff(url)

        try:
            return resp.text()
        except (LookupError, UnicodeDecodeError) as e:
            await self.dump_http_response(resp)
            self.logger.error("Cannot decode response body: %s", e)
            return None

    def _schedule_response_backoff(self, url: str, status: int, headers) -> None:
        if status not in self.RESPONSE_BACKOFF_STATUS_CODES or not self.RESPONSE_BACKOFF_DELAYS_SECONDS:
            return

        failure_count = self._response_backoff_failures.get(url, 0) + 1
        self._response_backoff_failures[url] = failure_count
        delay_index = min(failure_count - 1, len(self.RESPONSE_BACKOFF_DELAYS_SECONDS) - 1)
        delay_seconds = self.RESPONSE_BACKOFF_DELAYS_SECONDS[delay_index]

        retry_after = headers.get("Retry-After") if headers is not None else None
        retry_after_seconds = parse_retry_after_seconds(retry_after)
        if retry_after_seconds is not None:
            delay_seconds = max(delay_seconds, retry_after_seconds)

        self._response_backoff_until[url] = time.monotonic() + delay_seconds
        self.logger.warning(
            "Response backoff scheduled: status=%d delay=%ds failures=%d (%s)",
            status,
            delay_seconds,
            failure_count,
            url,
        )

    def _clear_response_backoff(self, url: str) -> None:
        self._response_backoff_failures.pop(url, None)
        self._response_backoff_until.pop(url, None)

    @abstractmethod
    async def parsing(self, html: str) -> dict[int, BaseArticle]:
        """HTML 문자열을 파싱하여 게시글 데이터 목록을 반환

        Args:
            html (str): HTML 문자열

        Returns:
            dict[int, BaseArticle]: 게시글 데이터 목록
        """
        pass

    async def close(self):
        """세션 종료"""
        if self._owns_client and not self.client.closed:
            await self.client.close()

    async def dump_http_response(self, resp: HttpResponse) -> None:
        """HTTP 응답을 error/ 폴더에 'YYYYMMDD_HHMMSS_{crawler_name}.html' 형식으로 저장

        Args:
            resp (HttpResponse): 공통 HTTP 응답 객체
        """
        current_datetime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join("error", f"{current_datetime}_{self.name}.html")

        if not os.path.exists("error"):
            os.makedirs("error")

        with open(filename, "wb") as f:
            f.write(resp.body)
            self.logger.debug("Dumped response binary to %s", filename)

        dumps = sorted(
            os.path.join("error", name)
            for name in os.listdir("error")
            if name.endswith(".html") and os.path.isfile(os.path.join("error", name))
        )
        for old_dump in dumps[:-MAX_ERROR_DUMPS]:
            os.unlink(old_dump)
