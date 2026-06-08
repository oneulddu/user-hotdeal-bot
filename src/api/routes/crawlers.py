"""Crawler routes."""

from fastapi import APIRouter

from src.api.deps import ArticleRepo, AuthResult
from src.api.schemas import CrawlerInfo, CrawlerListResponse, SiteInfo, SiteListResponse

router = APIRouter(prefix="/crawlers", tags=["crawlers"])


@router.get("", response_model=CrawlerListResponse)
async def list_crawlers(
    _auth: AuthResult,
    repo: ArticleRepo,
) -> CrawlerListResponse:
    """Get list of available crawlers with article counts."""
    crawlers = [CrawlerInfo(name=name, article_count=count) for name, count in await repo.count_by_crawler()]

    return CrawlerListResponse(data=crawlers)


@router.get("/sites", response_model=SiteListResponse)
async def list_sites(
    _auth: AuthResult,
    repo: ArticleRepo,
) -> SiteListResponse:
    """Get list of available sites with article counts."""
    sites = [SiteInfo(name=name, article_count=count) for name, count in await repo.count_by_site()]

    return SiteListResponse(data=sites)
