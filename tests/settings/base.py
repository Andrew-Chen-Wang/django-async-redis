SECRET_KEY = "django_tests_secret_key"

CACHES = {
    "default": {
        "BACKEND": "django_async_redis.cache.RedisCache",
        "LOCATION": ["redis://127.0.0.1:6379?db=1", "redis://127.0.0.1:6379?db=1"],
        "OPTIONS": {"CLIENT_CLASS": "django_async_redis.client.DefaultClient"},
    },
    "doesnotexist": {
        "BACKEND": "django_async_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:56379?db=1",
        "OPTIONS": {"CLIENT_CLASS": "django_async_redis.client.DefaultClient"},
    },
    "sample": {
        "BACKEND": "django_async_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379:1,redis://127.0.0.1:6379:1",
        "OPTIONS": {"CLIENT_CLASS": "django_async_redis.client.DefaultClient"},
    },
    "with_prefix": {
        "BACKEND": "django_async_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379?db=1",
        "OPTIONS": {"CLIENT_CLASS": "django_async_redis.client.DefaultClient"},
        "KEY_PREFIX": "test-prefix",
    },
}

INSTALLED_APPS = ["django.contrib.sessions"]
