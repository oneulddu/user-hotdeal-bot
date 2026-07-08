"""Repository layer for database CRUD operations."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiKey, ApiKeyRateLimit, Article, GuestRateLimit, Settings
from .time import utc_now


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

    async def bulk_soft_delete(self, keys: list[tuple[str, int]]) -> int:
        """Soft delete articles by crawler_name and article_id in batches."""
        if not keys:
            return 0

        grouped: dict[str, list[int]] = {}
        for crawler_name, article_id in keys:
            grouped.setdefault(crawler_name, []).append(article_id)

        deleted_at = utc_now()
        deleted_count = 0
        for crawler_name, article_ids in grouped.items():
            result = await self.session.execute(
                update(Article)
                .where(
                    Article.crawler_name == crawler_name,
                    Article.article_id.in_(article_ids),
                    Article.deleted_at.is_(None),
                )
                .values(deleted_at=deleted_at)
            )
            deleted_count += result.rowcount or 0

        await self.session.flush()
        return deleted_count

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

    async def update_last_used(self, api_key: ApiKey) -> None:
        """Update last_used_at for an API key.

        Args:
            api_key: Already loaded API key instance
        """
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


class _WindowRateLimitRepository:
    """Shared fixed-window rate limit repository implementation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def model(self) -> type[GuestRateLimit] | type[ApiKeyRateLimit]:
        raise NotImplementedError

    @property
    def key_name(self) -> str:
        raise NotImplementedError

    @property
    def key_column(self) -> ColumnElement[Any]:
        return getattr(self.model, self.key_name)

    async def _ensure_row(self, key: str | int, now: datetime) -> None:
        """Create a rate-limit row if it does not already exist."""
        values = {
            self.key_name: key,
            "request_count": 0,
            "window_start": now,
        }

        if _is_mysql_session(self.session):
            stmt = mysql_insert(self.model).values(**values)
            stmt = stmt.on_duplicate_key_update(**{self.key_name: getattr(stmt.inserted, self.key_name)})
        else:
            stmt = sqlite_insert(self.model).values(**values)
            stmt = stmt.on_conflict_do_nothing(index_elements=[self.key_name])

        await self.session.execute(stmt)

    async def _increment_active_window(self, key: str | int, cutoff: datetime, limit_per_minute: int) -> bool:
        result = await self.session.execute(
            update(self.model)
            .where(
                self.key_column == key,
                self.model.window_start >= cutoff,
                self.model.request_count < limit_per_minute,
            )
            .values(request_count=self.model.request_count + 1)
        )
        return bool(result.rowcount)

    async def _reset_expired_window(self, key: str | int, cutoff: datetime, now: datetime) -> bool:
        result = await self.session.execute(
            update(self.model)
            .where(
                self.key_column == key,
                self.model.window_start < cutoff,
            )
            .values(request_count=1, window_start=now)
        )
        return bool(result.rowcount)

    async def _check_and_increment_key(self, key: str | int, limit_per_minute: int) -> bool:
        now = utc_now()
        cutoff = now - timedelta(minutes=1)

        await self._ensure_row(key, now)

        if await self._increment_active_window(key, cutoff, limit_per_minute):
            await self.session.flush()
            return True

        if await self._reset_expired_window(key, cutoff, now):
            await self.session.flush()
            return True

        # Another request may have reset the expired window between the first
        # increment attempt and our reset attempt. Try the fresh window once.
        allowed = await self._increment_active_window(key, cutoff, limit_per_minute)
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
        result = await self.session.execute(delete(self.model).where(self.model.window_start < cutoff))
        await self.session.flush()
        return result.rowcount or 0


class GuestRateLimitRepository(_WindowRateLimitRepository):
    """Repository for guest rate limiting."""

    @property
    def model(self) -> type[GuestRateLimit]:
        return GuestRateLimit

    @property
    def key_name(self) -> str:
        return "ip_address"

    async def check_and_increment(self, ip_address: str, limit_per_minute: int) -> bool:
        """Check if IP is within rate limit and increment counter."""
        return await self._check_and_increment_key(ip_address, limit_per_minute)


class ApiKeyRateLimitRepository(_WindowRateLimitRepository):
    """Repository for API key rate limiting."""

    @property
    def model(self) -> type[ApiKeyRateLimit]:
        return ApiKeyRateLimit

    @property
    def key_name(self) -> str:
        return "api_key_id"

    async def check_and_increment(self, api_key_id: int, limit_per_minute: int) -> bool:
        """Check if API key is within rate limit and increment counter."""
        return await self._check_and_increment_key(api_key_id, limit_per_minute)


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

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        """Get multiple setting values with one query."""
        if not keys:
            return {}
        result = await self.session.execute(select(Settings).where(Settings.key.in_(keys)))
        return {setting.key: setting.value for setting in result.scalars()}

    @staticmethod
    def parse_int(value: str | None, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    def parse_bool(value: str | None, default: bool = False) -> bool:
        if value is None:
            return default
        return value.lower() in ("true", "1", "yes", "on")

    async def get_int(self, key: str, default: int = 0) -> int:
        """Get a setting value as integer.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value as int
        """
        return self.parse_int(await self.get(key), default)

    async def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a setting value as boolean.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value as bool
        """
        return self.parse_bool(await self.get(key), default)

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
