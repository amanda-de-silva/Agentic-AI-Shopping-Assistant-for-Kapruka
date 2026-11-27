"""Distributed MCP rate limiter.

When REDIS_URL is configured, limits are enforced in shared Redis state so multiple
backend replicas collectively respect the egress-IP upstream quota. Without Redis,
a process-local fixed-window fallback is used conservatively.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from backend.core.exceptions import RateLimitExceeded
from backend.core.logging import get_logger
from config.settings import get_params_config, get_settings

logger = get_logger(__name__)


_REDIS_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
local ttl = redis.call('PTTL', KEYS[1])
if current > tonumber(ARGV[1]) then
  return {0, ttl}
end
return {1, ttl}
"""


class RateLimiter:
    def __init__(self) -> None:
        cfg = get_params_config()["mcp"]
        self._request_limit = int(cfg["requests_per_minute"])
        self._order_limit = int(cfg["orders_per_hour"])
        self._lock = asyncio.Lock()
        self._local: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        self._redis = None
        settings = get_settings()
        if settings.mcp_rate_limit_redis_url:
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(settings.mcp_rate_limit_redis_url, decode_responses=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis rate limiter unavailable; using process-local fallback: %s", exc)

        self._prefix = settings.mcp_rate_limit_key_prefix

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def _acquire(self, scope: str, limit: int, window_seconds: int) -> None:
        if self._redis is not None:
            key = f"{self._prefix}:{scope}"
            try:
                allowed, ttl_ms = await self._redis.eval(
                    _REDIS_SCRIPT,
                    1,
                    key,
                    str(limit),
                    str(window_seconds * 1000),
                )
                if int(allowed) == 0:
                    raise RateLimitExceeded(scope, max(float(ttl_ms) / 1000.0, 1.0))
                return
            except RateLimitExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis limiter error, falling back locally: %s", exc)

        async with self._lock:
            now = time.monotonic()
            count, window_start = self._local[scope]
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            if count >= limit:
                retry_after = window_seconds - (now - window_start)
                self._local[scope] = (count, window_start)
                raise RateLimitExceeded(scope, max(retry_after, 1.0))
            self._local[scope] = (count + 1, window_start)

    async def acquire_request(self) -> None:
        await self._acquire("requests", self._request_limit, 60)

    async def acquire_order(self) -> None:
        await self._acquire("orders", self._order_limit, 3600)

_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter