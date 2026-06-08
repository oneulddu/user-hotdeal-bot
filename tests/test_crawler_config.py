import aiohttp
import pytest

from src import crawler


@pytest.mark.asyncio
async def test_dummy_crawler_accepts_base_crawler_options():
    async with aiohttp.ClientSession() as session:
        crawler_instance = crawler.DummyCrawler(
            "dummy",
            ["https://example.com"],
            session=session,
            proxy="http://127.0.0.1:8080",
            ssl_verify=False,
        )

        assert crawler_instance.proxy == "http://127.0.0.1:8080"
        assert crawler_instance.ssl_verify is False
