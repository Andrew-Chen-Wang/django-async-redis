import asyncio
import os
import pickle
import unittest
from unittest import IsolatedAsyncioTestCase, mock

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import DEFAULT_CACHE_ALIAS, CacheKeyWarning, cache, caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.http import HttpResponse
from django.middleware.cache import FetchFromCacheMiddleware, UpdateCacheMiddleware
from django.test import AsyncRequestFactory, SimpleTestCase, TestCase, override_settings

from django_async_redis.client.core import AsyncRedisCacheClient

from .app.models import Poll, expensive_calculation


# from .tests import C, KEY_ERRORS_WITH_MEMCACHED_MSG, caches_setting_for_tests, f, \
#     Unpicklable, empty_response


pytestmark = pytest.mark.django_db


# functions/classes for complex data type tests
def f():
    return 42


class C:
    def m(n):
        return 24


KEY_ERRORS_WITH_MEMCACHED_MSG = (
    "Cache key contains characters that will cause errors if used with memcached: %r"
)


def custom_key_func(key, key_prefix, version):
    "A customized cache key function"
    return "CUSTOM-" + "-".join([key_prefix, str(version), key])


_caches_setting_base = {
    "default": {},
    "prefix": {"KEY_PREFIX": "cacheprefix{}".format(os.getpid())},
    "v2": {"VERSION": 2},
    "custom_key": {"KEY_FUNCTION": custom_key_func},
    "custom_key2": {"KEY_FUNCTION": "cache.tests.custom_key_func"},
    "cull": {"OPTIONS": {"MAX_ENTRIES": 30}},
    "zero_cull": {"OPTIONS": {"CULL_FREQUENCY": 0, "MAX_ENTRIES": 30}},
}


def caches_setting_for_tests(base=None, exclude=None, **params):
    # `base` is used to pull in the memcached config from the original settings,
    # `exclude` is a set of cache names denoting which `_caches_setting_base` keys
    # should be omitted.
    # `params` are test specific overrides and `_caches_settings_base` is the
    # base config for the tests.
    # This results in the following search order:
    # params -> _caches_setting_base -> base
    base = base or {}
    exclude = exclude or set()
    setting = {k: base.copy() for k in _caches_setting_base if k not in exclude}
    for key, cache_params in setting.items():
        cache_params.update(_caches_setting_base[key])
        cache_params.update(params)
    return setting


class Unpicklable:
    def __getstate__(self):
        raise pickle.PickleError()


def empty_response(request):
    return HttpResponse()


class BaseCacheTests(IsolatedAsyncioTestCase):
    # A common set of tests to apply to all cache backends
    factory = AsyncRequestFactory()

    # Some clients raise custom exceptions when .incr() or .decr() are called
    # with a non-integer value.
    incr_decr_type_error = TypeError

    def setUp(self) -> None:
        if self.__class__ == BaseCacheTests:
            self.skipTest("Base cache test")

    async def asyncTearDown(self):
        await cache.aclear()

    async def test_simple(self):
        # Simple cache set/get works
        await cache.aset("key", "value")
        self.assertEqual(await cache.aget("key"), "value")

    async def test_default_used_when_none_is_set(self):
        """If None is cached, get() returns it instead of the default."""
        await cache.aset("key_default_none", None)
        self.assertIsNone(await cache.aget("key_default_none", default="default"))

    async def test_add(self):
        # A key can be added to a cache
        self.assertIs(await cache.aadd("addkey1", "value"), True)
        self.assertIs(await cache.aadd("addkey1", "newvalue"), False)
        self.assertEqual(await cache.aget("addkey1"), "value")

    async def test_prefix(self):
        # Test for same cache key conflicts between shared backend
        await cache.aset("somekey", "value")

        # should not be set in the prefixed cache
        self.assertIs(caches["prefix"].has_key("somekey"), False)

        await caches["prefix"].set("somekey", "value2")

        self.assertEqual(await cache.aget("somekey"), "value")
        self.assertEqual(await caches["prefix"].aget("somekey"), "value2")

    async def test_non_existent(self):
        """Nonexistent cache keys return as None/default."""
        self.assertIsNone(await cache.aget("does_not_exist"))
        self.assertEqual(await cache.aget("does_not_exist", "bang!"), "bang!")

    async def test_get_many(self):
        # Multiple cache keys can be returned using get_many
        await cache.aset_many({"a": "a", "b": "b", "c": "c", "d": "d"})
        self.assertEqual(
            await cache.aget_many(["a", "c", "d"]), {"a": "a", "c": "c", "d": "d"}
        )
        self.assertEqual(await cache.aget_many(["a", "b", "e"]), {"a": "a", "b": "b"})
        self.assertEqual(
            await cache.aget_many(iter(["a", "b", "e"])), {"a": "a", "b": "b"}
        )
        await cache.aset_many({"x": None, "y": 1})
        self.assertEqual(await cache.aget_many(["x", "y"]), {"x": None, "y": 1})

    async def test_delete(self):
        # Cache keys can be deleted
        await cache.aset_many({"key1": "spam", "key2": "eggs"})
        self.assertEqual(await cache.aget("key1"), "spam")
        self.assertIs(await cache.adelete("key1"), True)
        self.assertIsNone(await cache.aget("key1"))
        self.assertEqual(await cache.aget("key2"), "eggs")

    async def test_delete_nonexistent(self):
        self.assertIs(await cache.adelete("nonexistent_key"), False)

    async def test_has_key(self):
        # The cache can be inspected for cache keys
        await cache.aset("hello1", "goodbye1")
        self.assertIs(await cache.ahas_key("hello1"), True)
        self.assertIs(await cache.ahas_key("goodbye1"), False)
        await cache.aset("no_expiry", "here", None)
        self.assertIs(await cache.ahas_key("no_expiry"), True)
        await cache.aset("null", None)
        self.assertIs(await cache.ahas_key("null"), True)

    async def test_incr(self):
        # Cache values can be incremented
        await cache.aset("answer", 41)
        self.assertEqual(await cache.aincr("answer"), 42)
        self.assertEqual(await cache.aget("answer"), 42)
        self.assertEqual(await cache.aincr("answer", 10), 52)
        self.assertEqual(await cache.aget("answer"), 52)
        self.assertEqual(await cache.aincr("answer", -10), 42)
        with self.assertRaises(ValueError):
            await cache.aincr("does_not_exist")
        with self.assertRaises(ValueError):
            await cache.aincr("does_not_exist", -1)
        await cache.aset("null", None)
        with self.assertRaises(self.incr_decr_type_error):
            await cache.aincr("null")

    async def test_decr(self):
        # Cache values can be decremented
        await cache.aset("answer", 43)
        self.assertEqual(await cache.adecr("answer"), 42)
        self.assertEqual(await cache.aget("answer"), 42)
        self.assertEqual(await cache.adecr("answer", 10), 32)
        self.assertEqual(await cache.aget("answer"), 32)
        self.assertEqual(await cache.adecr("answer", -10), 42)
        with self.assertRaises(ValueError):
            await cache.adecr("does_not_exist")
        with self.assertRaises(ValueError):
            await cache.aincr("does_not_exist", -1)
        await cache.aset("null", None)
        with self.assertRaises(self.incr_decr_type_error):
            await cache.adecr("null")

    async def test_close(self):
        self.assertTrue(hasattr(cache, "close"))
        await cache.aclose()

    async def test_data_types(self):
        # Many different data types can be cached
        tests = {
            "string": "this is a string",
            "int": 42,
            "bool": True,
            "list": [1, 2, 3, 4],
            "tuple": (1, 2, 3, 4),
            "dict": {"A": 1, "B": 2},
            "function": f,
            "class": C,
        }
        for key, value in tests.items():
            with self.subTest(key=key):
                await cache.aset(key, value)
                self.assertEqual(await cache.aget(key), value)

    async def test_cache_read_for_model_instance(self):
        # Don't want fields with callable as default to be called on cache read
        expensive_calculation.num_runs = 0
        await sync_to_async(Poll.objects.all().delete)()
        my_poll = await sync_to_async(Poll.objects.create)(question="Well?")
        self.assertEqual(Poll.objects.count(), 1)
        pub_date = my_poll.pub_date
        await cache.aset("question", my_poll)
        cached_poll = await cache.aget("question")
        self.assertEqual(cached_poll.pub_date, pub_date)
        # We only want the default expensive calculation run once
        self.assertEqual(expensive_calculation.num_runs, 1)

    async def test_cache_write_for_model_instance_with_deferred(self):
        # Don't want fields with callable as default to be called on cache write
        expensive_calculation.num_runs = 0
        await sync_to_async(Poll.objects.all().delete)()
        await sync_to_async(Poll.objects.create)(question="What?")
        self.assertEqual(expensive_calculation.num_runs, 1)
        defer_qs = Poll.objects.defer("question")
        self.assertEqual(defer_qs.count(), 1)
        self.assertEqual(expensive_calculation.num_runs, 1)
        await cache.aset("deferred_queryset", defer_qs)
        # cache set should not re-evaluate default functions
        self.assertEqual(expensive_calculation.num_runs, 1)

    async def test_cache_read_for_model_instance_with_deferred(self):
        # Don't want fields with callable as default to be called on cache read
        expensive_calculation.num_runs = 0
        await sync_to_async(Poll.objects.all().delete)()
        await sync_to_async(Poll.objects.create)(question="What?")
        self.assertEqual(expensive_calculation.num_runs, 1)
        defer_qs = Poll.objects.defer("question")
        self.assertEqual(await sync_to_async(defer_qs.count)(), 1)
        await cache.aset("deferred_queryset", await sync_to_async(defer_qs)())
        self.assertEqual(expensive_calculation.num_runs, 1)
        runs_before_cache_read = expensive_calculation.num_runs
        await cache.aget("deferred_queryset")
        # We only want the default expensive calculation run on creation and set
        self.assertEqual(expensive_calculation.num_runs, runs_before_cache_read)

    async def test_expiration(self):
        # Cache values can be set to expire
        await cache.aset("expire1", "very quickly", 1)
        await cache.aset("expire2", "very quickly", 1)
        await cache.aset("expire3", "very quickly", 1)

        await asyncio.sleep(2)
        self.assertIsNone(await cache.aget("expire1"))

        self.assertIs(await cache.aadd("expire2", "newvalue"), True)
        self.assertEqual(await cache.aget("expire2"), "newvalue")
        self.assertIs(await cache.ahas_key("expire3"), False)

    async def test_touch(self):
        # cache.touch() updates the timeout.
        await cache.aset("expire1", "very quickly", timeout=1)
        self.assertIs(await cache.atouch("expire1", timeout=4), True)
        await asyncio.sleep(2)
        self.assertIs(await cache.ahas_key("expire1"), True)
        await asyncio.sleep(3)
        self.assertIs(await cache.ahas_key("expire1"), False)
        # cache.touch() works without the timeout argument.
        await cache.aset("expire1", "very quickly", timeout=1)
        self.assertIs(await cache.atouch("expire1"), True)
        await asyncio.sleep(2)
        self.assertIs(await cache.ahas_key("expire1"), True)

        self.assertIs(await cache.atouch("nonexistent"), False)

    async def test_unicode(self):
        # Unicode values can be cached
        stuff = {
            "ascii": "ascii_value",
            "unicode_ascii": "Iñtërnâtiônàlizætiøn1",
            "Iñtërnâtiônàlizætiøn": "Iñtërnâtiônàlizætiøn2",
            "ascii2": {"x": 1},
        }
        # Test `set`
        for (key, value) in stuff.items():
            with self.subTest(key=key):
                await cache.aset(key, value)
                self.assertEqual(await cache.aget(key), value)

        # Test `add`
        for (key, value) in stuff.items():
            with self.subTest(key=key):
                self.assertIs(await cache.adelete(key), True)
                self.assertIs(await cache.aadd(key, value), True)
                self.assertEqual(await cache.aget(key), value)

        # Test `set_many`
        for (key, value) in stuff.items():
            self.assertIs(await cache.adelete(key), True)
        await cache.aset_many(stuff)
        for (key, value) in stuff.items():
            with self.subTest(key=key):
                self.assertEqual(await cache.aget(key), value)

    async def test_binary_string(self):
        # Binary strings should be cacheable
        from zlib import compress, decompress

        value = "value_to_be_compressed"
        compressed_value = compress(value.encode())

        # Test set
        await cache.aset("binary1", compressed_value)
        compressed_result = await cache.aget("binary1")
        self.assertEqual(compressed_value, compressed_result)
        self.assertEqual(value, decompress(compressed_result).decode())

        # Test add
        self.assertIs(await cache.aadd("binary1-add", compressed_value), True)
        compressed_result = await cache.aget("binary1-add")
        self.assertEqual(compressed_value, compressed_result)
        self.assertEqual(value, decompress(compressed_result).decode())

        # Test set_many
        await cache.aset_many({"binary1-set_many": compressed_value})
        compressed_result = await cache.aget("binary1-set_many")
        self.assertEqual(compressed_value, compressed_result)
        self.assertEqual(value, decompress(compressed_result).decode())

    async def test_set_many(self):
        # Multiple keys can be set using set_many
        await cache.aset_many({"key1": "spam", "key2": "eggs"})
        self.assertEqual(await cache.aget("key1"), "spam")
        self.assertEqual(await cache.aget("key2"), "eggs")

    async def test_set_many_returns_empty_list_on_success(self):
        """set_many() returns an empty list when all keys are inserted."""
        failing_keys = await cache.aset_many({"key1": "spam", "key2": "eggs"})
        self.assertEqual(failing_keys, [])

    async def test_set_many_expiration(self):
        # set_many takes a second ``timeout`` parameter
        await cache.aset_many({"key1": "spam", "key2": "eggs"}, 1)
        await asyncio.sleep(2)
        self.assertIsNone(await cache.aget("key1"))
        self.assertIsNone(await cache.aget("key2"))

    async def test_set_many_empty_data(self):
        self.assertEqual(await cache.aset_many({}), [])

    async def test_delete_many(self):
        # Multiple keys can be deleted using delete_many
        await cache.aset_many({"key1": "spam", "key2": "eggs", "key3": "ham"})
        await cache.adelete_many(["key1", "key2"])
        self.assertIsNone(await cache.aget("key1"))
        self.assertIsNone(await cache.aget("key2"))
        self.assertEqual(await cache.aget("key3"), "ham")

    async def test_delete_many_no_keys(self):
        self.assertIsNone(await cache.adelete_many([]))

    async def test_clear(self):
        # The cache can be emptied using clear
        await cache.aset_many({"key1": "spam", "key2": "eggs"})
        await cache.aclear()
        self.assertIsNone(await cache.aget("key1"))
        self.assertIsNone(await cache.aget("key2"))

    async def test_long_timeout(self):
        """
        Follow memcached's convention where a timeout greater than 30 days is
        treated as an absolute expiration timestamp instead of a relative
        offset (#12399).
        """
        await cache.aset("key1", "eggs", 60 * 60 * 24 * 30 + 1)  # 30 days + 1 second
        self.assertEqual(await cache.aget("key1"), "eggs")

        self.assertIs(await cache.aadd("key2", "ham", 60 * 60 * 24 * 30 + 1), True)
        self.assertEqual(await cache.aget("key2"), "ham")

        await cache.aset_many(
            {"key3": "sausage", "key4": "lobster bisque"}, 60 * 60 * 24 * 30 + 1
        )
        self.assertEqual(await cache.aget("key3"), "sausage")
        self.assertEqual(await cache.aget("key4"), "lobster bisque")

    async def test_forever_timeout(self):
        """
        Passing in None into timeout results in a value that is cached forever
        """
        await cache.aset("key1", "eggs", None)
        self.assertEqual(await cache.aget("key1"), "eggs")

        self.assertIs(await cache.aadd("key2", "ham", None), True)
        self.assertEqual(await cache.aget("key2"), "ham")
        self.assertIs(await cache.aadd("key1", "new eggs", None), False)
        self.assertEqual(await cache.aget("key1"), "eggs")

        await cache.aset_many({"key3": "sausage", "key4": "lobster bisque"}, None)
        self.assertEqual(await cache.aget("key3"), "sausage")
        self.assertEqual(await cache.aget("key4"), "lobster bisque")

        await cache.aset("key5", "belgian fries", timeout=1)
        self.assertIs(cache.touch("key5", timeout=None), True)
        await asyncio.sleep(2)
        self.assertEqual(await cache.aget("key5"), "belgian fries")

    async def test_zero_timeout(self):
        """
        Passing in zero into timeout results in a value that is not cached
        """
        await cache.aset("key1", "eggs", 0)
        self.assertIsNone(await cache.aget("key1"))

        self.assertIs(await cache.aadd("key2", "ham", 0), True)
        self.assertIsNone(await cache.aget("key2"))

        await cache.aset_many({"key3": "sausage", "key4": "lobster bisque"}, 0)
        self.assertIsNone(await cache.aget("key3"))
        self.assertIsNone(await cache.aget("key4"))

        await cache.aset("key5", "belgian fries", timeout=5)
        self.assertIs(await cache.atouch("key5", timeout=0), True)
        self.assertIsNone(await cache.aget("key5"))

    async def test_float_timeout(self):
        # Make sure a timeout given as a float doesn't crash anything.
        await cache.aset("key1", "spam", 100.2)
        self.assertEqual(await cache.aget("key1"), "spam")

    async def _perform_cull_test(self, cull_cache_name, initial_count, final_count):
        try:
            cull_cache = caches[cull_cache_name]
        except InvalidCacheBackendError:
            self.skipTest("Culling isn't implemented.")

        # Create initial cache key entries. This will overflow the cache,
        # causing a cull.
        for i in range(1, initial_count):
            await cull_cache.aset("cull%d" % i, "value", 1000)
        count = 0
        # Count how many keys are left in the cache.
        for i in range(1, initial_count):
            if await cull_cache.ahas_key("cull%d" % i):
                count += 1
        self.assertEqual(count, final_count)

    async def test_cull(self):
        await self._perform_cull_test("cull", 50, 29)

    async def test_zero_cull(self):
        await self._perform_cull_test("zero_cull", 50, 19)

    async def test_cull_delete_when_store_empty(self):
        try:
            cull_cache = caches["cull"]
        except InvalidCacheBackendError:
            self.skipTest("Culling isn't implemented.")
        old_max_entries = cull_cache._max_entries
        # Force _cull to delete on first cached record.
        cull_cache._max_entries = -1
        try:
            await cull_cache.aset("force_cull_delete", "value", 1000)
            self.assertIs(await cull_cache.ahas_key("force_cull_delete"), True)
        finally:
            cull_cache._max_entries = old_max_entries

    async def _perform_invalid_key_test(self, key, expected_warning, key_func=None):
        """
        All the builtin backends should warn (except memcached that should
        error) on keys that would be refused by memcached. This encourages
        portable caching code without making it too difficult to use production
        backends with more liberal key rules. Refs #6447.
        """
        # mimic custom ``make_key`` method being defined since the default will
        # never show the below warnings
        def func(key, *args):
            return key

        old_func = cache.key_func
        cache.akey_func = key_func or func

        tests = [
            ("aadd", [key, 1]),
            ("aget", [key]),
            ("aset", [key, 1]),
            ("aincr", [key]),
            ("adecr", [key]),
            ("atouch", [key]),
            ("adelete", [key]),
            ("aget_many", [[key, "b"]]),
            ("aset_many", [{key: 1, "b": 2}]),
            ("adelete_many", [[key, "b"]]),
        ]
        try:
            for operation, args in tests:
                with self.subTest(operation=operation):
                    with self.assertWarns(CacheKeyWarning) as cm:
                        await getattr(cache, operation)(*args)
                    self.assertEqual(str(cm.warning), expected_warning)
        finally:
            cache.akey_func = old_func

    async def test_invalid_key_characters(self):
        # memcached doesn't allow whitespace or control characters in keys.
        key = "key with spaces and 清"
        await self._perform_invalid_key_test(key, KEY_ERRORS_WITH_MEMCACHED_MSG % key)

    async def test_invalid_key_length(self):
        # memcached limits key length to 250.
        key = ("a" * 250) + "清"
        expected_warning = (
            "Cache key will cause errors if used with memcached: "
            "%r (longer than %s)" % (key, 250)
        )
        await self._perform_invalid_key_test(key, expected_warning)

    async def test_invalid_with_version_key_length(self):
        # Custom make_key() that adds a version to the key and exceeds the
        # limit.
        def key_func(key, *args):
            return key + ":1"

        key = "a" * 249
        expected_warning = (
            "Cache key will cause errors if used with memcached: "
            "%r (longer than %s)" % (key_func(key), 250)
        )
        await self._perform_invalid_key_test(key, expected_warning, key_func=key_func)

    async def test_cache_versioning_get_set(self):
        # set, using default version = 1
        await cache.aset("answer1", 42)
        self.assertEqual(await cache.aget("answer1"), 42)
        self.assertEqual(await cache.aget("answer1", version=1), 42)
        self.assertIsNone(await cache.aget("answer1", version=2))

        self.assertIsNone(await caches["v2"].aget("answer1"))
        self.assertEqual(await caches["v2"].aget("answer1", version=1), 42)
        self.assertIsNone(await caches["v2"].aget("answer1", version=2))

        # set, default version = 1, but manually override version = 2
        await cache.aset("answer2", 42, version=2)
        self.assertIsNone(await cache.aget("answer2"))
        self.assertIsNone(await cache.aget("answer2", version=1))
        self.assertEqual(await cache.aget("answer2", version=2), 42)

        self.assertEqual(await caches["v2"].aget("answer2"), 42)
        self.assertIsNone(await caches["v2"].aget("answer2", version=1))
        self.assertEqual(await caches["v2"].aget("answer2", version=2), 42)

        # v2 set, using default version = 2
        await caches["v2"].aset("answer3", 42)
        self.assertIsNone(await cache.aget("answer3"))
        self.assertIsNone(await cache.aget("answer3", version=1))
        self.assertEqual(await cache.aget("answer3", version=2), 42)

        self.assertEqual(await caches["v2"].aget("answer3"), 42)
        self.assertIsNone(await caches["v2"].aget("answer3", version=1))
        self.assertEqual(await caches["v2"].aget("answer3", version=2), 42)

        # v2 set, default version = 2, but manually override version = 1
        await caches["v2"].aset("answer4", 42, version=1)
        self.assertEqual(await cache.aget("answer4"), 42)
        self.assertEqual(await cache.aget("answer4", version=1), 42)
        self.assertIsNone(await cache.aget("answer4", version=2))

        self.assertIsNone(await caches["v2"].aget("answer4"))
        self.assertEqual(await caches["v2"].aget("answer4", version=1), 42)
        self.assertIsNone(await caches["v2"].aget("answer4", version=2))

    async def test_cache_versioning_add(self):

        # add, default version = 1, but manually override version = 2
        self.assertIs(await cache.aadd("answer1", 42, version=2), True)
        self.assertIsNone(await cache.aget("answer1", version=1))
        self.assertEqual(await cache.aget("answer1", version=2), 42)

        self.assertIs(await cache.aadd("answer1", 37, version=2), False)
        self.assertIsNone(await cache.aget("answer1", version=1))
        self.assertEqual(await cache.aget("answer1", version=2), 42)

        self.assertIs(await cache.aadd("answer1", 37, version=1), True)
        self.assertEqual(await cache.aget("answer1", version=1), 37)
        self.assertEqual(await cache.aget("answer1", version=2), 42)

        # v2 add, using default version = 2
        self.assertIs(await caches["v2"].aadd("answer2", 42), True)
        self.assertIsNone(await cache.aget("answer2", version=1))
        self.assertEqual(await cache.aget("answer2", version=2), 42)

        self.assertIs(await caches["v2"].aadd("answer2", 37), False)
        self.assertIsNone(await cache.aget("answer2", version=1))
        self.assertEqual(await cache.aget("answer2", version=2), 42)

        self.assertIs(await caches["v2"].aadd("answer2", 37, version=1), True)
        self.assertEqual(await cache.aget("answer2", version=1), 37)
        self.assertEqual(await cache.aget("answer2", version=2), 42)

        # v2 add, default version = 2, but manually override version = 1
        self.assertIs(await caches["v2"].aadd("answer3", 42, version=1), True)
        self.assertEqual(await cache.aget("answer3", version=1), 42)
        self.assertIsNone(await cache.aget("answer3", version=2))

        self.assertIs(await caches["v2"].aadd("answer3", 37, version=1), False)
        self.assertEqual(await cache.aget("answer3", version=1), 42)
        self.assertIsNone(await cache.aget("answer3", version=2))

        self.assertIs(await caches["v2"].aadd("answer3", 37), True)
        self.assertEqual(await cache.aget("answer3", version=1), 42)
        self.assertEqual(await cache.aget("answer3", version=2), 37)

    async def test_cache_versioning_has_key(self):
        await cache.aset("answer1", 42)

        # has_key
        self.assertIs(await cache.ahas_key("answer1"), True)
        self.assertIs(await cache.ahas_key("answer1", version=1), True)
        self.assertIs(await cache.ahas_key("answer1", version=2), False)

        self.assertIs(await caches["v2"].ahas_key("answer1"), False)
        self.assertIs(await caches["v2"].ahas_key("answer1", version=1), True)
        self.assertIs(await caches["v2"].ahas_key("answer1", version=2), False)

    async def test_cache_versioning_delete(self):
        await cache.aset("answer1", 37, version=1)
        await cache.aset("answer1", 42, version=2)
        self.assertIs(await cache.adelete("answer1"), True)
        self.assertIsNone(await cache.aget("answer1", version=1))
        self.assertEqual(await cache.aget("answer1", version=2), 42)

        await cache.aset("answer2", 37, version=1)
        await cache.aset("answer2", 42, version=2)
        self.assertIs(await cache.adelete("answer2", version=2), True)
        self.assertEqual(await cache.aget("answer2", version=1), 37)
        self.assertIsNone(await cache.aget("answer2", version=2))

        await cache.aset("answer3", 37, version=1)
        await cache.aset("answer3", 42, version=2)
        self.assertIs(await caches["v2"].adelete("answer3"), True)
        self.assertEqual(await cache.aget("answer3", version=1), 37)
        self.assertIsNone(await cache.aget("answer3", version=2))

        await cache.aset("answer4", 37, version=1)
        await cache.aset("answer4", 42, version=2)
        self.assertIs(await caches["v2"].adelete("answer4", version=1), True)
        self.assertIsNone(await cache.aget("answer4", version=1))
        self.assertEqual(await cache.aget("answer4", version=2), 42)

    async def test_cache_versioning_incr_decr(self):
        await cache.aset("answer1", 37, version=1)
        await cache.aset("answer1", 42, version=2)
        self.assertEqual(await cache.aincr("answer1"), 38)
        self.assertEqual(await cache.aget("answer1", version=1), 38)
        self.assertEqual(await cache.aget("answer1", version=2), 42)
        self.assertEqual(await cache.adecr("answer1"), 37)
        self.assertEqual(await cache.aget("answer1", version=1), 37)
        self.assertEqual(await cache.aget("answer1", version=2), 42)

        await cache.aset("answer2", 37, version=1)
        await cache.aset("answer2", 42, version=2)
        self.assertEqual(await cache.aincr("answer2", version=2), 43)
        self.assertEqual(await cache.aget("answer2", version=1), 37)
        self.assertEqual(await cache.aget("answer2", version=2), 43)
        self.assertEqual(await cache.adecr("answer2", version=2), 42)
        self.assertEqual(await cache.aget("answer2", version=1), 37)
        self.assertEqual(await cache.aget("answer2", version=2), 42)

        await cache.aset("answer3", 37, version=1)
        await cache.aset("answer3", 42, version=2)
        self.assertEqual(await caches["v2"].aincr("answer3"), 43)
        self.assertEqual(await cache.aget("answer3", version=1), 37)
        self.assertEqual(await cache.aget("answer3", version=2), 43)
        self.assertEqual(await caches["v2"].adecr("answer3"), 42)
        self.assertEqual(await cache.aget("answer3", version=1), 37)
        self.assertEqual(await cache.aget("answer3", version=2), 42)

        await cache.aset("answer4", 37, version=1)
        await cache.aset("answer4", 42, version=2)
        self.assertEqual(await caches["v2"].aincr("answer4", version=1), 38)
        self.assertEqual(await cache.aget("answer4", version=1), 38)
        self.assertEqual(await cache.aget("answer4", version=2), 42)
        self.assertEqual(await caches["v2"].adecr("answer4", version=1), 37)
        self.assertEqual(await cache.aget("answer4", version=1), 37)
        self.assertEqual(await cache.aget("answer4", version=2), 42)

    async def test_cache_versioning_get_set_many(self):
        # set, using default version = 1
        await cache.aset_many({"ford1": 37, "arthur1": 42})
        self.assertEqual(
            await cache.aget_many(["ford1", "arthur1"]), {"ford1": 37, "arthur1": 42}
        )
        self.assertEqual(
            await cache.aget_many(["ford1", "arthur1"], version=1),
            {"ford1": 37, "arthur1": 42},
        )
        self.assertEqual(await cache.aget_many(["ford1", "arthur1"], version=2), {})

        self.assertEqual(await caches["v2"].aget_many(["ford1", "arthur1"]), {})
        self.assertEqual(
            await caches["v2"].aget_many(["ford1", "arthur1"], version=1),
            {"ford1": 37, "arthur1": 42},
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford1", "arthur1"], version=2), {}
        )

        # set, default version = 1, but manually override version = 2
        await cache.aset_many({"ford2": 37, "arthur2": 42}, version=2)
        self.assertEqual(await cache.aget_many(["ford2", "arthur2"]), {})
        self.assertEqual(await cache.aget_many(["ford2", "arthur2"], version=1), {})
        self.assertEqual(
            await cache.aget_many(["ford2", "arthur2"], version=2),
            {"ford2": 37, "arthur2": 42},
        )

        self.assertEqual(
            await caches["v2"].aget_many(["ford2", "arthur2"]),
            {"ford2": 37, "arthur2": 42},
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford2", "arthur2"], version=1), {}
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford2", "arthur2"], version=2),
            {"ford2": 37, "arthur2": 42},
        )

        # v2 set, using default version = 2
        await caches["v2"].aset_many({"ford3": 37, "arthur3": 42})
        self.assertEqual(await cache.aget_many(["ford3", "arthur3"]), {})
        self.assertEqual(await cache.aget_many(["ford3", "arthur3"], version=1), {})
        self.assertEqual(
            await cache.aget_many(["ford3", "arthur3"], version=2),
            {"ford3": 37, "arthur3": 42},
        )

        self.assertEqual(
            await caches["v2"].aget_many(["ford3", "arthur3"]),
            {"ford3": 37, "arthur3": 42},
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford3", "arthur3"], version=1), {}
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford3", "arthur3"], version=2),
            {"ford3": 37, "arthur3": 42},
        )

        # v2 set, default version = 2, but manually override version = 1
        await caches["v2"].aset_many({"ford4": 37, "arthur4": 42}, version=1)
        self.assertEqual(
            await cache.aget_many(["ford4", "arthur4"]), {"ford4": 37, "arthur4": 42}
        )
        self.assertEqual(
            await cache.aget_many(["ford4", "arthur4"], version=1),
            {"ford4": 37, "arthur4": 42},
        )
        self.assertEqual(await cache.aget_many(["ford4", "arthur4"], version=2), {})

        self.assertEqual(await caches["v2"].aget_many(["ford4", "arthur4"]), {})
        self.assertEqual(
            await caches["v2"].aget_many(["ford4", "arthur4"], version=1),
            {"ford4": 37, "arthur4": 42},
        )
        self.assertEqual(
            await caches["v2"].aget_many(["ford4", "arthur4"], version=2), {}
        )

    async def test_incr_version(self):
        await cache.aset("answer", 42, version=2)
        self.assertIsNone(await cache.aget("answer"))
        self.assertIsNone(await cache.aget("answer", version=1))
        self.assertEqual(await cache.aget("answer", version=2), 42)
        self.assertIsNone(await cache.aget("answer", version=3))

        self.assertEqual(await cache.aincr_version("answer", version=2), 3)
        self.assertIsNone(await cache.aget("answer"))
        self.assertIsNone(await cache.aget("answer", version=1))
        self.assertIsNone(await cache.aget("answer", version=2))
        self.assertEqual(await cache.aget("answer", version=3), 42)

        await caches["v2"].aset("answer2", 42)
        self.assertEqual(await caches["v2"].aget("answer2"), 42)
        self.assertIsNone(await caches["v2"].aget("answer2", version=1))
        self.assertEqual(await caches["v2"].aget("answer2", version=2), 42)
        self.assertIsNone(await caches["v2"].aget("answer2", version=3))

        self.assertEqual(await caches["v2"].aincr_version("answer2"), 3)
        self.assertIsNone(await caches["v2"].aget("answer2"))
        self.assertIsNone(await caches["v2"].aget("answer2", version=1))
        self.assertIsNone(await caches["v2"].aget("answer2", version=2))
        self.assertEqual(await caches["v2"].aget("answer2", version=3), 42)

        with self.assertRaises(ValueError):
            await cache.aincr_version("does_not_exist")

        await cache.aset("null", None)
        self.assertEqual(await cache.aincr_version("null"), 2)

    async def test_decr_version(self):
        await cache.aset("answer", 42, version=2)
        self.assertIsNone(await cache.aget("answer"))
        self.assertIsNone(await cache.aget("answer", version=1))
        self.assertEqual(await cache.aget("answer", version=2), 42)

        self.assertEqual(await cache.adecr_version("answer", version=2), 1)
        self.assertEqual(await cache.aget("answer"), 42)
        self.assertEqual(await cache.aget("answer", version=1), 42)
        self.assertIsNone(await cache.aget("answer", version=2))

        await caches["v2"].aset("answer2", 42)
        self.assertEqual(await caches["v2"].aget("answer2"), 42)
        self.assertIsNone(await caches["v2"].aget("answer2", version=1))
        self.assertEqual(await caches["v2"].aget("answer2", version=2), 42)

        self.assertEqual(await caches["v2"].adecr_version("answer2"), 1)
        self.assertIsNone(await caches["v2"].aget("answer2"))
        self.assertEqual(await caches["v2"].aget("answer2", version=1), 42)
        self.assertIsNone(await caches["v2"].aget("answer2", version=2))

        with self.assertRaises(ValueError):
            await cache.adecr_version("does_not_exist", version=2)

        await cache.aset("null", None, version=2)
        self.assertEqual(await cache.adecr_version("null", version=2), 1)

    async def test_custom_key_func(self):
        # Two caches with different key functions aren't visible to each other
        await cache.aset("answer1", 42)
        self.assertEqual(await cache.aget("answer1"), 42)
        self.assertIsNone(await caches["custom_key"].aget("answer1"))
        self.assertIsNone(await caches["custom_key2"].aget("answer1"))

        await caches["custom_key"].set("answer2", 42)
        self.assertIsNone(await cache.aget("answer2"))
        self.assertEqual(await caches["custom_key"].aget("answer2"), 42)
        self.assertEqual(await caches["custom_key2"].aget("answer2"), 42)

    @override_settings(CACHE_MIDDLEWARE_ALIAS=DEFAULT_CACHE_ALIAS)
    async def test_cache_write_unpicklable_object(self):
        fetch_middleware = FetchFromCacheMiddleware(empty_response)

        request = await self.factory.get("/cache/test")
        request._cache_update_cache = True
        get_cache_data = FetchFromCacheMiddleware(empty_response).process_request(
            request
        )
        self.assertIsNone(get_cache_data)

        content = "Testing cookie serialization."

        def get_response(req):
            response = HttpResponse(content)
            response.set_cookie("foo", "bar")
            return response

        update_middleware = UpdateCacheMiddleware(get_response)
        response = update_middleware(request)

        get_cache_data = fetch_middleware.process_request(request)
        self.assertIsNotNone(get_cache_data)
        self.assertEqual(get_cache_data.content, content.encode())
        self.assertEqual(get_cache_data.cookies, response.cookies)

        UpdateCacheMiddleware(lambda req: get_cache_data)(request)
        get_cache_data = fetch_middleware.process_request(request)
        self.assertIsNotNone(get_cache_data)
        self.assertEqual(get_cache_data.content, content.encode())
        self.assertEqual(get_cache_data.cookies, response.cookies)

    async def test_add_fail_on_pickleerror(self):
        # Shouldn't fail silently if trying to cache an unpicklable type.
        with self.assertRaises(pickle.PickleError):
            await cache.aadd("unpicklable", Unpicklable())

    async def test_set_fail_on_pickleerror(self):
        with self.assertRaises(pickle.PickleError):
            await cache.aset("unpicklable", Unpicklable())

    async def test_get_or_set(self):
        self.assertIsNone(await cache.aget("projector"))
        self.assertEqual(await cache.aget_or_set("projector", 42), 42)
        self.assertEqual(await cache.aget("projector"), 42)
        self.assertIsNone(await cache.aget_or_set("null", None))
        # Previous get_or_set() stores None in the cache.
        self.assertIsNone(await cache.aget("null", "default"))

    async def test_get_or_set_callable(self):
        def my_callable():
            return "value"

        self.assertEqual(await cache.aget_or_set("mykey", my_callable), "value")
        self.assertEqual(await cache.aget_or_set("mykey", my_callable()), "value")

        self.assertIsNone(await cache.aget_or_set("null", lambda: None))
        # Previous get_or_set() stores None in the cache.
        self.assertIsNone(await cache.aget("null", "default"))

    async def test_get_or_set_version(self):
        msg = "get_or_set() missing 1 required positional argument: 'default'"
        self.assertEqual(await cache.aget_or_set("brian", 1979, version=2), 1979)
        with self.assertRaisesMessage(TypeError, msg):
            await cache.aget_or_set("brian")
        with self.assertRaisesMessage(TypeError, msg):
            await cache.aget_or_set("brian", version=1)
        self.assertIsNone(await cache.aget("brian", version=1))
        self.assertEqual(await cache.aget_or_set("brian", 42, version=1), 42)
        self.assertEqual(await cache.aget_or_set("brian", 1979, version=2), 1979)
        self.assertIsNone(await cache.aget("brian", version=3))

    async def test_get_or_set_racing(self):
        with mock.patch(
            "%s.%s" % (settings.CACHES["default"]["BACKEND"], "add")
        ) as cache_add:
            # Simulate cache.add() failing to add a value. In that case, the
            # default value should be returned.
            cache_add.return_value = False
            self.assertEqual(await cache.aget_or_set("key", "default"), "default")


configured_caches = {}
for _cache_params in settings.CACHES.values():
    configured_caches[_cache_params["BACKEND"]] = _cache_params


RedisCache_params = configured_caches.get(
    "django_async_redis.client.core.AsyncRedisCache"
)

# The redis backend does not support cull-related options like `MAX_ENTRIES`.
redis_excluded_caches = {"cull", "zero_cull"}


@unittest.skipUnless(RedisCache_params, "Redis backend not configured")
@override_settings(
    CACHES=caches_setting_for_tests(
        base=RedisCache_params,
        exclude=redis_excluded_caches,
    )
)
class RedisCacheTests(BaseCacheTests, TestCase):
    def setUp(self):
        from redis import asyncio as redis_asyncio

        super().setUp()
        self.lib = redis_asyncio

    @property
    def incr_decr_type_error(self):
        return self.lib.ResponseError

    async def test_incr_write_connection(self):
        await cache.aset("number", 42)
        with mock.patch(
            "django.core.cache.backends.redis.RedisCacheClient.get_client"
        ) as mocked_get_client:
            await cache.aincr("number")
            self.assertEqual(mocked_get_client.call_args.kwargs, {"write": True})

    async def test_cache_client_class(self):
        self.assertIs(cache._class, AsyncRedisCacheClient)
        self.assertIsInstance(cache._cache, AsyncRedisCacheClient)

    async def test_get_backend_timeout_method(self):
        positive_timeout = 10
        positive_backend_timeout = cache.get_backend_timeout(positive_timeout)
        self.assertEqual(positive_backend_timeout, positive_timeout)

        negative_timeout = -5
        negative_backend_timeout = cache.get_backend_timeout(negative_timeout)
        self.assertEqual(negative_backend_timeout, 0)

        none_timeout = None
        none_backend_timeout = cache.get_backend_timeout(none_timeout)
        self.assertIsNone(none_backend_timeout)

    async def test_get_connection_pool_index(self):
        pool_index = cache._cache._get_connection_pool_index(write=True)
        self.assertEqual(pool_index, 0)
        pool_index = cache._cache._get_connection_pool_index(write=False)
        if len(cache._cache._servers) == 1:
            self.assertEqual(pool_index, 0)
        else:
            self.assertGreater(pool_index, 0)
            self.assertLess(pool_index, len(cache._cache._servers))

    async def test_get_connection_pool(self):
        pool = cache._cache._get_connection_pool(write=True)
        self.assertIsInstance(pool, self.lib.ConnectionPool)

        pool = cache._cache._get_connection_pool(write=False)
        self.assertIsInstance(pool, self.lib.ConnectionPool)

    async def test_get_client(self):
        self.assertIsInstance(await cache._cache.get_client(), self.lib.Redis)

    def test_serializer_dumps(self):
        self.assertEqual(cache._cache._serializer.dumps(123), 123)
        self.assertIsInstance(cache._cache._serializer.dumps(True), bytes)
        self.assertIsInstance(cache._cache._serializer.dumps("abc"), bytes)

    @override_settings(
        CACHES=caches_setting_for_tests(
            base=RedisCache_params,
            exclude=redis_excluded_caches,
            OPTIONS={
                "db": 5,
                "socket_timeout": 0.1,
                "retry_on_timeout": True,
            },
        )
    )
    async def test_redis_pool_options(self):
        pool = cache._cache._get_connection_pool(write=False)
        self.assertEqual(pool.connection_kwargs["db"], 5)
        self.assertEqual(pool.connection_kwargs["socket_timeout"], 0.1)
        self.assertIs(pool.connection_kwargs["retry_on_timeout"], True)
