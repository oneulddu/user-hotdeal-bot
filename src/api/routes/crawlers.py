"""Crawler routes."""

from fastapi import APIRouter
from sqlalchemy import func, select

from src.api.deps import AuthResult, DbSession
from src.api.schemas import CrawlerInfo, CrawlerListResponse, SiteInfo, SiteListResponse
from src.db import Article

router = APIRouter(prefix="/crawlers", tags=["crawlers"])


@router.get("", response_model=CrawlerListResponse)
async def list_crawlers(
    _auth: AuthResult,
    session: DbSession,
) -> CrawlerListResponse:
    """Get list of available crawlers with article counts."""
    # Get distinct crawlers with counts
    stmt = (
        select(Article.crawler_name, func.count(Article.id).label("count"))
        .where(Article.deleted_at.is_(None))
        .group_by(Article.crawler_name)
        .order_by(Article.crawler_name)
    )
    result = await session.execute(stmt)
    rows = result.all()

    crawlers = [CrawlerInfo(name=row.crawler_name, article_count=row.count) for row in rows]

    return CrawlerListResponse(data=crawlers)


@router.get("/sites", response_model=SiteListResponse)
async def list_sites(
    _auth: AuthResult,
    session: DbSession,
) -> SiteListResponse:
    """Get list of available sites with article counts."""
    # Get distinct sites with counts
    stmt = (
        select(Article.site_name, func.count(Article.id).label("count"))
        .where(Article.deleted_at.is_(None))
        .group_by(Article.site_name)
        .order_by(Article.site_name)
    )
    result = await session.execute(stmt)
    rows = result.all()

    sites = [SiteInfo(name=row.site_name, article_count=row.count) for row in rows]

    return SiteListResponse(data=sites)
