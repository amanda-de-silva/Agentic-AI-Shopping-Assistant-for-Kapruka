"""
Thin layer over catalog_agent with response caching (categories, hot searches).
"""
from __future__ import annotations

from backend.agents import catalog_agent
from backend.core.cache import cache
from config.settings import get_params_config


async def get_categories(depth: int = 1) -> list[dict]:
    cache_key = f"categories:{depth}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    categories = await catalog_agent.list_categories(depth=depth)
    ttl_minutes = get_params_config()["cache"]["categories_ttl_minutes"]
    cache.set(cache_key, categories, ttl_seconds=ttl_minutes * 60)
    return categories


async def search(**kwargs):
    return await catalog_agent.search_products(**kwargs)


async def get_product(product_id: str):
    return await catalog_agent.get_product(product_id)
