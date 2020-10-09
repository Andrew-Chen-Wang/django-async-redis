from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, UpdateError
from django.core.cache import caches

from django_async_redis.sessions.backends.base import AsyncSessionBase


# Don't worry about size; all keys are hashed... I think :P
KEY_PREFIX = "django_async_redis.sessions.cache"


class SessionStore(AsyncSessionBase):
    """
    An async cache-based session store.
    """

    cache_key_prefix = KEY_PREFIX

    def __init__(self, session_key=None):
        self._cache = caches[settings.SESSION_CACHE_ALIAS]
        super().__init__(session_key)

    @property
    def key_salt(self):
        return "django_async_redis.cache"

    async def get_cache_key(self):
        return self.cache_key_prefix + await self._get_or_create_session_key()

    # Core methods

    async def load(self) -> dict:
        try:
            session_data = await self._cache.get_async(await self.get_cache_key())
        except Exception:
            session_data = None
        if session_data is not None:
            return session_data
        self._session_key = None
        return {}

    async def create(self):
        # Because a cache can fail silently, we don't know if
        # we are failing to create a new session because of a key collision or
        # because the cache is missing. So we try for a (large) number of times
        # and then raise an exception. That's the risk you shoulder if using
        # cache backing.
        for i in range(10000):
            self._session_key = await self._get_new_session_key()
            try:
                await self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return
        raise RuntimeError(
            "Unable to create a new session key. "
            "It is likely that the cache is unavailable."
        )

    async def save(self, must_create=False):
        if self.session_key is None:
            return await self.create()
        if must_create:
            func = self._cache.add_async
        elif await self._cache.get_async(await self.get_cache_key()) is not None:
            func = self._cache.set_async
        else:
            raise UpdateError
        result = await func(
            await self.get_cache_key(),
            await self._session(no_load=must_create),
            await self.get_expiry_age(),
        )
        if must_create and not result:
            raise CreateError

    async def exists(self, session_key):
        return bool(session_key) and await self._cache.has_key_async(
            self.cache_key_prefix + session_key
        )

    async def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        await self._cache.delete_async(self.cache_key_prefix + session_key)

    @classmethod
    async def clear_expired(cls):
        pass
