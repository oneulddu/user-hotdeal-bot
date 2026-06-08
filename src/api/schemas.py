"""Pydantic schemas for API request/response models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ArticleBase(BaseModel):
    """Base article schema."""

    article_id: int
    crawler_name: str
    title: str
    category: str
    site_name: str
    board_name: str
    writer_name: str
    url: str
    is_end: bool
    extra: dict


class ArticleResponse(ArticleBase):
    """Article response schema with database fields."""

    model_config = ConfigDict(from_attributes=True)

    id: str  # ULID (26자 문자열, 시간순 정렬 가능)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ArticleListResponse(BaseModel):
    """Paginated list of articles."""

    data: list[ArticleResponse]
    meta: "ArticleListMeta"


class ArticleListMeta(BaseModel):
    """Metadata for article list response."""

    total: int
    limit: int
    offset: int
    has_more: bool


class CrawlerInfo(BaseModel):
    """Crawler information."""

    name: str
    article_count: int = 0


class CrawlerListResponse(BaseModel):
    """List of crawlers."""

    data: list[CrawlerInfo]


class SiteInfo(BaseModel):
    """Site information."""

    name: str
    article_count: int = 0


class SiteListResponse(BaseModel):
    """List of sites."""

    data: list[SiteInfo]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str
