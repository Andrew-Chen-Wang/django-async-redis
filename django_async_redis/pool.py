from typing import Any, Dict

from aioredis import Redis
from aioredis.parser import Reader
from aioredis.pool import ConnectionsPool
from django.conf import settings
from django.utils.module_loading import import_string


class ConnectionFactory:

    # Store connection pool by cache backend options.
    #
    # _pools is a process-global, as otherwise _pools is cleared every time
    # ConnectionFactory is instantiated, as Django creates new cache client
    # (DefaultClient) instance for every request.

    _pools = {}

    def __init__(self, options):
        # Pool
        pool_cls_path = options.get(
            "CONNECTION_POOL_CLS", "aioredis.pool.ConnectionsPool"
        )
        # Must subclass AbcPool
        self.pool_cls: ConnectionsPool = import_string(pool_cls_path)

        pool_func_path = options.get("CONNECTION_POOL_FUNC", "aioredis.create_pool")
        self.pool_func = import_string(pool_func_path)
        self.pool_func_kwargs: Dict[str, Any] = options.get(
            "CONNECTION_POOL_KWARGS", {}
        )

        # Client
        redis_client_func_path = options.get(
            "REDIS_CLIENT_FUNC", "aioredis.create_redis"
        )
        self.redis_client_func = import_string(redis_client_func_path)
        self.redis_client_func_kwargs = options.get("REDIS_CLIENT_KWARGS", {})
        # Must subclass AbcConnection
        redis_client_cls_path = options.get(
            "REDIS_CLIENT_CLASS", "aioredis.Redis"
        )
        self.redis_client_cls = import_string(redis_client_cls_path)

        self.options = options

    def make_connection_params(self, address) -> Dict[str, Any]:
        """
        Given a main connection parameters, build a complete
        dict of connection parameters.
        """

        kwargs = {
            "address": address,
            "parser": self.get_parser_cls(),
        }

        password = self.options.get("PASSWORD", None)
        if password:
            kwargs["password"] = password

        create_connection_timeout = self.options.get("CREATE_CONNECTION_TIMEOUT", None)
        if create_connection_timeout:
            assert isinstance(
                create_connection_timeout, (int, float)
            ), "Create connection timeout should be float or integer"
            assert (
                create_connection_timeout > 0
            ), "Create connection timeout should be greater than 0"
            kwargs["create_connection_timeout"] = create_connection_timeout

        return kwargs

    async def connect(self, address: str) -> Redis:
        """
        Given a basic connection parameters,
        return a new connection.
        """
        params: Dict[str, Any] = self.make_connection_params(address)
        connection = await self.get_connection(params)
        return connection

    async def get_connection(self, params: Dict[str, Any]) -> Redis:
        """
        Given pre-formatted params, return a
        new connection.

        The default implementation uses a cached pools
        for create new connection.
        """
        pool: ConnectionsPool = await self.get_or_create_connection_pool(params)
        # Creates new connection if needed. If pool is closed, womp womp
        # If no free connection, waits for another conn.
        return self.redis_client_cls(pool)

    def get_parser_cls(self):
        cls = self.options.get("PARSER_CLASS", None)
        if cls is None:
            return Reader
        return import_string(cls)

    async def get_or_create_connection_pool(
        self, params: Dict[str, Any]
    ) -> ConnectionsPool:
        """
        Given connection parameters and return a new
        or cached connection pool for them.

        Reimplement this method if you want distinct
        connection pool instance caching behavior.
        """
        key = params["address"]
        if key not in self._pools:
            self._pools[key] = await self.get_connection_pool(params)
        return self._pools[key]

    async def get_connection_pool(self, params: Dict[str, Any]) -> ConnectionsPool:
        """
        Given connection parameters, return a new
        connection pool for them.

        Overwrite this method if you want a custom
        behavior on creating connection pool.
        """
        cp_params = dict(params)
        cp_params.update(self.pool_func_kwargs)
        pool: ConnectionsPool = await self.pool_func(**cp_params)
        return pool


def get_connection_factory(path=None, options=None):
    if path is None:
        path = getattr(
            settings,
            "DJANGO_ASYNC_REDIS_CONNECTION_FACTORY",
            "django_async_redis.pool.ConnectionFactory",
        )

    cls = import_string(path)
    return cls(options or {})
