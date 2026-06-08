"""RSS Feed routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Response
from feedgen.feed import FeedGenerator

from src.api.deps import ArticleRepo, AuthResult

router = APIRouter(prefix="/feed", tags=["feed"])


def _create_feed_generator(title: str = "핫딜 모아보기") -> FeedGenerator:
    """Create a base feed generator with common settings."""
    fg = FeedGenerator()
    fg.title(title)
    fg.link(href="https://t.me/hotdeal_kr", rel="alternate")
    fg.description("한국 커뮤니티 핫딜 모아보기")
    fg.language("ko")
    fg.generator("user-hotdeal-bot")
    fg.lastBuildDate(datetime.now(timezone.utc))
    return fg


@router.get("/rss.xml", response_class=Response)
async def get_rss_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get RSS 2.0 feed of hot deals."""
    # Fetch articles
    articles, _ = await repo.list_articles(
        crawler=crawler,
        site=site,
        is_end=False,  # Only active deals
        include_deleted=False,
        limit=limit,
        offset=0,
    )

    # Create feed
    title = "핫딜 모아보기"
    if crawler:
        title = f"핫딜 모아보기 - {crawler}"
    elif site:
        title = f"핫딜 모아보기 - {site}"

    fg = _create_feed_generator(title)

    # Add entries
    for article in articles:
        fe = fg.add_entry()
        fe.id(str(article.id))
        fe.title(article.title)
        fe.link(href=article.url)
        fe.description(f"[{article.category}] {article.title}")
        fe.author(name=article.writer_name)
        fe.published(article.created_at.replace(tzinfo=timezone.utc))
        fe.updated(article.updated_at.replace(tzinfo=timezone.utc))

        # Add category
        if article.category:
            fe.category(term=article.category)

    # Generate RSS XML
    rss_xml = fg.rss_str(pretty=True)

    return Response(
        content=rss_xml,
        media_type="application/rss+xml; charset=utf-8",
    )


@router.get("/atom.xml", response_class=Response)
async def get_atom_feed(
    _auth: AuthResult,
    repo: ArticleRepo,
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    limit: int = Query(50, ge=1, le=100, description="Number of items in feed"),
) -> Response:
    """Get Atom feed of hot deals."""
    # Fetch articles
    articles, _ = await repo.list_articles(
        crawler=crawler,
        site=site,
        is_end=False,
        include_deleted=False,
        limit=limit,
        offset=0,
    )

    # Create feed
    title = "핫딜 모아보기"
    if crawler:
        title = f"핫딜 모아보기 - {crawler}"
    elif site:
        title = f"핫딜 모아보기 - {site}"

    fg = _create_feed_generator(title)
    fg.id("https://t.me/hotdeal_kr")

    # Add entries
    for article in articles:
        fe = fg.add_entry()
        fe.id(str(article.id))
        fe.title(article.title)
        fe.link(href=article.url)
        fe.content(f"[{article.category}] {article.title}", type="text")
        fe.author(name=article.writer_name)
        fe.published(article.created_at.replace(tzinfo=timezone.utc))
        fe.updated(article.updated_at.replace(tzinfo=timezone.utc))

        if article.category:
            fe.category(term=article.category)

    # Generate Atom XML
    atom_xml = fg.atom_str(pretty=True)

    return Response(
        content=atom_xml,
        media_type="application/atom+xml; charset=utf-8",
    )
