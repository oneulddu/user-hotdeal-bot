"""FastAPI application entry point."""

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db import close_db, get_engine, get_timezone

from .routes import articles_router, crawlers_router, feed_router
from .schemas import HealthResponse

# Application version (sync with pyproject.toml)
VERSION = "2.2.1"


def get_cors_origins() -> list[str]:
    """Get CORS origins from API_CORS_ORIGINS env var."""
    raw_origins = os.getenv("API_CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


# 타임존 설정 (config.yaml > TZ 환경변수 > UTC)
_timezone = get_timezone()
os.environ["TZ"] = _timezone
if hasattr(time, "tzset"):
    time.tzset()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown."""
    # Startup: Initialize database engine
    get_engine()
    yield
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
