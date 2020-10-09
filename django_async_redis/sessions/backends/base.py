import base64
import logging
import warnings
from datetime import datetime, timedelta

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.sessions.backends.base import VALID_KEY_CHARS
from django.contrib.sessions.exceptions import SuspiciousSession
from django.core import signing
from django.core.exceptions import SuspiciousOperation
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string, salted_hmac
from django.utils.deprecation import RemovedInDjango40Warning
from django.utils.module_loading import import_string
from django.utils.translation import LANGUAGE_SESSION_KEY


class AsyncSessionBase:
    """
    Base class for all async Session classes.
    """

    TEST_COOKIE_NAME = "testcookie"
    TEST_COOKIE_VALUE = "worked"

    __not_given = object()

    def __init__(self, session_key=None) -> None:
        self._session_key = session_key
        self.accessed = False
        self.modified = False
        self.serializer = import_string(settings.SESSION_SERIALIZER)

    async def __getitem(self, key):
        if key == LANGUAGE_SESSION_KEY:
            warnings.warn(
                "The user language will no longer be stored in "
                "request.session in Django 4.0. Read it from "
                "request.COOKIES[settings.LANGUAGE_COOKIE_NAME] instead.",
                RemovedInDjango40Warning,
                stacklevel=2,
            )
        return (await self._session())[key]

    async def __setitem(self, key, value):
        (await self._session())[key] = value
        self.modified = True

    async def __delitem(self, key):
        del (await self._session())[key]
        self.modified = True

    # For Django standard apps compatibility
    def __contains__(self, key):
        return async_to_sync(self.has_key)(key)

    def __getitem__(self, key):
        return async_to_sync(self.__getitem)(key)

    def __setitem__(self, key, value):
        async_to_sync(self.__setitem)(key, value)

    def __delitem__(self, key):
        async_to_sync(self.__delitem)(key)

    @property
    def key_salt(self):
        # I'm keeping it like this since I'd like for this portion
        # of the package to be ported into Django.
        return "django.contrib.sessions." + self.__class__.__qualname__

    async def get(self, key, default=None):
        return (await self._session()).get(key, default)

    async def pop(self, key, default=__not_given):
        self.modified = self.modified or key in (await self._session())
        args = () if default is self.__not_given else (default,)
        return (await self._session()).pop(key, *args)

    async def setdefault(self, key, value):
        if key in (await self._session()):
            return (await self._session())[key]
        else:
            self.modified = True
            (await self._session())[key] = value
            return value

    async def set_test_cookie(self) -> None:
        await self.__setitem(self.TEST_COOKIE_NAME, self.TEST_COOKIE_VALUE)

    async def test_cookie_worked(self):
        return await self.get(self.TEST_COOKIE_NAME) == self.TEST_COOKIE_VALUE

    async def delete_test_cookie(self) -> None:
        await self.__delitem(self.TEST_COOKIE_NAME)

    # Encoding/Decoding

    def _hash(self, value):
        # RemovedInDjango40Warning: pre-Django 3.1 format will be invalid.
        key_salt = "django.contrib.sessions" + self.__class__.__name__
        return salted_hmac(key_salt, value).hexdigest()

    def encode(self, session_dict):
        "Return the given session dictionary serialized and encoded as a string."
        # RemovedInDjango40Warning: DEFAULT_HASHING_ALGORITHM will be removed.
        if settings.DEFAULT_HASHING_ALGORITHM == "sha1":
            return self._legacy_encode(session_dict)
        return signing.dumps(
            session_dict, salt=self.key_salt, serializer=self.serializer, compress=True,
        )

    def decode(self, session_data):
        try:
            return signing.loads(
                session_data, salt=self.key_salt, serializer=self.serializer
            )
        # RemovedInDjango40Warning: when the deprecation ends, handle here
        # exceptions similar to what _legacy_decode() does now.
        except signing.BadSignature:
            try:
                # Return an empty session if data is not in the pre-Django 3.1
                # format.
                return self._legacy_decode(session_data)
            except Exception:
                logger = logging.getLogger("django.security.SuspiciousSession")
                logger.warning("Session data corrupted")
                return {}
        except Exception:
            return self._legacy_decode(session_data)

    def _legacy_encode(self, session_dict):
        # RemovedInDjango40Warning.
        serialized = self.serializer().dumps(session_dict)
        hash = self._hash(serialized)
        return base64.b64encode(hash.encode() + b":" + serialized).decode("ascii")

    def _legacy_decode(self, session_data):
        # RemovedInDjango40Warning: pre-Django 3.1 format will be invalid.
        encoded_data = base64.b64decode(session_data.encode("ascii"))
        try:
            # could produce ValueError if there is no ':'
            hash, serialized = encoded_data.split(b":", 1)
            expected_hash = self._hash(serialized)
            if not constant_time_compare(hash.decode(), expected_hash):
                raise SuspiciousSession("Session data corrupted")
            else:
                return self.serializer().loads(serialized)
        except Exception as e:
            # ValueError, SuspiciousOperation, unpickling exceptions. If any of
            # these happen, just return an empty dictionary (an empty session).
            if isinstance(e, SuspiciousOperation):
                logger = logging.getLogger("django.security.%s" % e.__class__.__name__)
                logger.warning(str(e))
            return {}

    # Methods changing _session

    async def update(self, dict_) -> None:
        (await self._session()).update(dict_)
        self.modified = True

    async def has_key(self, key) -> bool:
        return (await self._session()).get(key) is not None

    async def keys(self):
        return (await self._session()).keys()

    async def values(self):
        return (await self._session()).values()

    async def items(self):
        return (await self._session()).items()

    def clear(self):
        # To avoid unnecessary persistent storage accesses, we set up the
        # internals directly (loading data wastes time, since we are going to
        # set it to an empty dict anyway).
        self._session_cache = {}
        self.accessed = True
        self.modified = True

    def is_empty(self) -> bool:
        "Return True when there is no session_key and the session is empty."
        try:
            return not self._session_key and not self._session_cache
        except AttributeError:
            return True

    # Setting the session + session key methods

    async def _get_new_session_key(self):
        "Return session key that isn't being used."
        while True:
            session_key = get_random_string(32, VALID_KEY_CHARS)
            if not await self.exists(session_key):
                return session_key

    async def _get_or_create_session_key(self):
        if self._session_key is None:
            self._session_key = await self._get_new_session_key()
        return self._session_key

    def _validate_session_key(self, key):
        """
        Key must be truthy and at least 8 characters long. 8 characters is an
        arbitrary lower bound for some minimal key security.
        """
        return key and len(key) >= 8

    def _get_session_key(self):
        return self.__session_key

    def _set_session_key(self, value):
        """
        Validate session key on assignment. Invalid values will set to None.
        """
        if self._validate_session_key(value):
            self.__session_key = value
        else:
            self.__session_key = None

    session_key = property(_get_session_key)
    _session_key = property(_get_session_key, _set_session_key)

    async def _session(self, no_load=False) -> dict:
        """
        Lazily load session from storage (unless "no_load" is True, when only
        an empty dict is stored) and store it in the current instance.
        """
        self.accessed = True
        try:
            return self._session_cache
        except AttributeError:
            if self.session_key is None or no_load:
                self._session_cache = {}
            else:
                self._session_cache = await self.load()
        return self._session_cache

    # Expiry

    def get_session_cookie_age(self):
        return settings.SESSION_COOKIE_AGE

    async def get_expiry_age(self, **kwargs):
        """Get the number of seconds until the session expires.

        Optionally, this function accepts `modification` and `expiry` keyword
        arguments specifying the modification and expiry of the session.
        """
        try:
            modification = kwargs["modification"]
        except KeyError:
            modification = timezone.now()
        # Make the difference between "expiry=None passed in kwargs" and
        # "expiry not passed in kwargs", in order to guarantee not to trigger
        # self.load() when expiry is provided.
        try:
            expiry = kwargs["expiry"]
        except KeyError:
            expiry = await self.get("_session_expiry")

        if not expiry:  # Checks both None and 0 cases
            return self.get_session_cookie_age()
        if not isinstance(expiry, datetime):
            return expiry
        delta = expiry - modification
        return delta.days * 86400 + delta.seconds

    async def get_expiry_date(self, **kwargs):
        """Get session the expiry date (as a datetime object).

        Optionally, this function accepts `modification` and `expiry` keyword
        arguments specifying the modification and expiry of the session.
        """
        try:
            modification = kwargs["modification"]
        except KeyError:
            modification = timezone.now()
        # Same comment as in get_expiry_age
        try:
            expiry = kwargs["expiry"]
        except KeyError:
            expiry = await self.get("_session_expiry")

        if isinstance(expiry, datetime):
            return expiry
        expiry = expiry or self.get_session_cookie_age()
        return modification + timedelta(seconds=expiry)

    async def set_expiry(self, value):
        """
        Set a custom expiration for the session. ``value`` can be an integer,
        a Python ``datetime`` or ``timedelta`` object or ``None``.

        If ``value`` is an integer, the session will expire after that many
        seconds of inactivity. If set to ``0`` then the session will expire on
        browser close.

        If ``value`` is a ``datetime`` or ``timedelta`` object, the session
        will expire at that specific future time.

        If ``value`` is ``None``, the session uses the global session expiry
        policy.
        """
        if value is None:
            # Remove any custom expiration for this session.
            try:
                await self.__delitem("_session_expiry")
            except KeyError:
                pass
            return
        if isinstance(value, timedelta):
            value = timezone.now() + value
        await self.__setitem("_session_expiry", value)

    async def get_expire_at_browser_close(self):
        """
        Return ``True`` if the session is set to expire when the browser
        closes, and ``False`` if there's an expiry date. Use
        ``get_expiry_date()`` or ``get_expiry_age()`` to find the actual expiry
        date/age, if there is one.
        """
        if await self.get("_session_expiry") is None:
            return settings.SESSION_EXPIRE_AT_BROWSER_CLOSE
        return await self.get("_session_expiry") == 0

    async def flush(self):
        """
        Remove the current session data from the database and regenerate the
        key.
        """
        self.clear()
        await self.delete()
        self._session_key = None

    async def cycle_key(self):
        """
        Create a new session key, while retaining the current session data.
        """
        data = await self._session()
        key = self.session_key
        await self.create()
        self._session_cache = data
        if key:
            await self.delete(key)

    # Methods that child classes must implement.

    async def exists(self, session_key):
        """
        Return True if the given session_key already exists.
        """
        raise NotImplementedError(
            "subclasses of SessionBase must provide an exists() method"
        )

    async def create(self):
        """
        Create a new session instance. Guaranteed to create a new object with
        a unique key and will have saved the result once (with empty data)
        before the method returns.
        """
        raise NotImplementedError(
            "subclasses of SessionBase must provide a create() method"
        )

    async def save(self, must_create=False):
        """
        Save the session data. If 'must_create' is True, create a new session
        object (or raise CreateError). Otherwise, only update an existing
        object and don't create one (raise UpdateError if needed).
        """
        raise NotImplementedError(
            "subclasses of SessionBase must provide a save() method"
        )

    async def delete(self, session_key=None):
        """
        Delete the session data under this key. If the key is None, use the
        current session key value.
        """
        raise NotImplementedError(
            "subclasses of SessionBase must provide a delete() method"
        )

    async def load(self):
        """
        Load the session data and return a dictionary.
        """
        raise NotImplementedError(
            "subclasses of SessionBase must provide a load() method"
        )

    @classmethod
    async def clear_expired(cls):
        """
        Remove expired sessions from the session store.

        If this operation isn't possible on a given backend, it should raise
        NotImplementedError. If it isn't necessary, because the backend has
        a built-in expiration mechanism, it should be a no-op.
        """
        raise NotImplementedError("This backend does not support clear_expired().")
