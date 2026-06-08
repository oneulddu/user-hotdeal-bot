"""Article routes."""

from fastapi import APIRouter, HTTPException, Query, status

from src.api.deps import ArticleRepo, AuthResult
from src.api.schemas import ArticleListMeta, ArticleListResponse, ArticleResponse

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("", response_model=ArticleListResponse)
async def list_articles(
    _auth: AuthResult,
    repo: ArticleRepo,
    after: str | None = Query(None, description="Return articles with ID (ULID) greater than this"),
    crawler: str | None = Query(None, description="Filter by crawler name"),
    site: str | None = Query(None, description="Filter by site name"),
    is_end: bool | None = Query(None, description="Filter by is_end status"),
    include_deleted: bool = Query(False, description="Include soft-deleted articles"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
) -> ArticleListResponse:
    """Get list of articles with optional filtering."""
    articles, total = await repo.list_articles(
        after=after,
        crawler=crawler,
        site=site,
        is_end=is_end,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )

    has_more = offset + len(articles) < total

    return ArticleListResponse(
        data=[ArticleResponse.model_validate(a) for a in articles],
        meta=ArticleListMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        ),
    )


@router.get("/{article_id}", response_model=ArticleResponse)
async def get_article(
    article_id: str,
    _auth: AuthResult,
    repo: ArticleRepo,
) -> ArticleResponse:
    """Get a single article by ID (ULID)."""
    article = await repo.get_by_id(article_id)
    if article is None or article.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Article with id {article_id} not found",
        )
    return ArticleResponse.model_validate(article)
