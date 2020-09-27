import pytest
from django.core.cache import cache


pytestmark = pytest.mark.asyncio


async def test_basics():
    await cache.set_async("1", "blah")
    assert await cache.get_async("1") == "blah"
    await cache.delete_async("1")
    assert await cache.get_async("1") is None
