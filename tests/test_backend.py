import asyncio
import copy
import datetime
from typing import Dict

import pytest
from django.conf import settings
from django.core.cache import cache, caches
from django.test import override_settings

from django_async_redis import pool
from django_async_redis.cache import DJANGO_ASYNC_REDIS_SCAN_ITERSIZE, RedisCache
from django_async_redis.serializers.json import JSONSerializer


pytestmark = pytest.mark.asyncio

# Type hints
cache: RedisCache
caches: Dict[str, RedisCache]


@pytest.mark.parametrize(
    "conn_string",
    ["unix://tmp/foo.bar?db=1", "redis://localhost/2", "rediss://localhost:3333?db=2"],
)
async def test_connection_strings(conn_string):
    cf = pool.get_connection_factory(options={})
    res = cf.make_connection_params(conn_string)
    assert res["url"] == conn_string


class TestEscapePrefix:
    OTHER_CACHE = "with_prefix"

    async def test_delete_pattern(self):
        assert await cache.aset("a", "1") is True
        assert await caches[self.OTHER_CACHE].aset("b", "2") is True
        await cache.adelete_pattern("*")
        assert await cache.ahas_key("a") is False
        assert await caches[self.OTHER_CACHE].aget("b") == "2"

    async def test_iter_keys(self):
        assert await cache.aset("a", "1") is True
        await caches[self.OTHER_CACHE].aset("b", "2")
        assert [x async for x in await cache.aiter_keys("*")] == ["a"]

    async def test_keys(self):
        await cache.aset("a", "1")
        await caches[self.OTHER_CACHE].aset("b", "2")
        keys = await cache.akeys("*")
        assert "a" in keys
        assert "b" not in keys


def make_key(key, prefix, version):
    return "{}#{}#{}".format(prefix, version, key)


def reverse_key(key):
    return key.split("#", 2)[2]


class TestCustomKeyFunction:
    async def test_custom_key_function(self):
        caches_settings = copy.deepcopy(settings.CACHES)
        caches_settings["default"]["KEY_FUNCTION"] = "tests.test_backend.make_key"
        caches_settings["default"][
            "REVERSE_KEY_FUNCTION"
        ] = "tests.test_backend.reverse_key"
        cm = override_settings(CACHES=caches_settings)
        cm.enable()
        # Test
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.aset(key, "foo")

        res = await cache.adelete_pattern("*foo-a*")
        assert bool(res) is True

        keys = await cache.akeys("foo*")
        assert set(keys) == {"foo-bb", "foo-bc"}
        # ensure our custom function was actually called
        assert {
            k.decode() for k in await cache.client.get_client(write=False).keys("*")
        } == {"#1#foo-bc", "#1#foo-bb"}


class TestBackend:
    async def test_setnx(self):
        # we should ensure there is no test_key_nx in redis
        await cache.adelete("test_key_nx")
        res = await cache.aget("test_key_nx")
        assert res is None

        res = await cache.aset("test_key_nx", 1, nx=True)
        assert res is True
        # test that second set will have
        res = await cache.aset("test_key_nx", 2, nx=True)
        assert res is False
        res = await cache.aget("test_key_nx")
        assert res == 1

        await cache.adelete("test_key_nx")
        res = await cache.aget("test_key_nx")
        assert res is None

    async def test_setnx_timeout(self):
        # test that timeout still works for nx=True
        res = await cache.aset("test_key_nx", 1, timeout=2, nx=True)
        assert res is True
        await asyncio.sleep(3)
        res = await cache.aget("test_key_nx")
        assert res is None

        # test that timeout will not affect key, if it was there
        await cache.aset("test_key_nx", 1)
        res = await cache.aset("test_key_nx", 2, timeout=2, nx=True)
        assert res is False
        await asyncio.sleep(3)
        res = await cache.aget("test_key_nx")
        assert res == 1

        await cache.adelete("test_key_nx")
        res = await cache.aget("test_key_nx")
        assert res is None

    async def test_unicode_keys(self):
        await cache.aset("ключ", "value")
        res = await cache.aget("ключ")
        assert res == "value"

    async def test_save_and_integer(self):
        await cache.aset("test_key", 2)
        res = await cache.aget("test_key", "Foo")

        assert isinstance(res, int)
        assert res == 2

    async def test_save_string(self):
        await cache.aset("test_key", "hello" * 1000)
        res = await cache.aget("test_key")

        assert isinstance(res, str)
        assert res == "hello" * 1000

        await cache.aset("test_key", "2")
        res = await cache.aget("test_key")

        assert isinstance(res, str)
        assert res == "2"

    async def test_save_unicode(self):
        await cache.aset("test_key", "heló")
        res = await cache.aget("test_key")

        assert isinstance(res, str)
        assert res == "heló"

    async def test_save_dict(self):
        if isinstance(cache.client._serializer, (JSONSerializer,)):
            # JSONSerializer and MSGPackSerializer use the isoformat for
            # datetimes.
            now_dt = datetime.datetime.now().isoformat()
        else:
            now_dt = datetime.datetime.now()

        test_dict = {"id": 1, "date": now_dt, "name": "Foo"}

        await cache.aset("test_key", test_dict)
        res = await cache.aget("test_key")

        assert isinstance(res, dict)
        assert res["id"] == 1
        assert res["name"] == "Foo"
        assert res["date"] == now_dt

    async def test_save_float(self):
        float_val = 1.345620002

        await cache.aset("test_key", float_val)
        res = await cache.aget("test_key")

        assert isinstance(res, float)
        assert res == float_val

    async def test_timeout(self):
        await cache.aset("test_key", 222, timeout=3)
        await asyncio.sleep(4)

        res = await cache.aget("test_key")
        assert res is None

    async def test_timeout_0(self):
        await cache.aset("test_key", 222, timeout=0)
        res = await cache.aget("test_key")
        assert res is None

    async def test_timeout_parameter_as_positional_argument(self):
        await cache.aset("test_key", 222, -1)
        res = await cache.aget("test_key")
        assert res is None

        await cache.aset("test_key", 222, 1)
        res1 = await cache.aget("test_key")
        await asyncio.sleep(2)
        res2 = await cache.aget("test_key")
        assert res1 == 222
        assert res2 is None

        # nx=True should not overwrite expire of key already in db
        await cache.aset("test_key", 222, None)
        await cache.aset("test_key", 222, -1, nx=True)
        res = await cache.aget("test_key")
        assert res == 222

    async def test_timeout_negative(self):
        await cache.aset("test_key", 222, timeout=-1)
        res = await cache.aget("test_key")
        assert res is None

        await cache.aset("test_key", 222, timeout=None)
        await cache.aset("test_key", 222, timeout=-1)
        res = await cache.aget("test_key")
        assert res is None

        # nx=True should not overwrite expire of key already in db
        await cache.aset("test_key", 222, timeout=None)
        await cache.aset("test_key", 222, timeout=-1, nx=True)
        res = await cache.aget("test_key")
        assert res == 222

    async def test_timeout_tiny(self):
        await cache.aset("test_key", 222, timeout=0.00001)
        res = await cache.aget("test_key")
        assert res in (None, 222)

    async def test_set_add(self):
        await cache.aset("add_key", "Initial value")
        res = await cache.aadd("add_key", "New value")
        assert res is False

        res = await cache.aget("add_key")
        assert res == "Initial value"

        res = await cache.aadd("other_key", "New value")
        assert res is True

    async def test_get_many(self):
        await cache.aset("a", 1)
        await cache.aset("b", 2)
        await cache.aset("c", 3)

        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"a": 1, "b": 2, "c": 3}

    async def test_get_many_unicode(self):
        await cache.aset("a", "1")
        await cache.aset("b", "2")
        await cache.aset("c", "3")

        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"a": "1", "b": "2", "c": "3"}

    async def test_set_many(self):
        await cache.aset_many({"a": 1, "b": 2, "c": 3})
        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_set_call_empty_pipeline(self, mocker):
        pipeline = cache.client.get_client(write=True).pipeline()
        key = "key"
        value = "value"

        mocked_set = mocker.patch.object(pipeline, "set")
        cache.aset(key, value, client=pipeline)
        await pipeline.execute()

        (await mocked_set).assert_called_once_with(
            cache.client.make_key(key, version=None),
            cache.client.encode(value),
            pexpire=cache.client._backend.default_timeout * 1000,
            nx=False,
            xx=False,
        )

    async def test_delete(self):
        await cache.aset_many({"a": 1, "b": 2, "c": 3})
        res = await cache.adelete("a")
        assert bool(res) is True

        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"b": 2, "c": 3}

        res = await cache.adelete("a")
        assert bool(res) is False

    async def test_delete_many(self):
        await cache.aset_many({"a": 1, "b": 2, "c": 3})
        res = await cache.adelete_many(["a", "b"])
        assert bool(res) is True

        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"c": 3}

        res = await cache.adelete_many(["a", "b"])
        assert bool(res) is False

    async def test_delete_many_generator(self):
        await cache.aset_many({"a": 1, "b": 2, "c": 3})
        res = await cache.adelete_many(key for key in ["a", "b"])
        assert bool(res) is True

        res = await cache.aget_many(["a", "b", "c"])
        assert res == {"c": 3}

        res = await cache.adelete_many(["a", "b"])
        assert bool(res) is False

    async def test_delete_many_empty_generator(self):
        res = await cache.adelete_many(key for key in [])
        assert bool(res) is False

    async def test_incr(self):
        await cache.aset("num", 1)

        await cache.aincr("num")
        res = await cache.aget("num")
        assert res == 2

        await cache.aincr("num", 10)
        res = await cache.aget("num")
        assert res == 12

        # max 64 bit signed int
        await cache.aset("num", 9223372036854775807)

        await cache.aincr("num")
        res = await cache.aget("num")
        assert res == 9223372036854775808

        await cache.aincr("num", 2)
        res = await cache.aget("num")
        assert res == 9223372036854775810

        await cache.aset("num", 3)

        await cache.aincr("num", 2)
        res = await cache.aget("num")
        assert res == 5

    async def test_incr_error(self):
        with pytest.raises(ValueError):
            # key does not exist
            await cache.aincr("numnum")

    async def test_incr_ignore_check(self):
        # key exists check will be skipped and the value will be incremented by
        # '1' which is the default delta
        await cache.aincr("num", ignore_key_check=True)
        res = await cache.aget("num")
        assert res == 1
        await cache.adelete("num")

        # since key doesn't exist it is set to the delta value, 10 in this case
        await cache.aincr("num", 10, ignore_key_check=True)
        res = await cache.aget("num")
        assert res == 10
        await cache.adelete("num")

        # following are just regression checks to make sure it still works as
        # expected with incr max 64 bit signed int
        await cache.aset("num", 9223372036854775807)

        await cache.aincr("num", ignore_key_check=True)
        res = await cache.aget("num")
        assert res == 9223372036854775808

        await cache.aincr("num", 2, ignore_key_check=True)
        res = await cache.aget("num")
        assert res == 9223372036854775810

        await cache.aset("num", 3)

        await cache.aincr("num", 2, ignore_key_check=True)
        res = await cache.aget("num")
        assert res == 5

    async def test_get_set_bool(self):
        await cache.aset("bool", True)
        res = await cache.aget("bool")

        assert isinstance(res, bool)
        assert res is True

        await cache.aset("bool", False)
        res = await cache.aget("bool")

        assert isinstance(res, bool)
        assert res is False

    async def test_decr(self):
        await cache.aset("num", 20)

        await cache.adecr("num")
        res = await cache.aget("num")
        assert res == 19

        await cache.adecr("num", 20)
        res = await cache.aget("num")
        assert res == -1

        await cache.adecr("num", 2)
        res = await cache.aget("num")
        assert res == -3

        await cache.aset("num", 20)

        await cache.adecr("num")
        res = await cache.aget("num")
        assert res == 19

        # max 64 bit signed int + 1
        await cache.aset("num", 9223372036854775808)

        await cache.adecr("num")
        res = await cache.aget("num")
        assert res == 9223372036854775807

        await cache.adecr("num", 2)
        res = await cache.aget("num")
        assert res == 9223372036854775805

    async def test_version(self):
        await cache.aset("keytest", 2, version=2)
        res = await cache.aget("keytest")
        assert res is None

        res = await cache.aget("keytest", version=2)
        assert res == 2

    async def test_incr_version(self):
        await cache.aset("keytest", 2)
        await cache.aincr_version("keytest")

        res = await cache.aget("keytest")
        assert res is None

        res = await cache.aget("keytest", version=2)
        assert res == 2

    async def test_delete_pattern(self):
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.aset(key, "foo")

        res = await cache.adelete_pattern("*foo-a*")
        assert bool(res) is True

        keys = await cache.akeys("foo*")
        assert set(keys) == {"foo-bb", "foo-bc"}

        res = await cache.adelete_pattern("*foo-a*")
        assert bool(res) is False

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_delete_pattern_with_custom_count(self, mocker):
        client_mock = mocker.patch("django_async_redis.cache.RedisCache.client")
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.aset(key, "foo")

        await cache.adelete_pattern("*foo-a*", itersize=2)

        (await client_mock).adelete_pattern.assert_called_once_with(
            "*foo-a*", itersize=2
        )

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_delete_pattern_with_settings_default_scan_count(self, mocker):
        client_mock = mocker.patch("django_async_redis.cache.RedisCache.client")
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.aset(key, "foo")
        expected_count = DJANGO_ASYNC_REDIS_SCAN_ITERSIZE

        await cache.adelete_pattern("*foo-a*")

        (await client_mock).adelete_pattern.assert_called_once_with(
            "*foo-a*", itersize=expected_count
        )

    async def test_close(self):
        _cache = caches["default"]
        await _cache.aset("f", "1")
        await _cache.aclose()

    async def test_ttl(self):
        _cache = caches["default"]

        # Test ttl
        await _cache.aset("foo", "bar", 10)
        ttl = await _cache.attl("foo")
        assert ttl == 10

        # Test ttl None
        await _cache.aset("foo", "foo", timeout=None)
        ttl = await _cache.attl("foo")
        assert ttl is None

        # Test ttl with expired key
        await _cache.aset("foo", "foo", timeout=-1)
        ttl = await _cache.attl("foo")
        assert ttl == 0

        # Test ttl with not existent key
        ttl = await _cache.attl("not-existent-key")
        assert ttl == 0

    async def test_persist(self):
        await cache.aset("foo", "bar", timeout=20)
        await cache.apersist("foo")

        ttl = await cache.attl("foo")
        assert ttl is None

    async def test_expire(self):
        await cache.aset("foo", "bar", timeout=None)
        await cache.aexpire("foo", 20)
        ttl = await cache.attl("foo")
        assert pytest.approx(ttl) == 20

    async def test_iter_keys(self):
        _cache = caches["default"]

        await _cache.aset("foo1", 1)
        await _cache.aset("foo2", 1)
        await _cache.aset("foo3", 1)

        # Test simple result
        result = set([x async for x in await _cache.aiter_keys("*")])
        assert result == {"foo1", "foo2", "foo3"}

        # Test limited result
        result = [x async for x in await _cache.aiter_keys("foo*", itersize=2)]
        assert len(result) == 3

        # Test generator object
        result = await _cache.aiter_keys("foo*")
        assert result.__anext__() is not None

    async def test_primary_replica_switching(self):
        _cache = caches["sample"]
        client = cache.client
        client._server = ["foo", "bar"]
        client._clients = ["Foo", "Bar"]

        assert await client.get_client(write=True) == "Foo"
        assert await client.get_client(write=False) == "Bar"

    async def test_touch_zero_timeout(self):
        await cache.aset("test_key", 222, timeout=10)

        assert await cache.atouch("test_key", 0) is True
        res = await cache.aget("test_key")
        assert res is None

    async def test_touch_positive_timeout(self):
        await cache.aset("test_key", 222, timeout=10)

        assert await cache.atouch("test_key", 2) is True
        assert await cache.aget("test_key") == 222
        await asyncio.sleep(3)
        assert await cache.aget("test_key") is None

    async def test_touch_negative_timeout(self):
        await cache.aset("test_key", 222, timeout=10)

        assert await cache.atouch("test_key", -1) is True
        res = await cache.aget("test_key")
        assert res is None

    async def test_touch_missed_key(self):
        assert await cache.atouch("test_key_does_not_exist", 1) is False

    async def test_touch_forever(self):
        await cache.aset("test_key", "foo", timeout=1)
        result = await cache.atouch("test_key", None)
        assert result is True
        assert await cache.attl("test_key") is None
        await asyncio.sleep(2)
        assert await cache.aget("test_key") == "foo"

    async def test_touch_forever_nonexistent(self):
        result = await cache.atouch("test_key_does_not_exist", None)
        assert result is False

    async def test_touch_default_timeout(self):
        await cache.aset("test_key", "foo", timeout=1)
        result = await cache.atouch("test_key")
        assert result is True
        await asyncio.sleep(2)
        assert await cache.aget("test_key") == "foo"
