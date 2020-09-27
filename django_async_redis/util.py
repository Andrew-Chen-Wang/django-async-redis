# noinspection PyProtectedMember
from aioredis.util import _converters


class CacheKey(str):
    """
    A stub string class that we can use to check if a key was created already.
    """

    def original_key(self):
        return self.rsplit(":", 1)[1]


# Monkeypatch so that CacheKey works with aioredis encoding
_converters[CacheKey] = lambda x: x.encode()


def default_reverse_key(key):
    return key.split(":", 2)[2]
