import asyncio
import ssl
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from email.message import Message
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import aiohttp
from aiohttp.resolver import AsyncResolver
from curl_cffi import AsyncSession, CurlOpt
from curl_cffi.requests.exceptions import Timeout as CurlTimeout
from multidict import CIMultiDict, CIMultiDictProxy

CLOUDFLARE_DNS_SERVERS = ("1.1.1.1", "1.0.0.1")
CLOUDFLARE_DOH_URL = "https://cloudflare-dns.com/dns-query"
DNS_CACHE_TTL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 20


class HttpClientError(Exception):
    """HTTP 요청 처리 중 발생한 전송 계층 오류."""


class HttpTimeoutError(HttpClientError):
    """HTTP 요청 제한 시간을 초과한 경우."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """HTTP 클라이언트 구현과 무관한 공통 응답."""

    status: int
    body: bytes
    headers: Mapping[str, str]
    url: str
    charset: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", CIMultiDictProxy(CIMultiDict(self.headers)))
        if self.charset is None:
            content_type = Message()
            content_type["Content-Type"] = self.headers.get("Content-Type", "")
            object.__setattr__(self, "charset", content_type.get_content_charset())

    def text(self) -> str:
        """응답 본문을 선언된 문자 인코딩으로 디코딩한다."""
        encoding = self.charset or "utf-8"
        if encoding.lower().replace("_", "-") == "euc-kr":
            encoding = "cp949"
        return self.body.decode(encoding)


class HttpClient(Protocol):
    """크롤러가 사용하는 비동기 HTTP 클라이언트 계약."""

    @property
    def closed(self) -> bool: ...

    async def get(
        self,
        url: str,
        *,
        allow_redirects: bool = False,
        proxy: str | None = None,
        verify: bool | str = True,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...

    async def close(self) -> None: ...


class CloudflareDNSConnector(aiohttp.TCPConnector):
    """Cloudflare DNS를 사용하고 세션 종료 시 resolver도 함께 닫는 커넥터."""

    def __init__(self, **kwargs: Any) -> None:
        self._cloudflare_resolver = AsyncResolver(nameservers=list(CLOUDFLARE_DNS_SERVERS))
        self._cloudflare_resolver_closed = False
        super().__init__(
            resolver=self._cloudflare_resolver,
            ttl_dns_cache=DNS_CACHE_TTL_SECONDS,
            **kwargs,
        )

    @property
    def dns_resolver(self) -> AsyncResolver:
        return self._cloudflare_resolver

    def close(self) -> Awaitable[None]:
        connector_close = super().close()
        if self._cloudflare_resolver_closed:
            return connector_close

        self._cloudflare_resolver_closed = True

        async def close_connector_and_resolver() -> None:
            try:
                await connector_close
            finally:
                await self._cloudflare_resolver.close()

        return close_connector_and_resolver()


def create_aiohttp_session(**kwargs: Any) -> aiohttp.ClientSession:
    """Cloudflare DNS를 사용하는 aiohttp 세션을 생성한다."""
    return aiohttp.ClientSession(connector=CloudflareDNSConnector(), **kwargs)


class AiohttpClient:
    """aiohttp 기반의 일반 HTTP 클라이언트 구현."""

    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        trust_env: bool = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: aiohttp.ClientSession | None = None,
        owns_session: bool = True,
    ) -> None:
        self._owns_session = owns_session or session is None
        self._ssl_contexts: dict[str, ssl.SSLContext] = {}
        self._session = (
            session
            if session is not None
            else create_aiohttp_session(
                headers=headers,
                trust_env=trust_env,
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
        )

    @property
    def closed(self) -> bool:
        return self._session.closed

    async def get(
        self,
        url: str,
        *,
        allow_redirects: bool = False,
        proxy: str | None = None,
        verify: bool | str = True,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        try:
            options: dict[str, Any] = {"allow_redirects": allow_redirects}
            if proxy is not None:
                options["proxy"] = proxy
            if verify is False:
                options["ssl"] = False
            elif isinstance(verify, str):
                if verify not in self._ssl_contexts:
                    self._ssl_contexts[verify] = ssl.create_default_context(cafile=verify)
                options["ssl"] = self._ssl_contexts[verify]
            if headers:
                options["headers"] = headers
            if cookies:
                options["cookies"] = cookies
            response = await self._session.get(url, **options)
            try:
                body = await response.read()
                return HttpResponse(
                    status=response.status,
                    body=body,
                    headers=response.headers,
                    url=str(response.url),
                    charset=response.charset or response.get_encoding(),
                )
            finally:
                response.release()
        except (aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
            raise HttpTimeoutError(str(e)) from e
        except (aiohttp.ClientError, OSError, ValueError) as e:
            raise HttpClientError(str(e)) from e

    async def close(self) -> None:
        if self._owns_session and not self.closed:
            await self._session.close()


class CurlCffiClient:
    """curl_cffi 기반의 브라우저 지문 위장 HTTP 클라이언트 구현."""

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        trust_env: bool = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: AsyncSession | None = None,
    ) -> None:
        self._closed = False
        self._timeout = timeout
        self._trust_env = trust_env and getattr(session, "trust_env", True)
        self._configured_proxies = dict(getattr(session, "proxies", {}))
        self._session_options = dict(
            impersonate=impersonate,
            trust_env=trust_env,
            timeout=timeout,
            discard_cookies=True,
            curl_options={CurlOpt.DOH_URL: CLOUDFLARE_DOH_URL, CurlOpt.NOPROXY: ""},
        )
        self._injected_session = session is not None
        self._session = session
        self._session_users: dict[AsyncSession, int] = {}
        if session is not None:
            # Resolve environment policy once per request below. libcurl must not
            # independently bypass an explicitly configured proxy via NO_PROXY.
            session.curl_options = {**getattr(session, "curl_options", {}), CurlOpt.NOPROXY: ""}

    @property
    def closed(self) -> bool:
        return self._closed

    def _proxy_for_url(self, url: str, proxy: str | None) -> str:
        if proxy is not None:
            return proxy
        parts = urlsplit(url)
        for key in (f"{parts.scheme}://{parts.hostname}", f"all://{parts.hostname}", parts.scheme, "all"):
            configured = self._configured_proxies.get(key)
            if configured is not None:
                return configured
        if self._trust_env and parts.hostname and not proxy_bypass(parts.hostname):
            proxies = getproxies()
            return proxies.get(parts.scheme, proxies.get("all", ""))
        return ""

    async def get(
        self,
        url: str,
        *,
        allow_redirects: bool = False,
        proxy: str | None = None,
        verify: bool | str = True,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        if self.closed:
            raise HttpClientError("HTTP client is closed")
        if self._session is None:
            self._session = AsyncSession(**self._session_options)
        session = self._session
        self._session_users[session] = self._session_users.get(session, 0) + 1
        try:
            options: dict[str, Any] = {
                "allow_redirects": allow_redirects,
                # A nonempty mapping with an empty proxy explicitly disables libcurl
                # environment proxies; proxy='' alone is ignored by curl-cffi.
                "proxies": {"all": self._proxy_for_url(url, proxy)},
            }
            if verify is not True:
                options["verify"] = verify
            if headers:
                options["headers"] = headers
            if cookies:
                options["cookies"] = cookies
            # curl's native timeout excludes waiting for a free pool slot.
            async with asyncio.timeout(self._timeout):
                response = await session.get(url, **options)
        except (Exception, asyncio.CancelledError) as e:
            # Failed option setup/cancellation can consume a pool slot in curl-cffi.
            # Retire this pool without interrupting its other active requests.
            if self._session is session:
                self._session = None
            if self._injected_session:
                # An injected session cannot be recreated with its caller's settings.
                self._closed = True
            if isinstance(e, asyncio.CancelledError):
                raise
            if isinstance(e, (CurlTimeout, TimeoutError)):
                raise HttpTimeoutError(str(e)) from e
            raise HttpClientError(str(e)) from e
        finally:
            remaining = self._session_users[session] - 1
            if remaining:
                self._session_users[session] = remaining
            else:
                self._session_users.pop(session)
                if session is not self._session:
                    await session.close()

        return HttpResponse(
            status=response.status_code,
            body=response.content,
            headers=response.headers,
            url=str(response.url),
        )

    async def close(self) -> None:
        if self.closed:
            return
        self._closed = True
        session, self._session = self._session, None
        if session is not None and session not in self._session_users:
            await session.close()


def create_default_http_client() -> HttpClient:
    """애플리케이션과 단독 크롤러에서 사용할 기본 HTTP 클라이언트를 생성한다."""
    return CurlCffiClient()
