import functools
import logging
from collections import OrderedDict
from typing import Any, AsyncIterator, Callable, Optional, Union

from django.conf import settings
from django.core.cache.backends.base import BaseCache
from django.utils.module_loading import import_string

from .client import DefaultClient
from .exceptions import ConnectionInterrupted


DJANGO_ASYNC_REDIS_SCAN_ITERSIZE = getattr(
    settings, "DJANGO_ASYNC_REDIS_SCAN_ITERSIZE", 10
)

CONNECTION_INTERRUPTED = object()


def omit_exception(
    method: Optional[Callable] = None, return_value: Optional[Any] = None
):
    """
    Simple decorator that intercepts connection
    errors and ignores these if settings specify this.
    """

    if method is None:
        return functools.partial(omit_exception, return_value=return_value)

    @functools.wraps(method)
    def _decorator(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except ConnectionInterrupted as e:
            if self._ignore_exceptions:
                if self._log_ignored_exceptions:
                    self.logger.exception("Exception ignored")

                return return_value
            raise e.__cause__

    return _decorator


# noinspection PyAbstractClass
class RedisCache(BaseCache):
    def __init__(self, server, params):
        super().__init__(params)
        self._server = server
        self._params = params

        options = params.get("OPTIONS", {})
        self._client_cls = options.get(
            "CLIENT_CLASS", "django_async_redis.client.DefaultClient"
        )
        self._client_cls = import_string(self._client_cls)
        self._client = None

        self._ignore_exceptions = options.get(
            "IGNORE_EXCEPTIONS",
            getattr(settings, "DJANGO_ASYNC_REDIS_IGNORE_EXCEPTIONS", False),
        )
        self._log_ignored_exceptions = getattr(
            settings, "DJANGO_ASYNC_REDIS_LOG_IGNORED_EXCEPTIONS", False
        )
        self.logger = (
            logging.getLogger(getattr(settings, "DJANGO_ASYNC_REDIS_LOGGER", __name__))
            if self._log_ignored_exceptions
            else None
        )

    @property
    def client(self) -> Union[DefaultClient]:
        """
        Lazy client connection property.
        """
        if self._client is None:
            self._client = self._client_cls(self._server, self._params, self)
        return self._client

    @omit_exception
    async def aset(self, *args, **kwargs):
        return await self.client.set(*args, **kwargs)

    @omit_exception
    async def aincr_version(self, *args, **kwargs):
        return await self.client.incr_version(*args, **kwargs)

    @omit_exception
    async def aadd(self, *args, **kwargs):
        return await self.client.add(*args, **kwargs)

    async def aget(self, key, default=None, version=None, client=None):
        value = await self._aget(key, default, version, client)
        if value is CONNECTION_INTERRUPTED:
            value = default
        return value

    @omit_exception(return_value=CONNECTION_INTERRUPTED)
    async def _aget(self, key, default, version, client):
        return await self.client.get(
            key, default=default, version=version, client=client
        )

    @omit_exception
    async def adelete(self, *args, **kwargs):
        return await self.client.delete(*args, **kwargs)

    @omit_exception
    async def adelete_pattern(self, *args, **kwargs):
        kwargs["itersize"] = kwargs.get("itersize", DJANGO_ASYNC_REDIS_SCAN_ITERSIZE)
        return await self.client.delete_pattern(*args, **kwargs)

    @omit_exception
    async def adelete_many(self, *args, **kwargs):
        return await self.client.delete_many(*args, **kwargs)

    @omit_exception
    async def aclear(self) -> None:
        await self.client.clear()

    @omit_exception(return_value={})
    async def aget_many(self, *args, **kwargs) -> OrderedDict:
        return await self.client.get_many(*args, **kwargs)

    @omit_exception
    async def aset_many(self, *args, **kwargs):
        return await self.client.set_many(*args, **kwargs)

    @omit_exception
    async def aincr(self, *args, **kwargs):
        return await self.client.incr(*args, **kwargs)

    @omit_exception
    async def adecr(self, *args, **kwargs):
        return await self.client.decr(*args, **kwargs)

    @omit_exception
    async def ahas_key(self, *args, **kwargs):
        return await self.client.has_key(*args, **kwargs)

    @omit_exception
    async def akeys(self, *args, **kwargs):
        return await self.client.keys(*args, **kwargs)

    @omit_exception
    async def aiter_keys(self, *args, **kwargs) -> AsyncIterator[str]:
        """
        Returns a coroutine that the dev will
        await since an async generator is returned.
        """
        return self.client.iter_keys(*args, **kwargs)

    @omit_exception
    async def attl(self, *args, **kwargs) -> Optional[int]:
        return await self.client.ttl(*args, **kwargs)

    @omit_exception
    async def apersist(self, *args, **kwargs):
        return await self.client.persist(*args, **kwargs)

    @omit_exception
    async def aexpire(self, *args, **kwargs):
        return await self.client.expire(*args, **kwargs)

    @omit_exception
    async def aclose(self, **kwargs) -> None:
        await self.client.close(**kwargs)

    @omit_exception
    async def atouch(self, *args, **kwargs):
        return await self.client.touch(*args, **kwargs)
