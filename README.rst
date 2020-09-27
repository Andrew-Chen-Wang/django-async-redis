==================
Django Async Redis
==================


.. image:: https://img.shields.io/pypi/v/django-async-redis.svg
        :target: https://pypi.python.org/pypi/django_async_redis

.. image:: https://img.shields.io/travis/Andrew-Chen-Wang/django-async-redis.svg
        :target: https://travis-ci.com/Andrew-Chen-Wang/django-async-redis

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

django-async-redis uses the redis-py native URL notation for connection strings, it
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

In some circumstances the password you should use to connect Redis
is not URL-safe, in this case you can escape it or just use the
convenience option in ``OPTIONS`` dict:

.. code-block:: python

    CACHES = {
        "default": {
            "BACKEND": "django_async_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/1",
            "OPTIONS": {
                "CLIENT_CLASS": "django_async_redis.client.DefaultClient",
                "PASSWORD": "mysecret"
            }
        }
    }

Take care, that this option does not overwrites the password in the uri, so if
you have set the password in the uri, this settings will be ignored.

Credit
------

- Hey, I'm Andrew. I'm busy in college, but I wanted to help contribute to Django's async ecosystem.
- Lots of code is taken from django-redis, including the tests.
  I just needed to port everything to asyncio and aioredis,
  but aioredis is not as full of capabilities as redis-py.
- I used cookiecutter-pypackage to generate this project.
- Thank you to Python Discord server's async topical chat for helping
  me understand when to use coroutines over sync functions.
