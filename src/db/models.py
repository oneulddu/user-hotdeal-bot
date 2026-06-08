"""SQLAlchemy ORM models for user-hotdeal-bot."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class Article(Base):
    """Article model representing a hot deal post."""

    __tablename__ = "articles"

    # PK: ULID (시간순 정렬 가능, DB 비종속적)
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=generate_ulid)

    # 원본 데이터
    article_id: Mapped[int] = mapped_column(index=True)
    crawler_name: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(100), default="")
    site_name: Mapped[str] = mapped_column(String(100))
    board_name: Mapped[str] = mapped_column(String(100))
    writer_name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1000))
    is_end: Mapped[bool] = mapped_column(default=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # 타임스탬프
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 복합 유니크 제약 및 인덱스
    __table_args__ = (
        UniqueConstraint("crawler_name", "article_id", name="uix_crawler_article"),
        Index("ix_articles_created_at", "created_at"),
        Index("ix_articles_is_end", "is_end"),
        Index("ix_articles_deleted_at", "deleted_at"),
    )

    def __repr__(self) -> str:
        return f"<Article(id={self.id}, article_id={self.article_id}, crawler={self.crawler_name}, title={self.title[:30]}...)>"


class ApiKey(Base):
    """API Key model for authentication."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))  # 사용자/서비스 식별
    rate_limit_per_minute: Mapped[int] = mapped_column(default=60)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, name={self.name}, active={self.is_active})>"


class GuestRateLimit(Base):
    """Guest rate limiting by IP address."""

    __tablename__ = "guest_rate_limits"

    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)  # IPv6 max length
    request_count: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<GuestRateLimit(ip={self.ip_address}, count={self.request_count})>"


class ApiKeyRateLimit(Base):
    """API Key rate limiting by key."""

    __tablename__ = "api_key_rate_limits"

    api_key_id: Mapped[int] = mapped_column(primary_key=True)
    request_count: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<ApiKeyRateLimit(api_key_id={self.api_key_id}, count={self.request_count})>"


class Settings(Base):
    """Application settings stored in database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # 기본 설정값 상수
    GUEST_RATE_LIMIT_PER_MINUTE = "guest_rate_limit_per_minute"
    GUEST_ACCESS_ENABLED = "guest_access_enabled"

    def __repr__(self) -> str:
        return f"<Settings(key={self.key}, value={self.value})>"
