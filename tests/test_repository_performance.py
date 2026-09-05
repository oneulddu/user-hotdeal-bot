import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from src.db import Article, ArticleRepository, get_async_engine, get_async_session, init_db
from tests.test_api_articles import make_article


@pytest_asyncio.fixture
async def engine():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        await driver._execute(driver._conn.setlimit, sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bulk_writes_stay_within_sqlite_bind_limit(engine):
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, parameters, _context, _many):
        statements.append((statement.split()[0].upper(), len(parameters)))

    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        assert await repo.bulk_upsert([make_article(i) for i in range(1051)]) == 1051
        assert await repo.bulk_soft_delete([("dummy", i) for i in range(1050)]) == 1050
        rows, total = await repo.list_articles()
        assert total == 1
        assert rows[0].article_id == 1050

    assert len([s for s in statements if s[0] == "INSERT"]) == 22
    assert len([s for s in statements if s[0] == "UPDATE"]) == 3
    assert max(size for _, size in statements) <= 999


@pytest.mark.asyncio
async def test_bulk_upsert_rolls_back_earlier_batches_on_failure(engine):
    rows = [make_article(i) for i in range(51)]
    rows[-1]["title"] = None
    with pytest.raises(IntegrityError):
        async with get_async_session(engine) as session:
            await ArticleRepository(session).bulk_upsert(rows)
    async with get_async_session(engine) as session:
        assert list((await session.execute(select(Article.id))).scalars()) == []


@pytest.mark.asyncio
async def test_upsert_batches_preserve_identity_creation_time_and_share_update_time(engine):
    rows = [make_article(i) for i in range(51)]
    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        await repo.bulk_upsert(rows)
        before = {
            article.article_id: (article.id, article.created_at)
            for article in (await session.execute(select(Article))).scalars()
        }
        await repo.bulk_soft_delete([("dummy", i) for i in range(51)])
    async with get_async_session(engine) as session:
        await ArticleRepository(session).bulk_upsert([{**row, "title": "updated"} for row in rows])
    async with get_async_session(engine) as session:
        restored = list((await session.execute(select(Article))).scalars())
        assert {article.article_id: (article.id, article.created_at) for article in restored} == before
        assert {article.title for article in restored} == {"updated"}
        assert {article.deleted_at for article in restored} == {None}
        assert len({article.updated_at for article in restored}) == 1


@pytest.mark.asyncio
async def test_mysql_upsert_batches_compile_with_shared_conflict_fields():
    session = SimpleNamespace(bind=SimpleNamespace(dialect=mysql.dialect()), execute=AsyncMock(), flush=AsyncMock())
    repo = ArticleRepository(session)
    assert await repo.bulk_upsert([make_article(i) for i in range(501)]) == 501
    assert session.execute.await_count == 2
    sizes = []
    for call in session.execute.await_args_list:
        compiled = call.args[0].compile(dialect=mysql.dialect())
        sql = str(compiled)
        updates = sql.split("ON DUPLICATE KEY UPDATE ")[1]
        assert "title = VALUES(title)" in updates
        assert "deleted_at = " in updates
        assert "created_at = " not in updates
        assert " id = " not in updates
        sizes.append(sum(name.startswith("article_id_m") for name in compiled.params))
    assert sizes == [500, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"crawler": "dummy", "site": "site", "is_end": False},
        {"crawler": "dummy", "site": "other"},
        {"is_end": True},
        {"include_deleted": True},
        {"after": "00000000000000000000000003", "include_deleted": True},
        {"crawler": "missing"},
    ],
)
async def test_shared_filters_preserve_pagination_totals_and_order(engine, filters):
    rows = [
        {
            **make_article(i),
            "id": f"{i:026d}",
            "site_name": "other" if i % 2 else "site",
            "is_end": i == 4,
            "deleted_at": datetime(2026, 1, 1) if i == 6 else None,
        }
        for i in range(1, 7)
    ]
    expected = [
        row["id"]
        for row in reversed(rows)
        if (filters.get("after") is None or row["id"] > filters["after"])
        and (filters.get("crawler") is None or row["crawler_name"] == filters["crawler"])
        and (filters.get("site") is None or row["site_name"] == filters["site"])
        and (filters.get("is_end") is None or row["is_end"] == filters["is_end"])
        and (filters.get("include_deleted") or row["deleted_at"] is None)
    ]
    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        await repo.bulk_upsert(rows)
        articles, total = await repo.list_articles(**filters, limit=2, offset=1)
        assert total == len(expected)
        assert [a.id for a in articles] == expected[1:3]
