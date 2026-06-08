import pytest
from fastapi import HTTPException, status
from sqlalchemy import event, select

from src.api.routes.articles import get_article
from src.api.routes.crawlers import list_crawlers, list_sites
from src.db import Article, ArticleRepository, get_async_engine, get_async_session, init_db


def make_article(article_id: int = 1) -> dict:
    return {
        "article_id": article_id,
        "title": f"Article {article_id}",
        "category": "category",
        "site_name": "site",
        "board_name": "board",
        "writer_name": "writer",
        "crawler_name": "dummy",
        "url": f"https://example.com/{article_id}",
        "is_end": False,
        "extra": {},
    }


@pytest.mark.asyncio
async def test_get_article_hides_soft_deleted_article():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        article = await repo.create(make_article())
        await repo.soft_delete(article.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_article(article.id, None, repo)

    await engine.dispose()

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_crawler_and_site_counts_exclude_soft_deleted_articles():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    async with get_async_session(engine) as session:
        repo = ArticleRepository(session)
        await repo.create(make_article(1))
        deleted_article = await repo.create(
            {
                **make_article(2),
                "crawler_name": "deleted-crawler",
                "site_name": "deleted-site",
            }
        )
        await repo.soft_delete(deleted_article.id)

        crawler_response = await list_crawlers(None, repo)
        site_response = await list_sites(None, repo)

    await engine.dispose()

    assert [(item.name, item.article_count) for item in crawler_response.data] == [("dummy", 1)]
    assert [(item.name, item.article_count) for item in site_response.data] == [("site", 1)]


@pytest.mark.asyncio
async def test_bulk_upsert_runs_single_insert_statement():
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    async with get_async_session(engine) as session:
        count = await ArticleRepository(session).bulk_upsert([make_article(1), make_article(2), make_article(3)])
        result = await session.execute(select(Article.article_id))
        article_ids = set(result.scalars())

    await engine.dispose()

    insert_statements = [statement for statement in statements if statement.lstrip().startswith("insert")]
    assert count == 3
    assert article_ids == {1, 2, 3}
    assert len(insert_statements) == 1
