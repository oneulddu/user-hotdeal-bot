"""RSS Feed routes."""

import time

from fastapi import APIRouter, Query, Response
from feedgen.feed import FeedGenerator

from src.api.deps import ArticleRepo, AuthResult
from src.datetime_utils import as_utc, utc_now
from src.db import Article

router = APIRouter(prefix="/feed", tags=["feed"])
FEED_CACHE_TTL_SECONDS = 60
FEED_CACHE_CONTROL = "public, max-age=60"
_feed_cache: dict[tuple[str, str | None, str | None, int], tuple[float, bytes]] = {}


def _get_cached_feed(cache_key: tuple[str, str | None, str | None, int]) -> bytes | None:
    cached = _feed_cache.get(cache_key)
    if cached is None:
        return None

    expires_at, content = cached
    if expires_at <= time.monotonic():
        _feed_cache.pop(cache_key, None)
        return None
    return content


def _set_cached_feed(cache_key: tuple[str, str | None, str | None, int], content: bytes) -> None:
    _feed_cache[cache_key] = (time.monotonic() + FEED_CACHE_TTL_SECONDS, content)


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


async def _list_feed_articles(
    repo: ArticleRepo,
    crawler: str | None,
    site: str | None,
    limit: int,
) -> list[Article]:
    articles, _ = await repo.list_articles(
        crawler=crawler,
        site=site,
        is_end=False,
        include_deleted=False,
        limit=limit,
        offset=0,
    )
    return articles


def _fill_entry_common(fe, article: Article) -> None:
    fe.id(str(article.id))
    fe.title(article.title)
    fe.link(href=article.url)
    fe.author(name=article.writer_name)
    fe.published(as_utc(article.created_at))
    fe.updated(as_utc(article.updated_at))

    if article.category:
        fe.category(term=article.category)


@router.get("/rss.xml", response_class=Response)
async def get_rss_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get RSS 2.0 feed of hot deals."""
    cache_key = ("rss", crawler, site, limit)
    if cached_feed := _get_cached_feed(cache_key):
        return _feed_response(cached_feed, "application/rss+xml; charset=utf-8")

    articles = await _list_feed_articles(repo, crawler, site, limit)
    fg = _create_feed_generator(_feed_title(crawler, site))

    for article in articles:
        fe = fg.add_entry()
        _fill_entry_common(fe, article)
        fe.description(f"[{article.category}] {article.title}")

    # Generate RSS XML
    rss_xml = fg.rss_str(pretty=True)
    _set_cached_feed(cache_key, rss_xml)

    return _feed_response(rss_xml, "application/rss+xml; charset=utf-8")


@router.get("/atom.xml", response_class=Response)
async def get_atom_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get Atom feed of hot deals."""
    cache_key = ("atom", crawler, site, limit)
    if cached_feed := _get_cached_feed(cache_key):
        return _feed_response(cached_feed, "application/atom+xml; charset=utf-8")

    articles = await _list_feed_articles(repo, crawler, site, limit)
    fg = _create_feed_generator(_feed_title(crawler, site))
    fg.id("https://t.me/hotdeal_kr")

    for article in articles:
        fe = fg.add_entry()
        _fill_entry_common(fe, article)
        fe.content(f"[{article.category}] {article.title}", type="text")

    # Generate Atom XML
    atom_xml = fg.atom_str(pretty=True)
    _set_cached_feed(cache_key, atom_xml)

    return _feed_response(atom_xml, "application/atom+xml; charset=utf-8")
