"""
Wraps order_agent + order_status_cache with TTL-based refresh.
"""
from __future__ import annotations

from backend.agents import order_agent
from backend.schemas.order import OrderStatus


async def get_status(order_number: str, *, force_refresh: bool = False) -> OrderStatus:
    return await order_agent.track_order(order_number, force_refresh=force_refresh)
