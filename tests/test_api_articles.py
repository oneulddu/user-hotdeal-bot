import pytest
from fastapi import HTTPException, status

from src.api.routes.articles import get_article
from src.db import ArticleRepository, get_async_engine, get_async_session, init_db


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
