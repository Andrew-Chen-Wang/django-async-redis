import asyncio
from sys import version_info

import pytest
import pytest_asyncio
from django.core.cache import cache as default_cache

from django_async_redis.cache import RedisCache


@pytest.fixture(scope="session")
def event_loop():
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def cache() -> RedisCache:
    yield default_cache
    await default_cache.aclear()


if version_info >= (3, 8):
    from unittest.mock import AsyncMock
else:
    from unittest.mock import MagicMock

    class AsyncMock(MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


@pytest.fixture
def mocker() -> AsyncMock:
    return AsyncMock()
