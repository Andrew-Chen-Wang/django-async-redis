from sys import version_info

import pytest
from django.core.cache import cache

from django_async_redis.cache import RedisCache


@pytest.fixture(autouse=True)
async def cache_setup() -> RedisCache:
    await cache.clear_async()
    yield cache
    await cache.client.close_connections()


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
