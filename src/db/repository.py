"""Repository layer for database CRUD operations."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.datetime_utils import utc_now

from .models import ApiKey, ApiKeyRateLimit, Article, GuestRateLimit, Settings


def _is_mysql_session(session: AsyncSession) -> bool:
    """Check if the session is using MySQL/MariaDB dialect."""
    if session.bind is None:
        return False
    # AsyncEngine uses sync_engine to access dialect
    engine = session.bind
    dialect_name = engine.dialect.name
    return dialect_name in ("mysql", "mariadb")


class ArticleRepository:
    """Repository for Article CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, article_data: dict[str, Any]) -> Article:
        """Create a new article.

        Args:
            article_data: Dictionary with article fields matching BaseArticle

        Returns:
            Created Article instance
        """
        article = Article(**article_data)
        self.session.add(article)
        await self.session.flush()
        return article

    async def upsert(self, article_data: dict[str, Any]) -> Article:
        """Insert or update an article based on crawler_name + article_id.

        Args:
            article_data: Dictionary with article fields

        Returns:
            Upserted Article instance
        """
        await self.bulk_upsert([article_data])

        # Fetch the upserted record
        result = await self.session.execute(
            select(Article).where(
                Article.crawler_name == article_data["crawler_name"],
                Article.article_id == article_data["article_id"],
            )
        )
        return result.scalar_one()

    async def bulk_upsert(self, articles: list[dict[str, Any]]) -> int:
        """Bulk insert or update articles.

        Args:
            articles: List of article dictionaries

        Returns:
            Number of articles processed
        """
        if not articles:
            return 0

        now = utc_now()
        if _is_mysql_session(self.session):
            stmt = mysql_insert(Article).values(articles)
            stmt = stmt.on_duplicate_key_update(
                title=stmt.inserted.title,
                category=stmt.inserted.category,
                site_name=stmt.inserted.site_name,
                board_name=stmt.inserted.board_name,
                writer_name=stmt.inserted.writer_name,
                url=stmt.inserted.url,
                is_end=stmt.inserted.is_end,
                extra=stmt.inserted.extra,
                updated_at=now,
                deleted_at=None,
            )
        else:
            stmt = sqlite_insert(Article).values(articles)
            stmt = stmt.on_conflict_do_update(
                index_elements=["crawler_name", "article_id"],
                set_={
                    "title": stmt.excluded.title,
                    "category": stmt.excluded.category,
                    "site_name": stmt.excluded.site_name,
                    "board_name": stmt.excluded.board_name,
                    "writer_name": stmt.excluded.writer_name,
                    "url": stmt.excluded.url,
                    "is_end": stmt.excluded.is_end,
                    "extra": stmt.excluded.extra,
                    "updated_at": now,
                    "deleted_at": None,
                },
            )

        await self.session.execute(stmt)
        await self.session.flush()

        return len(articles)

    async def get_by_id(self, article_id: str) -> Article | None:
        """Get article by primary key ID (ULID).

        Args:
            article_id: Primary key ID (ULID string)

        Returns:
            Article or None if not found
        """
        result = await self.session.execute(select(Article).where(Article.id == article_id))
        return result.scalar_one_or_none()

    async def get_by_crawler_and_article_id(self, crawler_name: str, article_id: int) -> Article | None:
        """Get article by crawler_name and original article_id.

        Args:
            crawler_name: Crawler identifier
            article_id: Original site's article ID

        Returns:
            Article or None if not found
        """
        result = await self.session.execute(
            select(Article).where(
                Article.crawler_name == crawler_name,
                Article.article_id == article_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_articles(
        self,
        after: str | None = None,
        crawler: str | None = None,
        site: str | None = None,
        is_end: bool | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Article], int]:
        """List articles with filtering options.

        Args:
            after: Return articles with ID (ULID) greater than this
            crawler: Filter by crawler_name
            site: Filter by site_name
            is_end: Filter by is_end status
            include_deleted: Include soft-deleted articles
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            Tuple of (articles list, total count)
        """
        query = select(Article)
        count_query = select(func.count(Article.id))

        # Apply filters
        if after is not None:
            query = query.where(Article.id > after)
            count_query = count_query.where(Article.id > after)

        if crawler is not None:
            query = query.where(Article.crawler_name == crawler)
            count_query = count_query.where(Article.crawler_name == crawler)

        if site is not None:
            query = query.where(Article.site_name == site)
            count_query = count_query.where(Article.site_name == site)

        if is_end is not None:
            query = query.where(Article.is_end == is_end)
            count_query = count_query.where(Article.is_end == is_end)

        if not include_deleted:
            query = query.where(Article.deleted_at.is_(None))
            count_query = count_query.where(Article.deleted_at.is_(None))

        # Order by ID desc (newest first)
        query = query.order_by(Article.id.desc())

        # Pagination
        query = query.offset(offset).limit(limit)

        # Execute queries
        result = await self.session.execute(query)
        articles = list(result.scalars().all())

        count_result = await self.session.execute(count_query)
        total = count_result.scalar_one()

        return articles, total

    async def soft_delete(self, article_id: str) -> bool:
        """Soft delete an article by setting deleted_at.

        Args:
            article_id: Primary key ID (ULID string)

        Returns:
            True if deleted, False if not found
        """
        article = await self.get_by_id(article_id)
        if article is None:
            return False

        article.deleted_at = utc_now()
        await self.session.flush()
        return True

    async def soft_delete_by_crawler(self, crawler_name: str, article_id: int) -> bool:
        """Soft delete an article by crawler_name and article_id.

        Args:
            crawler_name: Crawler identifier
            article_id: Original site's article ID

        Returns:
            True if deleted, False if not found
        """
        article = await self.get_by_crawler_and_article_id(crawler_name, article_id)
        if article is None:
            return False

        article.deleted_at = utc_now()
        await self.session.flush()
        return True

    async def get_distinct_crawlers(self) -> list[str]:
        """Get list of distinct crawler names.

        Returns:
            List of crawler names
        """
        result = await self.session.execute(
            select(Article.crawler_name).distinct().where(Article.deleted_at.is_(None)).order_by(Article.crawler_name)
        )
        return list(result.scalars().all())

    async def get_distinct_sites(self) -> list[str]:
        """Get list of distinct site names.

        Returns:
            List of site names
        """
        result = await self.session.execute(
            select(Article.site_name).distinct().where(Article.deleted_at.is_(None)).order_by(Article.site_name)
        )
        return list(result.scalars().all())

    async def count_by_crawler(self) -> list[tuple[str, int]]:
        """Count active articles grouped by crawler name."""
        return await self._count_active_by(Article.crawler_name)

    async def count_by_site(self) -> list[tuple[str, int]]:
        """Count active articles grouped by site name."""
        return await self._count_active_by(Article.site_name)

    async def _count_active_by(self, column: ColumnElement[str]) -> list[tuple[str, int]]:
        stmt = (
            select(column.label("name"), func.count(Article.id).label("count"))
            .where(Article.deleted_at.is_(None))
            .group_by(column)
            .order_by(column)
        )
        result = await self.session.execute(stmt)
        return [(row._mapping["name"], row._mapping["count"]) for row in result.all()]


class ApiKeyRepository:
    """Repository for API Key operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_key(self, key: str) -> ApiKey | None:
        """Get API key by the key string.

        Args:
            key: API key string

        Returns:
            ApiKey or None if not found
        """
        result = await self.session.execute(select(ApiKey).where(ApiKey.key == key, ApiKey.is_active.is_(True)))
        return result.scalar_one_or_none()

    async def update_last_used(self, key: str) -> None:
        """Update last_used_at for an API key.

        Args:
            key: API key string
        """
        api_key = await self.get_by_key(key)
        if api_key:
            api_key.last_used_at = utc_now()
            await self.session.flush()

    async def create(self, key: str, name: str, rate_limit_per_minute: int = 60) -> ApiKey:
        """Create a new API key.

        Args:
            key: API key string
            name: Name/description for the key
            rate_limit_per_minute: Rate limit

        Returns:
            Created ApiKey instance
        """
        api_key = ApiKey(key=key, name=name, rate_limit_per_minute=rate_limit_per_minute)
        self.session.add(api_key)
        await self.session.flush()
        return api_key


class GuestRateLimitRepository:
    """Repository for guest rate limiting."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _ensure_row(self, ip_address: str, now: datetime) -> None:
        """Create a rate-limit row if it does not already exist."""
        values = {
            "ip_address": ip_address,
            "request_count": 0,
            "window_start": now,
        }

        if _is_mysql_session(self.session):
            stmt = mysql_insert(GuestRateLimit).values(**values)
            stmt = stmt.on_duplicate_key_update(ip_address=stmt.inserted.ip_address)
        else:
            stmt = sqlite_insert(GuestRateLimit).values(**values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["ip_address"])

        await self.session.execute(stmt)

    async def _increment_active_window(self, ip_address: str, cutoff: datetime, limit_per_minute: int) -> bool:
        result = await self.session.execute(
            update(GuestRateLimit)
            .where(
                GuestRateLimit.ip_address == ip_address,
                GuestRateLimit.window_start >= cutoff,
                GuestRateLimit.request_count < limit_per_minute,
            )
            .values(request_count=GuestRateLimit.request_count + 1)
        )
        return bool(result.rowcount)

    async def _reset_expired_window(self, ip_address: str, cutoff: datetime, now: datetime) -> bool:
        result = await self.session.execute(
            update(GuestRateLimit)
            .where(
                GuestRateLimit.ip_address == ip_address,
                GuestRateLimit.window_start < cutoff,
            )
            .values(request_count=1, window_start=now)
        )
        return bool(result.rowcount)

    async def check_and_increment(self, ip_address: str, limit_per_minute: int) -> bool:
        """Check if IP is within rate limit and increment counter.

        Args:
            ip_address: Client IP address
            limit_per_minute: Maximum requests per minute

        Returns:
            True if within limit, False if exceeded
        """
        now = utc_now()
        cutoff = now - timedelta(minutes=1)

        await self._ensure_row(ip_address, now)

        if await self._increment_active_window(ip_address, cutoff, limit_per_minute):
            await self.session.flush()
            return True

        if await self._reset_expired_window(ip_address, cutoff, now):
            await self.session.flush()
            return True

        # Another request may have reset the expired window between the first
        # increment attempt and our reset attempt. Try the fresh window once.
        allowed = await self._increment_active_window(ip_address, cutoff, limit_per_minute)
        await self.session.flush()
        return allowed

    async def cleanup_old_records(self, older_than_minutes: int = 60) -> int:
        """Remove old rate limit records.

        Args:
            older_than_minutes: Remove records older than this

        Returns:
            Number of records deleted
        """
        cutoff = utc_now() - timedelta(minutes=older_than_minutes)
        result = await self.session.execute(delete(GuestRateLimit).where(GuestRateLimit.window_start < cutoff))
        await self.session.flush()
        return result.rowcount or 0


class ApiKeyRateLimitRepository:
    """Repository for API key rate limiting."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _ensure_row(self, api_key_id: int, now: datetime) -> None:
        """Create a rate-limit row if it does not already exist."""
        values = {
            "api_key_id": api_key_id,
            "request_count": 0,
            "window_start": now,
        }

        if _is_mysql_session(self.session):
            stmt = mysql_insert(ApiKeyRateLimit).values(**values)
            stmt = stmt.on_duplicate_key_update(api_key_id=stmt.inserted.api_key_id)
        else:
            stmt = sqlite_insert(ApiKeyRateLimit).values(**values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["api_key_id"])

        await self.session.execute(stmt)

    async def _increment_active_window(self, api_key_id: int, cutoff: datetime, limit_per_minute: int) -> bool:
        result = await self.session.execute(
            update(ApiKeyRateLimit)
            .where(
                ApiKeyRateLimit.api_key_id == api_key_id,
                ApiKeyRateLimit.window_start >= cutoff,
                ApiKeyRateLimit.request_count < limit_per_minute,
            )
            .values(request_count=ApiKeyRateLimit.request_count + 1)
        )
        return bool(result.rowcount)

    async def _reset_expired_window(self, api_key_id: int, cutoff: datetime, now: datetime) -> bool:
        result = await self.session.execute(
            update(ApiKeyRateLimit)
            .where(
                ApiKeyRateLimit.api_key_id == api_key_id,
                ApiKeyRateLimit.window_start < cutoff,
            )
            .values(request_count=1, window_start=now)
        )
        return bool(result.rowcount)

    async def check_and_increment(self, api_key_id: int, limit_per_minute: int) -> bool:
        """Check if API key is within rate limit and increment counter.

        Args:
            api_key_id: API key ID
            limit_per_minute: Maximum requests per minute

        Returns:
            True if within limit, False if exceeded
        """
        now = utc_now()
        cutoff = now - timedelta(minutes=1)

        await self._ensure_row(api_key_id, now)

        if await self._increment_active_window(api_key_id, cutoff, limit_per_minute):
            await self.session.flush()
            return True

        if await self._reset_expired_window(api_key_id, cutoff, now):
            await self.session.flush()
            return True

        # Another request may have reset the expired window between the first
        # increment attempt and our reset attempt. Try the fresh window once.
        allowed = await self._increment_active_window(api_key_id, cutoff, limit_per_minute)
        await self.session.flush()
        return allowed

    async def cleanup_old_records(self, older_than_minutes: int = 60) -> int:
        """Remove old rate limit records.

        Args:
            older_than_minutes: Remove records older than this

        Returns:
            Number of records deleted
        """
        cutoff = utc_now() - timedelta(minutes=older_than_minutes)
        result = await self.session.execute(delete(ApiKeyRateLimit).where(ApiKeyRateLimit.window_start < cutoff))
        await self.session.flush()
        return result.rowcount or 0


class SettingsRepository:
    """Repository for application settings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str, default: str | None = None) -> str | None:
        """Get a setting value.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        result = await self.session.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting else default

    async def get_int(self, key: str, default: int = 0) -> int:
        """Get a setting value as integer.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value as int
        """
        value = await self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    async def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a setting value as boolean.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value as bool
        """
        value = await self.get(key)
        if value is None:
            return default
        return value.lower() in ("true", "1", "yes", "on")

    async def set(self, key: str, value: str, description: str | None = None) -> Settings:
        """Set a setting value (upsert).

        Args:
            key: Setting key
            value: Setting value
            description: Optional description

        Returns:
            Settings instance
        """
        result = await self.session.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()

        if setting:
            setting.value = value
            if description is not None:
                setting.description = description
        else:
            setting = Settings(key=key, value=value, description=description)
            self.session.add(setting)

        await self.session.flush()
        return setting
