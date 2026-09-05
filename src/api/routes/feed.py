"""RSS Feed routes."""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Query, Response
from feedgen.feed import FeedGenerator

from src.api.deps import ArticleRepo, AuthResult
from src.datetime_utils import as_utc, utc_now
from src.db import Article

router = APIRouter(prefix="/feed", tags=["feed"])
FEED_CACHE_TTL_SECONDS = 60
FEED_CACHE_CONTROL = "public, max-age=60"
FEED_CACHE_MAX_SIZE = 128
FeedFormat = Literal["rss", "atom"]
FeedCacheKey = tuple[FeedFormat, str | None, str | None, int]
_feed_cache: dict[FeedCacheKey, tuple[float, bytes]] = {}


@dataclass
class _FeedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_feed_locks: dict[FeedCacheKey, _FeedLock] = {}


@asynccontextmanager
async def _lock_feed(cache_key: FeedCacheKey):
    entry = _feed_locks.get(cache_key)
    if entry is None:
        entry = _FeedLock()
        _feed_locks[cache_key] = entry
    # Count both the owner and waiting requests before yielding control.
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0:
            # Explicit cleanup also handles exceptions retaining tracebacks.
            _feed_locks.pop(cache_key, None)


def _prune_feed_cache(now: float) -> None:
    expired_keys = [cache_key for cache_key, (expires_at, _content) in _feed_cache.items() if expires_at <= now]
    for cache_key in expired_keys:
        _feed_cache.pop(cache_key, None)

    while len(_feed_cache) >= FEED_CACHE_MAX_SIZE:
        oldest_key = min(_feed_cache, key=lambda cache_key: _feed_cache[cache_key][0])
        _feed_cache.pop(oldest_key, None)


def _get_cached_feed(cache_key: FeedCacheKey) -> bytes | None:
    cached = _feed_cache.get(cache_key)
    if cached is None:
        return None

    expires_at, content = cached
    if expires_at <= time.monotonic():
        _feed_cache.pop(cache_key, None)
        return None
    return content


def _set_cached_feed(cache_key: FeedCacheKey, content: bytes) -> None:
    now = time.monotonic()
    _prune_feed_cache(now)
    _feed_cache[cache_key] = (now + FEED_CACHE_TTL_SECONDS, content)


def _feed_response(content: bytes, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": FEED_CACHE_CONTROL},
    )


def _create_feed_generator(title: str = "핫딜 모아보기") -> FeedGenerator:
    """Create a base feed generator with common settings."""
    fg = FeedGenerator()
    fg.title(title)
    fg.link(href="https://t.me/hotdeal_kr", rel="alternate")
    fg.description("한국 커뮤니티 핫딜 모아보기")
    fg.language("ko")
    fg.generator("user-hotdeal-bot")
    fg.lastBuildDate(as_utc(utc_now()))
    return fg


def _feed_title(crawler: str | None, site: str | None) -> str:
    title = "핫딜 모아보기"
    if crawler:
        return f"{title} - {crawler}"
    if site:
        return f"{title} - {site}"
    return title


def _fill_entry_common(fe, article: Article) -> None:
    fe.id(str(article.id))
    fe.title(article.title)
    fe.link(href=article.url)
    fe.author(name=article.writer_name)
    fe.published(as_utc(article.created_at))
    fe.updated(as_utc(article.updated_at))

    if article.category:
        fe.category(term=article.category)


async def _get_feed(
    feed_format: FeedFormat, repo: ArticleRepo, crawler: str | None, site: str | None, limit: int
) -> Response:
    cache_key = (feed_format, crawler, site, limit)
    media_type = f"application/{feed_format}+xml; charset=utf-8"
    cached = _get_cached_feed(cache_key)
    if cached is not None:
        return _feed_response(cached, media_type)

    async with _lock_feed(cache_key):
        # Another request may have filled this key while we waited.
        cached = _get_cached_feed(cache_key)
        if cached is not None:
            return _feed_response(cached, media_type)

        articles = await repo.list_feed_articles(crawler=crawler, site=site, limit=limit)
        fg = _create_feed_generator(_feed_title(crawler, site))
        if feed_format == "atom":
            fg.id("https://t.me/hotdeal_kr")
        for article in articles:
            fe = fg.add_entry()
            _fill_entry_common(fe, article)
            description = f"[{article.category}] {article.title}"
            if feed_format == "rss":
                fe.description(description)
            else:
                fe.content(description, type="text")

        content = fg.rss_str(pretty=True) if feed_format == "rss" else fg.atom_str(pretty=True)
        _set_cached_feed(cache_key, content)
        return _feed_response(content, media_type)


@router.get("/rss.xml", response_class=Response)
async def get_rss_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get RSS 2.0 feed of hot deals."""
    return await _get_feed("rss", repo, crawler, site, limit)


@router.get("/atom.xml", response_class=Response)
async def get_atom_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get Atom feed of hot deals."""
    return await _get_feed("atom", repo, crawler, site, limit)
