"""
Wraps kapruka_list_delivery_cities / kapruka_check_delivery.
"""
from __future__ import annotations

from typing import Any

import httpx
from urllib.parse import urlparse

from backend.core.cache import cache
from backend.core.logging import get_logger
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import (
    extract_markdown_result,
    parse_check_delivery_markdown,
    parse_delivery_cities_markdown,
)
from backend.mcp.tool_schemas import CheckDeliveryInput, ListDeliveryCitiesInput
from config.settings import get_params_config, get_settings


logger = get_logger(__name__)


async def search_delivery_information(*, user_question: str) -> dict[str, Any]:
    """Search current public information about Kapruka delivery policy/coverage.

    Use this for general questions (one-day/same-day areas, delivery windows,
    delivery charges, delivery policies). Specific city/date feasibility remains
    handled by ``check_delivery`` and Kapruka MCP.
    """
    settings = get_settings()
    api_key = (settings.tavily_api_key or "").strip()
    if not api_key:
        logger.warning("Tavily delivery search requested but TAVILY_API_KEY is not configured")
        return {
            "status": "unavailable",
            "query": user_question,
            "answer": None,
            "sources": [],
            "message": "Current delivery-policy search is not configured.",
        }

    query = (
        "Kapruka official delivery policy and service coverage: "
        + " ".join(str(user_question or "").split())
    )
    payload = {
        "query": query,
        "topic": "general",
        "search_depth": "advanced",
        "max_results": 6,
        "include_answer": True,
        "include_raw_content": False,
        # Prefer Kapruka-owned policy pages for service-area claims.
        "include_domains": ["kapruka.com"],
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Tavily delivery information search failed: %s", exc)
        return {
            "status": "unavailable",
            "query": user_question,
            "answer": None,
            "sources": [],
            "message": "Could not retrieve current delivery information.",
        }

    sources: list[dict[str, str]] = []
    for item in (data.get("results") or [])[:6]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "Kapruka delivery information").strip()
        snippet = " ".join(str(item.get("content") or "").split())[:900]
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or "").lower()
        is_kapruka_domain = (
            hostname == "kapruka.com"
            or hostname.endswith(".kapruka.com")
        )
        if parsed_url.scheme == "https" and is_kapruka_domain:
            sources.append({"title": title, "url": url, "snippet": snippet})

    answer = data.get("answer")
    if not sources and not answer:
        return {
            "status": "no_results",
            "query": user_question,
            "answer": None,
            "sources": [],
            "message": "No reliable Kapruka delivery-policy pages were found.",
        }

    return {
        "status": "ok",
        "query": user_question,
        "answer": str(answer).strip() if answer else None,
        "sources": sources,
    }


async def resolve_city(query: str) -> list[dict[str, Any]]:
    cache_key = f"delivery_cities:{query.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    client = get_mcp_client()
    result = await client.list_delivery_cities(ListDeliveryCitiesInput(query=query, limit=10))
    data = result.data or {}
    # Kapruka's list_delivery_cities only ever returns markdown (confirmed live) --
    # structured_content is populated but shaped as {"result": "<markdown>"}, not a
    # real city array. Parse it; keep the old dict-based read as a defensive fallback
    # in case Kapruka ever ships real structured JSON for this tool.
    markdown = extract_markdown_result(data)
    if markdown is not None:
        matches = parse_delivery_cities_markdown(markdown)["cities"]
    else:
        matches = data.get("cities") or data.get("matches") or []

    ttl_minutes = get_params_config()["cache"]["delivery_cities_ttl_minutes"]
    cache.set(cache_key, matches, ttl_seconds=ttl_minutes * 60)
    return matches


async def check_delivery(*, city: str, delivery_date: str, product_id: str | None = None) -> dict[str, Any]:
    resolved = await resolve_city(city)
    canonical_city = resolved[0]["name"] if resolved else city

    client = get_mcp_client()
    result = await client.check_delivery(
        CheckDeliveryInput(city=canonical_city, delivery_date=delivery_date, product_id=product_id)
    )
    data = result.data or {}
    # Kapruka's check_delivery only ever returns markdown (confirmed live) --
    # structured_content is populated but shaped as {"result": "<markdown>"}, not real
    # structured JSON. Parse it; keep the old dict-based read as a defensive fallback
    # in case Kapruka ever ships real structured JSON for this tool. The perishable-
    # warning text itself is still unconfirmed (requires a cake/flower/combo
    # product_id, which hasn't been tested against the live server yet) -- see
    # parse_check_delivery_markdown()'s docstring.
    markdown = extract_markdown_result(data)
    parsed = parse_check_delivery_markdown(markdown) if markdown is not None else data
    return {
        "requested_city": city,
        "resolved_city": canonical_city,
        "deliverable": parsed.get("deliverable"),
        "rate_lkr": parsed.get("rate_lkr") or parsed.get("rate"),
        "perishable_warning": parsed.get("perishable_warning"),
        "raw": data,
    }
