"""Atomic, shared quotas. A Redis failure must not enable unbounded spending."""

import hashlib
import hmac
import time

from redis.asyncio import Redis

from app.core.config import settings

_INCREMENT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used + tonumber(ARGV[1]) > tonumber(ARGV[2]) then return 0 end
redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


class QuotaExceeded(ValueError):
    pass


async def consume(scope: str, subject: str, limit: int, period: int, amount: int = 1):
    if not settings.rate_limit_enabled and settings.app_env != "production":
        return
    digest = hmac.new(
        settings.app_secret_key.encode(), subject.encode(), hashlib.sha256
    ).hexdigest()
    bucket = int(time.time()) // period
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        allowed = await client.eval(
            _INCREMENT, 1, f"quota:{scope}:{digest}:{bucket}", amount, limit, period + 1
        )
        if not allowed:
            raise QuotaExceeded("Limite temporário atingido. Tente mais tarde.")
    finally:
        await client.aclose()
