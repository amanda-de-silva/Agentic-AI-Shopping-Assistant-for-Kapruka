"""Unit tests for the distributed/local MCP rate limiter."""
from __future__ import annotations

import pytest

from backend.core.exceptions import RateLimitExceeded
from backend.mcp.rate_limiter import RateLimiter


async def test_rate_limiter_allows_up_to_capacity(monkeypatch):
    monkeypatch.setenv("MCP_RATE_LIMIT_REDIS_URL", "")
    limiter = RateLimiter()
    limiter._request_limit = 2
    await limiter.acquire_request()
    await limiter.acquire_request()
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire_request()


async def test_order_bucket_is_independent(monkeypatch):
    monkeypatch.setenv("MCP_RATE_LIMIT_REDIS_URL", "")
    limiter = RateLimiter()
    limiter._order_limit = 1
    limiter._request_limit = 10
    await limiter.acquire_order()
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire_order()
    await limiter.acquire_request()