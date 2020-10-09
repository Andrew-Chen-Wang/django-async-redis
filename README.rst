==================
Django Async Redis
==================


.. image:: https://img.shields.io/pypi/v/django-async-redis.svg
        :target: https://pypi.python.org/pypi/django-async-redis

.. image:: https://img.shields.io/travis/Andrew-Chen-Wang/django-async-redis.svg?branch=master
        :target: https://travis-ci.com/Andrew-Chen-Wang/django-async-redis?branch=master

.. image:: https://readthedocs.org/projects/django-async-redis/badge/?version=latest
        :target: https://django-async-redis.readthedocs.io/en/latest/?badge=latest
        :alt: Documentation Status

Introduction
------------

django-async-redis is a full featured Redis cache and session backend for Django.

* Free software: Apache Software License 2.0
* Documentation: https://django-async-redis.readthedocs.io.

Requirements
------------

- `Python`_ 3.6+
- `Django`_ 3.0+
- `aioredis`_ 1.0+
- `Redis server`_ 2.8+

.. _Python: https://www.python.org/downloads/
.. _Django: https://www.djangoproject.com/download/
.. _aioredis: https://pypi.org/project/aioredis/
.. _Redis server: https://redis.io/download

User guide
----------

Installation
~~~~~~~~~~~~

Install with pip:

.. code-block:: console

    $ python -m pip install django-async-redis

Configure as cache backend
~~~~~~~~~~~~~~~~~~~~~~~~~~

To start using django-async-redis, you should change your Django cache settings to
something like:

.. code-block:: python

    CACHES = {
        "default": {
            "BACKEND": "django_async_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/1",
            "OPTIONS": {
                "CLIENT_CLASS": "django_async_redis.client.DefaultClient",
            }
        }
    }

django-async-redis uses the aioredis native URL notation for connection strings, it
allows better interoperability and has a connection string in more "standard"
way. Some examples:

- ``redis://[:password]@localhost:6379/0``
- ``rediss://[:password]@localhost:6379/0``
- ``unix://[:password]@/path/to/socket.sock?db=0``

Three URL schemes are supported:

- ``redis://``: creates a normal TCP socket connection
- ``rediss://``: creates a SSL wrapped TCP socket connection
- ``unix://`` creates a Unix Domain Socket connection

There are several ways to specify a database number:

- A ``db`` querystring option, e.g. ``redis://localhost?db=0``
- If using the ``redis://`` scheme, the path argument of the URL, e.g.
  ``redis://localhost/0``

Advanced usage
--------------

Pickle version
~~~~~~~~~~~~~~

For almost all values, django-async-redis uses pickle to serialize objects.

The latest available version of pickle is used by default. If you want set a
concrete version, you can do it, using ``PICKLE_VERSION`` option:

.. code-block:: python

    CACHES = {
        "default": {
            # ...
            "OPTIONS": {
                "PICKLE_VERSION": -1  # Use the latest protocol version
            }
        }
    }

Memcached exceptions behavior
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In some situations, when Redis is only used for cache, you do not want
exceptions when Redis is down. This is default behavior in the memcached
backend and it can be emulated in django-async-redis.

For setup memcached like behaviour (ignore connection exceptions), you should
set ``IGNORE_EXCEPTIONS`` settings on your cache configuration:

.. code-block:: python

    CACHES = {
        "default": {
            # ...
            "OPTIONS": {
                "IGNORE_EXCEPTIONS": True,
            }
        }
    }

Also, you can apply the same settings to all configured caches, you can set the global flag in
your settings:

.. code-block:: python

    DJANGO_ASYNC_REDIS_IGNORE_EXCEPTIONS = True

Log Ignored Exceptions
~~~~~~~~~~~~~~~~~~~~~~

When ignoring exceptions with ``IGNORE_EXCEPTIONS`` or
``DJANGO_ASYNC_REDIS_IGNORE_EXCEPTIONS``, you may optionally log exceptions using the
global variable ``DJANGO_ASYNC_REDIS_LOG_IGNORED_EXCEPTIONS`` in your settings file::

    DJANGO_ASYNC_REDIS_LOG_IGNORED_EXCEPTIONS = True

If you wish to specify the logger in which the exceptions are output, simply
set the global variable ``DJANGO_ASYNC_REDIS_LOGGER`` to the string name and/or path
of the desired logger. This will default to ``__name__`` if no logger is
specified and ``DJANGO_ASYNC_REDIS_LOG_IGNORED_EXCEPTIONS`` is ``True``::

    DJANGO_ASYNC_REDIS_LOGGER = 'some.specified.logger'

Infinite timeout
~~~~~~~~~~~~~~~~

django-async-redis comes with infinite timeouts support out of the box.
And it behaves in the same way as the Django BaseCache backend specifies:

- ``timeout=0`` expires the value immediately.
- ``timeout=None`` infinite timeout

.. code-block:: python

    await cache.set_async("key", "value", timeout=None)

Get ttl (time-to-live) from key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With Redis, you can access to ttl of any stored key, for it,
django-async-redis exposes ``ttl_async`` function.

It returns:

- 0 if key does not exists (or already expired).
- None for keys that exists but does not have any expiration.
- ttl value for any volatile key (any key that has expiration).

.. code-block:: pycon

    >>> from django.core.cache import cache
    >>> await cache.set_async("foo", "value", timeout=25)
    >>> await cache.ttl_async("foo")
    25
    >>> await cache.ttl_async("not-existent")
    0

Expire & Persist
~~~~~~~~~~~~~~~~

Additionally to the simple ttl query, you can send persist a concrete key or
specify a new expiration timeout using the ``persist_async`` and ``expire_async``
methods:

.. code-block:: pycon

    >>> await cache.set_async("foo", "bar", timeout=22)
    >>> await cache.ttl_async("foo")
    22
    >>> await cache.persist_async("foo")
    >>> await cache.ttl_async("foo")
    None

.. code-block:: pycon

    >>> await cache.set_async("foo", "bar", timeout=22)
    >>> await cache.expire_async("foo", timeout=5)
    >>> await cache.ttl_async("foo")
    5

Scan & Delete keys in bulk
~~~~~~~~~~~~~~~~~~~~~~~~~~

django-async-redis comes with some additional methods that help with searching or
deleting keys using glob patterns.

.. code-block:: pycon

    >>> from django.core.cache import cache
    >>> await cache.keys_async("foo_*")
    ["foo_1", "foo_2"]

A simple search like this will return all matched values. In databases with a
large number of keys this isn't suitable method. Instead, you can use the
``iter_keys_async`` function that works like the ``keys_async`` function but uses Redis
server side cursors. Calling ``iter_keys_async`` will return a generator that you can
then iterate over efficiently.

.. code-block:: pycon

    >>> from django.core.cache import cache
    >>> await cache.iter_keys_async("foo_*")
    <async_generator object algo at 0x7ffa9c2713a8>
    >>> (await cache.iter_keys_async("foo_*")).__anext__()
    "foo_1"

For deleting keys, you should use ``delete_pattern_async`` which has the same glob
pattern syntax as the ``keys_async`` function and returns the number of deleted keys.

.. code-block:: pycon

    >>> from django.core.cache import cache
    >>> await cache.delete_pattern_async("foo_*")

Redis native commands
~~~~~~~~~~~~~~~~~~~~~

django-async-redis has limited support for some Redis atomic operations, such as the
commands ``SETNX`` and ``INCR``.

You can use the ``SETNX`` command through the backend ``set_async()`` method with
the ``nx`` parameter:

.. code-block:: pycon

    >>> from django.core.cache import cache
    >>> await cache.set_async("key", "value1", nx=True)
    True
    >>> await cache.set_async("key", "value2", nx=True)
    False
    >>> await cache.get_async("key")
    "value1"

Also, the ``incr_async`` and ``decr_async`` methods use Redis atomic
operations when the value that a key contains is suitable for it.

Note that setting ``xx`` to True overrides the ``nx`` flag according
to aioredis.

Connection pools
~~~~~~~~~~~~~~~~

Behind the scenes, django-async-redis uses the underlying aioredis connection pool
implementation and exposes a simple way to configure it. Alternatively, you
can directly customize a connection/connection pool creation for a backend.

The default aioredis behavior is to not close connections, recycling them when
possible.

Notes
-----

Since the majority of this code was ported from django-redis, there was one
case that had needed a monkeypatch. In ``django_async_redis.util``, we implement
``CacheKey`` which subclasses ``str`` which helps us know if a cache key was
already created. Since aioredis, checks if the cache key is of type str
(and others), I had to monkeypatch that check so that a CacheKey instance could
also be accepted.

Credit
~~~~~~

- Hey, I'm Andrew. I'm busy in college, but I wanted to help contribute
  to Django's async ecosystem.
- Lots of code and docs is taken from django-async-redis, including the tests.
  I just needed to port everything to asyncio and aioredis.
- I used cookiecutter-pypackage to generate this project.
- Thank you to Python Discord server's async topical chat
  for helping me understand when to use coroutines over sync functions
  and @Bast and @hmmmm in general because they're OG.
