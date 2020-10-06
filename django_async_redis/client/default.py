import random
import re
import socket
from collections import OrderedDict
from typing import List, Optional, Tuple, Union

from aioredis import Redis
from aioredis.abc import AbcPool
from aioredis.errors import ConnectionClosedError, ReplyError
from django.conf import settings
from django.core.cache.backends.base import DEFAULT_TIMEOUT, get_key_func
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .. import pool
from ..compressors.base import BaseCompressor
from ..exceptions import CompressorError, ConnectionInterrupted
from ..serializers.base import BaseSerializer
from ..util import CacheKey


_main_exceptions = (
    ConnectionClosedError,
    ConnectionResetError,
    ReplyError,
    socket.timeout,
)

special_re = re.compile("([*?[])")


def glob_escape(s):
    return special_re.sub(r"[\1]", s)


class DefaultClient:
    def __init__(self, server: Union[list, tuple, set], params: dict, backend):
        self._backend = backend
        self._server = server
        self._params = params

        self.reverse_key = get_key_func(
            params.get("REVERSE_KEY_FUNCTION")
            or "django_async_redis.util.default_reverse_key"
        )

        if not self._server:
            raise ImproperlyConfigured("Missing connections string")

        if not isinstance(self._server, (list, tuple, set)):
            self._server = self._server.split(",")

        self._clients: List[Optional[Redis]] = [None] * len(self._server)
        self._options = params.get("OPTIONS", {})
        self._replica_read_only = self._options.get("REPLICA_READ_ONLY", True)

        serializer_path = self._options.get(
            "SERIALIZER", "django_async_redis.serializers.pickle.PickleSerializer"
        )
        serializer_cls = import_string(serializer_path)

        compressor_path = self._options.get(
            "COMPRESSOR", "django_async_redis.compressors.identity.IdentityCompressor"
        )
        compressor_cls = import_string(compressor_path)

        self._serializer: BaseSerializer = serializer_cls(options=self._options)
        self._compressor: BaseCompressor = compressor_cls(options=self._options)

        self.connection_factory = pool.get_connection_factory(options=self._options)

    def __contains__(self, key):
        return self.has_key(key)

    async def close_connections(self) -> None:
        """
        Clears pool connections. Mostly used for testing with pytest
        https://aioredis.readthedocs.io/en/v1.3.0/api_reference.html#aioredis.ConnectionsPool.clear
        """
        client = await self.get_client(write=False)
        assert isinstance(
            client.connection, AbcPool
        ), "Must subclass aioredis.abc.AbcPool"
        await client.connection.clear()

    def get_next_client_index(self, write=True, tried=()) -> int:
        """
        Return the next index for read client. This function implements a default
        behavior for getting the next read client for a replication setup.
        Overwrite this function if you want a specific behavior.
        """
        if tried and len(tried) < len(self._server):
            not_tried = [i for i in range(0, len(self._server)) if i not in tried]
            return random.choice(not_tried)

        if write or len(self._server) == 1:
            return 0

        return random.randint(1, len(self._server) - 1)

    async def get_client(
        self, write=True, tried=(), show_index=False
    ) -> Union[Redis, Tuple[Redis, int]]:
        """
        Method used to obtain a raw redis client.
        This function is used by almost all cache backend
        operations to obtain a native redis client/connection
        instance.
        """
        index = self.get_next_client_index(write=write, tried=tried or [])

        if self._clients[index] is None:
            self._clients[index] = await self.connect(index)

        if show_index:
            return self._clients[index], index
        else:
            return self._clients[index]

    async def connect(self, index=0) -> Redis:
        """
        Given a connection index, returns a new raw redis client/connection
        instance. Index is used for replication setups and indicates that
        connection string should be used. In normal setups, index is 0.
        """
        return await self.connection_factory.connect(self._server[index])

    async def set(
        self,
        key,
        value,
        timeout: Optional[int] = DEFAULT_TIMEOUT,
        version=None,
        client: Redis = None,
        nx: bool = False,
        xx: bool = False,
    ):
        """
        Persist a value to the cache, and set an optional expiration time.

        Also supports optional nx parameter. If set to True - will use redis
        setnx instead of set. Note: xx overrides nx in aioredis.
        """
        nkey = self.make_key(key, version=version)
        nvalue = self.encode(value)

        if timeout is DEFAULT_TIMEOUT:
            timeout = self._backend.default_timeout

        original_client = client
        tried = []
        while True:
            try:
                if client is None:
                    client, index = await self.get_client(
                        write=True, tried=tried, show_index=True
                    )
                with (await client) as c:
                    if timeout is not None:
                        # Convert to milliseconds
                        timeout = int(timeout * 1000)

                        if timeout <= 0:
                            if nx:
                                # Using negative timeouts when nx is True should
                                # not expire (in our case delete) the value if it exists.
                                # Obviously expire not existent value is noop.
                                return not await self.has_key(
                                    key, version=version, client=client
                                )
                            else:
                                # redis doesn't support negative timeouts in ex flags
                                # so it seems that it's better to just delete the key
                                # than to set it and than expire in a pipeline
                                return await self.delete(
                                    key, client=client, version=version
                                )

                    # aioredis expects xx/nx flag to be strings. Convert bool to strings
                    flag = None
                    if xx:
                        flag = client.SET_IF_EXIST
                    elif nx:
                        flag = client.SET_IF_NOT_EXIST
                    return bool(await c.set(nkey, nvalue, pexpire=timeout, exist=flag))
            except _main_exceptions as e:
                if (
                    not original_client
                    and not self._replica_read_only
                    and len(tried) < len(self._server)
                ):
                    # noinspection PyUnboundLocalVariable
                    tried.append(index)
                    client = None
                    continue
                raise ConnectionInterrupted(connection=client) from e

    async def incr_version(self, key, delta=1, version=None, client: Redis = None):
        """
        Adds delta to the cache version for the supplied key. Returns the
        new version.
        """

        if client is None:
            client = await self.get_client(write=True)

        if version is None:
            version = self._backend.version

        old_key = self.make_key(key, version)
        value = await self.get(old_key, version=version, client=client)

        with await client as c:
            try:
                ttl = await c.ttl(old_key)
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

        if value is None:
            raise ValueError("Key '%s' not found" % key)

        if isinstance(key, CacheKey):
            new_key = self.make_key(key.original_key(), version=version + delta)
        else:
            new_key = self.make_key(key, version=version + delta)

        await self.set(new_key, value, timeout=ttl, client=client)
        await self.delete(old_key, client=client)
        return version + delta

    async def add(
        self, key, value, timeout=DEFAULT_TIMEOUT, version=None, client: Redis = None
    ):
        """
        Add a value to the cache, failing if the key already exists.
        Returns ``True`` if the object was added, ``False`` if not.
        """
        return await self.set(
            key, value, timeout, version=version, client=client, nx=True
        )

    async def get(self, key, default=None, version=None, client: Redis = None):
        """
        Retrieve a value from the cache.
        Returns decoded value if key is found, the default if not.
        """
        if client is None:
            client = await self.get_client(write=False)

        key = self.make_key(key, version=version)

        with await client as c:
            try:
                value = await c.get(key)
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

        if value is None:
            return default
        return self.decode(value)

    async def persist(self, key, version=None, client: Redis = None):
        if client is None:
            client = await self.get_client(write=True)

        key = self.make_key(key, version=version)

        with await client as c:
            if await c.exists(key):
                await c.persist(key)

    async def expire(self, key, timeout, version=None, client: Redis = None):
        if client is None:
            client = await self.get_client(write=True)

        key = self.make_key(key, version=version)

        with await client as c:
            if await c.exists(key):
                await c.expire(key, timeout)

    async def delete(self, key, version=None, prefix=None, client: Redis = None):
        """
        Remove a key from the cache.
        """
        if client is None:
            client = await self.get_client(write=True)

        with await client as c:
            try:
                return await c.delete(
                    self.make_key(key, version=version, prefix=prefix)
                )
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    async def delete_pattern(
        self, pattern, version=None, prefix=None, client: Redis = None, itersize=None
    ):
        """
        Remove all keys matching pattern.
        """

        if client is None:
            client = await self.get_client(write=True)

        pattern = self.make_pattern(pattern, version=version, prefix=prefix)

        kwargs = {"match": pattern}
        if itersize:
            kwargs["count"] = itersize

        with await client as c:
            try:
                count = 0
                async for key in c.iscan(**kwargs):
                    await c.delete(key)
                    count += 1
                return count
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    async def delete_many(self, keys, version=None, client: Redis = None):
        """
        Remove multiple keys at once.
        """
        keys = [self.make_key(k, version=version) for k in keys]
        if not keys:
            return

        if client is None:
            client = await self.get_client(write=True)

        with await client as c:
            try:
                return await c.delete(*keys)
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    async def clear(self, client: Redis = None):
        """
        Flush all cache keys.
        """

        if client is None:
            client = await self.get_client(write=True)

        with await client as c:
            try:
                await c.flushdb()
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    def decode(self, value):
        """
        Decode the given value.
        """
        try:
            value = int(value)
        except (ValueError, TypeError):
            try:
                value = self._compressor.decompress(value)
            except CompressorError:
                # Handle little values, chosen to be not compressed
                pass
            value = self._serializer.loads(value)
        return value

    def encode(self, value):
        """
        Encode the given value.
        """

        if isinstance(value, bool) or not isinstance(value, int):
            value = self._serializer.dumps(value)
            value = self._compressor.compress(value)
            return value

        return value

    async def get_many(self, keys, version=None, client: Redis = None):
        """
        Retrieve many keys.
        """
        if not keys:
            return {}

        if client is None:
            client = await self.get_client(write=False)

        recovered_data = OrderedDict()
        map_keys = OrderedDict((self.make_key(k, version=version), k) for k in keys)

        with await client as c:
            try:
                results = await c.mget(*map_keys)
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

            for key, value in zip(map_keys, results):
                if value is None:
                    continue
                recovered_data[map_keys[key]] = self.decode(value)
            return recovered_data

    async def set_many(
        self, data, timeout=DEFAULT_TIMEOUT, version=None, client: Redis = None
    ):
        """
        Set a bunch of values in the cache at once from a dict of key/value
        pairs. This is much more efficient than calling set() multiple times.
        If timeout is given, that timeout will be used for the key; otherwise
        the default cache timeout will be used.
        """
        if timeout is DEFAULT_TIMEOUT:
            timeout = self._backend.default_timeout

        if client is None:
            client = await self.get_client(write=True)

        with await client as c:
            try:
                # Use pipeline since mset doesn't have timeout
                pipeline = c.pipeline()
                for key, value in data.items():
                    nkey = self.make_key(key, version=version)
                    nvalue = self.encode(value)
                    if timeout is not None:
                        timeout = int(timeout * 1000)
                        if timeout <= 0:
                            await self.delete(key, client=client, version=version)
                            continue
                    pipeline.set(nkey, nvalue, pexpire=timeout)

                await pipeline.execute()
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    async def _incr(
        self, key, delta=1, version=None, client: Redis = None, ignore_key_check=False
    ):
        if client is None:
            client = await self.get_client(write=True)

        key = self.make_key(key, version=version)

        with await client as c:
            try:
                try:
                    # if key expired after exists check, then we get
                    # key with wrong value and ttl -1.
                    # use lua script for atomicity
                    if not ignore_key_check:
                        lua = """
                        local exists = redis.call('EXISTS', KEYS[1])
                        if (exists == 1) then
                            return redis.call('INCRBY', KEYS[1], ARGV[1])
                        else return false end
                        """
                    else:
                        lua = """
                        return redis.call('INCRBY', KEYS[1], ARGV[1])
                        """
                    value = await c.eval(lua, [key], [delta])
                    if value is None:
                        raise ValueError("Key '%s' not found" % key)
                except ReplyError:
                    # if cached value or total value is greater than 64 bit signed
                    # integer.
                    # elif int is encoded. so redis sees the data as string.
                    # In this situation redis will throw ResponseError

                    # try to keep TTL of key

                    timeout = await c.ttl(key)
                    # returns -2 if the key does not exist
                    # means, that key have expired
                    if timeout == -2:
                        await client.quit()
                        raise ValueError("Key '%s' not found" % key)
                    value = await self.get(key, version=version, client=client) + delta
                    await self.set(
                        key, value, version=version, timeout=timeout, client=client
                    )
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

        return value

    def incr(
        self, key, delta=1, version=None, client: Redis = None, ignore_key_check=False
    ):
        """
        Add delta to value in the cache. If the key does not exist, raise a
        ValueError exception. if ignore_key_check=True then the key will be
        created and set to the delta value by default.
        """
        return self._incr(
            key=key,
            delta=delta,
            version=version,
            client=client,
            ignore_key_check=ignore_key_check,
        )

    def decr(self, key, delta=1, version=None, client: Redis = None):
        """
        Decrease delta to value in the cache. If the key does not exist, raise a
        ValueError exception.
        """
        return self._incr(key=key, delta=-delta, version=version, client=client)

    async def ttl(self, key, version=None, client: Redis = None):
        """
        Executes TTL redis command and return the "time-to-live" of specified key.
        If key is a non volatile key, it returns None.
        """
        if client is None:
            client = await self.get_client(write=False)

        key = self.make_key(key, version=version)
        with await client as c:
            if not await c.exists(key):
                return 0
            t = await c.ttl(key)

        if t >= 0:
            return t
        elif t == -1:
            return None
        elif t == -2:
            return 0
        else:
            # Should never reach here
            return None

    async def has_key(self, key, version=None, client: Redis = None):
        """
        Test if key exists.
        """

        if client is None:
            client = await self.get_client(write=False)
        with await client as c:
            key = self.make_key(key, version=version)
            try:
                return await c.exists(key) == 1
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    async def iter_keys(
        self, search, itersize=None, client: Redis = None, version=None
    ):
        """
        Same as keys, but uses redis >= 2.8 cursors
        for make memory efficient keys iteration.
        """

        if client is None:
            client = await self.get_client(write=False)

        pattern = self.make_pattern(search, version=version)

        with await client as c:
            async for item in c.iscan(match=pattern, count=itersize):
                yield self.reverse_key(item.decode())

    async def keys(self, search, version=None, client: Redis = None):
        """
        Execute KEYS command and return matched results.
        Warning: this can return huge number of results, in
        this case, it strongly recommended use iter_keys
        for it.
        """

        if client is None:
            client = await self.get_client(write=False)

        with await client as c:
            pattern = self.make_pattern(search, version=version)
            try:
                return [self.reverse_key(k.decode()) for k in await c.keys(pattern)]
            except _main_exceptions as e:
                raise ConnectionInterrupted(connection=client) from e

    def make_key(self, key, version=None, prefix=None) -> CacheKey:
        if isinstance(key, CacheKey):
            return key

        if prefix is None:
            prefix = self._backend.key_prefix

        if version is None:
            version = self._backend.version

        return CacheKey(self._backend.key_func(key, prefix, version))

    def make_pattern(self, pattern, version=None, prefix=None) -> CacheKey:
        if isinstance(pattern, CacheKey):
            return pattern

        if prefix is None:
            prefix = self._backend.key_prefix
        prefix = glob_escape(prefix)

        if version is None:
            version = self._backend.version
        version = glob_escape(str(version))

        return CacheKey(self._backend.key_func(pattern, prefix, version))

    async def close(self, **kwargs):
        if getattr(settings, "DJANGO_ASYNC_REDIS_CLOSE_CONNECTION", False):
            for i, c in enumerate(self._clients):
                c.close()
                await c.wait_closed()
                self._clients[i] = None

    async def touch(
        self, key, timeout=DEFAULT_TIMEOUT, version=None, client: Redis = None
    ):
        """
        Sets a new expiration for a key.
        """

        if timeout is DEFAULT_TIMEOUT:
            timeout = self._backend.default_timeout

        if client is None:
            client = await self.get_client(write=True)

        with await client as c:
            key = self.make_key(key, version=version)
            if timeout is None:
                return bool(await c.persist(key))
            else:
                # Convert to milliseconds
                timeout = int(timeout * 1000)
                return bool(await c.pexpire(key, timeout))
