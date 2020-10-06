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
from django_async_redis.serializers import JSONSerializer


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
    assert res["address"] == conn_string


class TestEscapePrefix:
    OTHER_CACHE = "with_prefix"

    async def test_delete_pattern(self):
        assert await cache.set_async("a", "1") is True
        assert await caches[self.OTHER_CACHE].set_async("b", "2") is True
        await cache.delete_pattern_async("*")
        assert await cache.has_key_async("a") is False
        assert await caches[self.OTHER_CACHE].get_async("b") == "2"

    async def test_iter_keys(self):
        assert await cache.set_async("a", "1") is True
        await caches[self.OTHER_CACHE].set_async("b", "2")
        assert [x async for x in await cache.iter_keys_async("*")] == ["a"]

    async def test_keys(self):
        await cache.set_async("a", "1")
        await caches[self.OTHER_CACHE].set_async("b", "2")
        keys = await cache.keys_async("*")
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
            await cache.set_async(key, "foo")

        res = await cache.delete_pattern_async("*foo-a*")
        assert bool(res) is True

        keys = await cache.keys_async("foo*")
        assert set(keys) == {"foo-bb", "foo-bc"}
        # ensure our custom function was actually called
        assert {
            k.decode()
            for k in await (await cache.client.get_client(write=False)).keys("*")
        } == {"#1#foo-bc", "#1#foo-bb"}


class TestBackend:
    async def test_setnx(self):
        # we should ensure there is no test_key_nx in redis
        await cache.delete_async("test_key_nx")
        res = await cache.get_async("test_key_nx")
        assert res is None

        res = await cache.set_async("test_key_nx", 1, nx=True)
        assert res is True
        # test that second set will have
        res = await cache.set_async("test_key_nx", 2, nx=True)
        assert res is False
        res = await cache.get_async("test_key_nx")
        assert res == 1

        await cache.delete_async("test_key_nx")
        res = await cache.get_async("test_key_nx")
        assert res is None

    async def test_setnx_timeout(self):
        # test that timeout still works for nx=True
        res = await cache.set_async("test_key_nx", 1, timeout=2, nx=True)
        assert res is True
        await asyncio.sleep(3)
        res = await cache.get_async("test_key_nx")
        assert res is None

        # test that timeout will not affect key, if it was there
        await cache.set_async("test_key_nx", 1)
        res = await cache.set_async("test_key_nx", 2, timeout=2, nx=True)
        assert res is False
        await asyncio.sleep(3)
        res = await cache.get_async("test_key_nx")
        assert res == 1

        await cache.delete_async("test_key_nx")
        res = await cache.get_async("test_key_nx")
        assert res is None

    async def test_unicode_keys(self):
        await cache.set_async("ключ", "value")
        res = await cache.get_async("ключ")
        assert res == "value"

    async def test_save_and_integer(self):
        await cache.set_async("test_key", 2)
        res = await cache.get_async("test_key", "Foo")

        assert isinstance(res, int)
        assert res == 2

    async def test_save_string(self):
        await cache.set_async("test_key", "hello" * 1000)
        res = await cache.get_async("test_key")

        assert isinstance(res, str)
        assert res == "hello" * 1000

        await cache.set_async("test_key", "2")
        res = await cache.get_async("test_key")

        assert isinstance(res, str)
        assert res == "2"

    async def test_save_unicode(self):
        await cache.set_async("test_key", "heló")
        res = await cache.get_async("test_key")

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

        await cache.set_async("test_key", test_dict)
        res = await cache.get_async("test_key")

        assert isinstance(res, dict)
        assert res["id"] == 1
        assert res["name"] == "Foo"
        assert res["date"] == now_dt

    async def test_save_float(self):
        float_val = 1.345620002

        await cache.set_async("test_key", float_val)
        res = await cache.get_async("test_key")

        assert isinstance(res, float)
        assert res == float_val

    async def test_timeout(self):
        await cache.set_async("test_key", 222, timeout=3)
        await asyncio.sleep(4)

        res = await cache.get_async("test_key")
        assert res is None

    async def test_timeout_0(self):
        await cache.set_async("test_key", 222, timeout=0)
        res = await cache.get_async("test_key")
        assert res is None

    async def test_timeout_parameter_as_positional_argument(self):
        await cache.set_async("test_key", 222, -1)
        res = await cache.get_async("test_key")
        assert res is None

        await cache.set_async("test_key", 222, 1)
        res1 = await cache.get_async("test_key")
        await asyncio.sleep(2)
        res2 = await cache.get_async("test_key")
        assert res1 == 222
        assert res2 is None

        # nx=True should not overwrite expire of key already in db
        await cache.set_async("test_key", 222, None)
        await cache.set_async("test_key", 222, -1, nx=True)
        res = await cache.get_async("test_key")
        assert res == 222

    async def test_timeout_negative(self):
        await cache.set_async("test_key", 222, timeout=-1)
        res = await cache.get_async("test_key")
        assert res is None

        await cache.set_async("test_key", 222, timeout=None)
        await cache.set_async("test_key", 222, timeout=-1)
        res = await cache.get_async("test_key")
        assert res is None

        # nx=True should not overwrite expire of key already in db
        await cache.set_async("test_key", 222, timeout=None)
        await cache.set_async("test_key", 222, timeout=-1, nx=True)
        res = await cache.get_async("test_key")
        assert res == 222

    async def test_timeout_tiny(self):
        await cache.set_async("test_key", 222, timeout=0.00001)
        res = await cache.get_async("test_key")
        assert res in (None, 222)

    async def test_set_add(self):
        await cache.set_async("add_key", "Initial value")
        res = await cache.add_async("add_key", "New value")
        assert res is False

        res = await cache.get_async("add_key")
        assert res == "Initial value"

        res = await cache.add_async("other_key", "New value")
        assert res is True

    async def test_get_many(self):
        await cache.set_async("a", 1)
        await cache.set_async("b", 2)
        await cache.set_async("c", 3)

        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"a": 1, "b": 2, "c": 3}

    async def test_get_many_unicode(self):
        await cache.set_async("a", "1")
        await cache.set_async("b", "2")
        await cache.set_async("c", "3")

        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"a": "1", "b": "2", "c": "3"}

    async def test_set_many(self):
        await cache.set_many_async({"a": 1, "b": 2, "c": 3})
        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_set_call_empty_pipeline(self, mocker):
        pipeline = (await cache.client.get_client(write=True)).pipeline()
        key = "key"
        value = "value"

        mocked_set = mocker.patch.object(pipeline, "set")
        cache.set_async(key, value, client=pipeline)
        await pipeline.execute()

        (await mocked_set).assert_called_once_with(
            cache.client.make_key(key, version=None),
            cache.client.encode(value),
            pexpire=cache.client._backend.default_timeout * 1000,
            nx=False,
            xx=False,
        )

    async def test_delete(self):
        await cache.set_many_async({"a": 1, "b": 2, "c": 3})
        res = await cache.delete_async("a")
        assert bool(res) is True

        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"b": 2, "c": 3}

        res = await cache.delete_async("a")
        assert bool(res) is False

    async def test_delete_many(self):
        await cache.set_many_async({"a": 1, "b": 2, "c": 3})
        res = await cache.delete_many_async(["a", "b"])
        assert bool(res) is True

        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"c": 3}

        res = await cache.delete_many_async(["a", "b"])
        assert bool(res) is False

    async def test_delete_many_generator(self):
        await cache.set_many_async({"a": 1, "b": 2, "c": 3})
        res = await cache.delete_many_async(key for key in ["a", "b"])
        assert bool(res) is True

        res = await cache.get_many_async(["a", "b", "c"])
        assert res == {"c": 3}

        res = await cache.delete_many_async(["a", "b"])
        assert bool(res) is False

    async def test_delete_many_empty_generator(self):
        res = await cache.delete_many_async(key for key in [])
        assert bool(res) is False

    async def test_incr(self):
        await cache.set_async("num", 1)

        await cache.incr_async("num")
        res = await cache.get_async("num")
        assert res == 2

        await cache.incr_async("num", 10)
        res = await cache.get_async("num")
        assert res == 12

        # max 64 bit signed int
        await cache.set_async("num", 9223372036854775807)

        await cache.incr_async("num")
        res = await cache.get_async("num")
        assert res == 9223372036854775808

        await cache.incr_async("num", 2)
        res = await cache.get_async("num")
        assert res == 9223372036854775810

        await cache.set_async("num", 3)

        await cache.incr_async("num", 2)
        res = await cache.get_async("num")
        assert res == 5

    async def test_incr_error(self):
        with pytest.raises(ValueError):
            # key does not exist
            await cache.incr_async("numnum")

    async def test_incr_ignore_check(self):
        # key exists check will be skipped and the value will be incremented by
        # '1' which is the default delta
        await cache.incr_async("num", ignore_key_check=True)
        res = await cache.get_async("num")
        assert res == 1
        await cache.delete_async("num")

        # since key doesnt exist it is set to the delta value, 10 in this case
        await cache.incr_async("num", 10, ignore_key_check=True)
        res = await cache.get_async("num")
        assert res == 10
        await cache.delete_async("num")

        # following are just regression checks to make sure it still works as
        # expected with incr max 64 bit signed int
        await cache.set_async("num", 9223372036854775807)

        await cache.incr_async("num", ignore_key_check=True)
        res = await cache.get_async("num")
        assert res == 9223372036854775808

        await cache.incr_async("num", 2, ignore_key_check=True)
        res = await cache.get_async("num")
        assert res == 9223372036854775810

        await cache.set_async("num", 3)

        await cache.incr_async("num", 2, ignore_key_check=True)
        res = await cache.get_async("num")
        assert res == 5

    async def test_get_set_bool(self):
        await cache.set_async("bool", True)
        res = await cache.get_async("bool")

        assert isinstance(res, bool)
        assert res is True

        await cache.set_async("bool", False)
        res = await cache.get_async("bool")

        assert isinstance(res, bool)
        assert res is False

    async def test_decr(self):
        await cache.set_async("num", 20)

        await cache.decr_async("num")
        res = await cache.get_async("num")
        assert res == 19

        await cache.decr_async("num", 20)
        res = await cache.get_async("num")
        assert res == -1

        await cache.decr_async("num", 2)
        res = await cache.get_async("num")
        assert res == -3

        await cache.set_async("num", 20)

        await cache.decr_async("num")
        res = await cache.get_async("num")
        assert res == 19

        # max 64 bit signed int + 1
        await cache.set_async("num", 9223372036854775808)

        await cache.decr_async("num")
        res = await cache.get_async("num")
        assert res == 9223372036854775807

        await cache.decr_async("num", 2)
        res = await cache.get_async("num")
        assert res == 9223372036854775805

    async def test_version(self):
        await cache.set_async("keytest", 2, version=2)
        res = await cache.get_async("keytest")
        assert res is None

        res = await cache.get_async("keytest", version=2)
        assert res == 2

    async def test_incr_version(self):
        await cache.set_async("keytest", 2)
        await cache.incr_version_async("keytest")

        res = await cache.get_async("keytest")
        assert res is None

        res = await cache.get_async("keytest", version=2)
        assert res == 2

    async def test_delete_pattern(self):
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.set_async(key, "foo")

        res = await cache.delete_pattern_async("*foo-a*")
        assert bool(res) is True

        keys = await cache.keys_async("foo*")
        assert set(keys) == {"foo-bb", "foo-bc"}

        res = await cache.delete_pattern_async("*foo-a*")
        assert bool(res) is False

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_delete_pattern_with_custom_count(self, mocker):
        client_mock = mocker.patch("django_async_redis.cache.RedisCache.client")
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.set_async(key, "foo")

        await cache.delete_pattern_async("*foo-a*", itersize=2)

        (await client_mock).delete_pattern_async.assert_called_once_with(
            "*foo-a*", itersize=2
        )

    @pytest.mark.xfail(reason="Can't figure out how to test with mock")
    async def test_delete_pattern_with_settings_default_scan_count(self, mocker):
        client_mock = mocker.patch("django_async_redis.cache.RedisCache.client")
        for key in ["foo-aa", "foo-ab", "foo-bb", "foo-bc"]:
            await cache.set_async(key, "foo")
        expected_count = DJANGO_ASYNC_REDIS_SCAN_ITERSIZE

        await cache.delete_pattern_async("*foo-a*")

        (await client_mock).delete_pattern_async.assert_called_once_with(
            "*foo-a*", itersize=expected_count
        )

    async def test_close(self):
        _cache = caches["default"]
        await _cache.set_async("f", "1")
        await _cache.close_async()

    async def test_ttl(self):
        _cache = caches["default"]

        # Test ttl
        await _cache.set_async("foo", "bar", 10)
        ttl = await _cache.ttl_async("foo")
        assert ttl == 10

        # Test ttl None
        await _cache.set_async("foo", "foo", timeout=None)
        ttl = await _cache.ttl_async("foo")
        assert ttl is None

        # Test ttl with expired key
        await _cache.set_async("foo", "foo", timeout=-1)
        ttl = await _cache.ttl_async("foo")
        assert ttl == 0

        # Test ttl with not existent key
        ttl = await _cache.ttl_async("not-existent-key")
        assert ttl == 0

    async def test_persist(self):
        await cache.set_async("foo", "bar", timeout=20)
        await cache.persist_async("foo")

        ttl = await cache.ttl_async("foo")
        assert ttl is None

    async def test_expire(self):
        await cache.set_async("foo", "bar", timeout=None)
        await cache.expire_async("foo", 20)
        ttl = await cache.ttl_async("foo")
        assert pytest.approx(ttl) == 20

    async def test_iter_keys(self):
        _cache = caches["default"]

        await _cache.set_async("foo1", 1)
        await _cache.set_async("foo2", 1)
        await _cache.set_async("foo3", 1)

        # Test simple result
        result = set([x async for x in await _cache.iter_keys_async("*")])
        assert result == {"foo1", "foo2", "foo3"}

        # Test limited result
        result = [x async for x in await _cache.iter_keys_async("foo*", itersize=2)]
        assert len(result) == 3

        # Test generator object
        result = await _cache.iter_keys_async("foo*")
        assert result.__anext__() is not None

    async def test_primary_replica_switching(self):
        _cache = caches["sample"]
        client = cache.client
        client._server = ["foo", "bar"]
        client._clients = ["Foo", "Bar"]

        assert await client.get_client(write=True) == "Foo"
        assert await client.get_client(write=False) == "Bar"

    async def test_touch_zero_timeout(self):
        await cache.set_async("test_key", 222, timeout=10)

        assert await cache.touch_async("test_key", 0) is True
        res = await cache.get_async("test_key")
        assert res is None

    async def test_touch_positive_timeout(self):
        await cache.set_async("test_key", 222, timeout=10)

        assert await cache.touch_async("test_key", 2) is True
        assert await cache.get_async("test_key") == 222
        await asyncio.sleep(3)
        assert await cache.get_async("test_key") is None

    async def test_touch_negative_timeout(self):
        await cache.set_async("test_key", 222, timeout=10)

        assert await cache.touch_async("test_key", -1) is True
        res = await cache.get_async("test_key")
        assert res is None

    async def test_touch_missed_key(self):
        assert await cache.touch_async("test_key_does_not_exist", 1) is False

    async def test_touch_forever(self):
        await cache.set_async("test_key", "foo", timeout=1)
        result = await cache.touch_async("test_key", None)
        assert result is True
        assert await cache.ttl_async("test_key") is None
        await asyncio.sleep(2)
        assert await cache.get_async("test_key") == "foo"

    async def test_touch_forever_nonexistent(self):
        result = await cache.touch_async("test_key_does_not_exist", None)
        assert result is False

    async def test_touch_default_timeout(self):
        await cache.set_async("test_key", "foo", timeout=1)
        result = await cache.touch_async("test_key")
        assert result is True
        await asyncio.sleep(2)
        assert await cache.get_async("test_key") == "foo"
