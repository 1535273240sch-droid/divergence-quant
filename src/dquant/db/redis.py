"""Redis 客户端边界。"""

from __future__ import annotations

from redis.asyncio import Redis

from dquant.settings import RedisSettings


def create_redis_client(settings: RedisSettings) -> Redis:
    """创建 Redis 异步客户端；调用方决定何时 ping 或关闭。"""

    return Redis.from_url(settings.url, decode_responses=settings.decode_responses)


async def check_redis_connection(client: Redis) -> bool:
    """执行一次轻量 ping，连接失败由调用方处理。"""

    return bool(await client.ping())


async def close_redis_client(client: Redis) -> None:
    """关闭 Redis 客户端。"""

    await client.aclose()
