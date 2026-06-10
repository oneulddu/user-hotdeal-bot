"""FastAPI application entry point."""

import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db import (
    ApiKeyRateLimitRepository,
    GuestRateLimitRepository,
    close_db,
    get_async_session,
    get_engine,
    get_timezone,
)
from src.version import __version__

from .routes import articles_router, crawlers_router, feed_router
from .schemas import HealthResponse

VERSION = __version__
RATE_LIMIT_CLEANUP_INTERVAL_SECONDS = 600
RATE_LIMIT_CLEANUP_OLDER_THAN_MINUTES = 60
logger = logging.getLogger(__name__)


def get_cors_origins() -> list[str]:
    """Get CORS origins from API_CORS_ORIGINS env var."""
    raw_origins = os.getenv("API_CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


# 타임존 설정 (config.yaml > TZ 환경변수 > UTC)
_timezone = get_timezone()
os.environ["TZ"] = _timezone
if hasattr(time, "tzset"):
    time.tzset()


async def cleanup_rate_limit_records() -> int:
    async with get_async_session(get_engine()) as session:
        guest_deleted = await GuestRateLimitRepository(session).cleanup_old_records(
            older_than_minutes=RATE_LIMIT_CLEANUP_OLDER_THAN_MINUTES
        )
        api_key_deleted = await ApiKeyRateLimitRepository(session).cleanup_old_records(
            older_than_minutes=RATE_LIMIT_CLEANUP_OLDER_THAN_MINUTES
        )
    return guest_deleted + api_key_deleted


async def rate_limit_cleanup_loop() -> None:
    while True:
        try:
            await cleanup_rate_limit_records()
        except Exception as e:
            logger.warning("Failed to cleanup rate limit records: %s", e)
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown."""
    # Startup: Initialize database engine
    get_engine()
    cleanup_task = asyncio.create_task(rate_limit_cleanup_loop())
    app.state.rate_limit_cleanup_task = cleanup_task
    try:
        yield
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        # Shutdown: Close database connections
        await close_db()


app = FastAPI(
    title="핫딜 API",
    description="한국 커뮤니티 핫딜 모아보기 API",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

cors_origins = get_cors_origins()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials="*" not in cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(articles_router, prefix="/api/v1")
app.include_router(crawlers_router, prefix="/api/v1")
app.include_router(feed_router)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version=VERSION)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    """Root endpoint redirect to docs."""
    return {
        "message": "핫딜 API",
        "version": VERSION,
        "docs": "/docs",
        "health": "/health",
    }
