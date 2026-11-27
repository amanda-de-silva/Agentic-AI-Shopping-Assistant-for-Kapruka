"""Search one requested shopping-list item at a time.

The catalog search result is authoritative for product selection, name and price.
Kapruka search summaries do not always include product images, so this service
lazily enriches only image-missing results using the existing catalog detail
agent. Enrichment is bounded and failures never remove an otherwise valid
search result.

This service remains isolated from the normal chat recommendation pipeline
and Gift Composer.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.agents import catalog_agent
from backend.core.logging import get_logger
from backend.schemas.shopping_list import (
    ShoppingListItemSearchResponse,
    ShoppingListProduct,
)

logger = get_logger(__name__)
MAX_PRODUCTS_PER_ITEM = 12
IMAGE_ENRICHMENT_CONCURRENCY = 3
IMAGE_CACHE_TTL_SECONDS = 30 * 60
IMAGE_CACHE_MAX_ENTRIES = 500

# Small process-local cache: browsing the same product from another list entry
# should not cause another MCP detail call every time. Only successful detail
# responses are cached; a temporary MCP failure can be retried on the next view.
_IMAGE_DETAIL_CACHE: dict[
    str,
    tuple[float, str | None, list[str], str | None],
] = {}


def _valid_product_id(value: object) -> str | None:
    if value is None:
        return None
    product_id = str(value).strip()
    if not product_id or product_id.lower() in {"none", "null", "undefined"}:
        return None
    return product_id


def _clean_image(value: object) -> str | None:
    """Return a non-empty image URL without changing its actual URL."""
    if not isinstance(value, str):
        return None
    image = value.strip()
    if not image or image.lower() in {"none", "null", "undefined"}:
        return None
    return image


def _candidate_images(candidate: Any) -> list[str]:
    raw_images = getattr(candidate, "images", None)
    images = [
        image
        for value in (raw_images if isinstance(raw_images, (list, tuple)) else [])
        if (image := _clean_image(value))
    ]
    first = _clean_image(getattr(candidate, "image_url", None))
    if first and first not in images:
        images.insert(0, first)
    return images


def _cached_image_detail(
    product_id: str,
) -> tuple[str | None, list[str], str | None] | None:
    cached = _IMAGE_DETAIL_CACHE.get(product_id)
    if cached is None:
        return None

    expires_at, image_url, images, url = cached
    if expires_at <= time.monotonic():
        _IMAGE_DETAIL_CACHE.pop(product_id, None)
        return None

    return image_url, list(images), url


def _cache_image_detail(
    product_id: str,
    image_url: str | None,
    images: list[str],
    url: str | None,
) -> None:
    # Keep the cache bounded even in a long-running development/production app.
    if len(_IMAGE_DETAIL_CACHE) >= IMAGE_CACHE_MAX_ENTRIES:
        oldest_key = min(
            _IMAGE_DETAIL_CACHE,
            key=lambda key: _IMAGE_DETAIL_CACHE[key][0],
        )
        _IMAGE_DETAIL_CACHE.pop(oldest_key, None)

    _IMAGE_DETAIL_CACHE[product_id] = (
        time.monotonic() + IMAGE_CACHE_TTL_SECONDS,
        image_url,
        list(images),
        url,
    )


async def _load_product_image_detail(
    product_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str | None, list[str], str | None]:
    """Load canonical image metadata through the already-used Kapruka MCP tool."""
    cached = _cached_image_detail(product_id)
    if cached is not None:
        return cached

    async with semaphore:
        # Check again after acquiring the semaphore in case another coroutine
        # populated the cache while this task was waiting.
        cached = _cached_image_detail(product_id)
        if cached is not None:
            return cached

        try:
            detail = await catalog_agent.get_product(product_id)
            if detail is None:
                logger.warning(
                    "Shopping-list product detail returned no data for image enrichment",
                    extra={"product_id": product_id},
                )
                return None, [], None

            raw_images = getattr(detail, "images", None)
            images = [
                image
                for value in (raw_images if isinstance(raw_images, (list, tuple)) else [])
                if (image := _clean_image(value))
            ]
            image_url = _clean_image(getattr(detail, "image_url", None))
            if image_url and image_url not in images:
                images.insert(0, image_url)
            if not image_url and images:
                image_url = images[0]

            detail_url = _clean_image(getattr(detail, "url", None))
            _cache_image_detail(product_id, image_url, images, detail_url)
            return image_url, images, detail_url

        except Exception as exc:  # noqa: BLE001
            # Image enrichment is best-effort. Never fail the shopping list
            # search just because one product's detail endpoint is unavailable.
            logger.warning(
                "Shopping-list product image enrichment failed",
                extra={
                    "product_id": product_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None, [], None


async def search_shopping_list_item(
    *, item_query: str
) -> ShoppingListItemSearchResponse:
    """Return up to twelve in-stock matches with images when Kapruka provides them.

    Search calls remain sequential at the shopping-list-flow level. For one
    item, image enrichment runs only for candidates whose search summaries do
    not already include an image, with a maximum of three simultaneous MCP
    product-detail requests. Enrichment failure leaves the original product
    card visible with its existing no-image fallback.
    """
    query = " ".join(str(item_query).split()).strip(" ,;\t\r\n")
    if len(query) < 2:
        return ShoppingListItemSearchResponse(
            item_query=query,
            products=[],
            shown_count=0,
            limit=MAX_PRODUCTS_PER_ITEM,
            message="Please provide a more specific product name.",
        )

    logger.info(
        "Shopping list item search started",
        extra={"item_query": query, "limit": MAX_PRODUCTS_PER_ITEM},
    )

    candidates = await catalog_agent.search_products(
        query=query,
        in_stock_only=True,
        limit=MAX_PRODUCTS_PER_ITEM,
    )

    # Validate and de-duplicate first. Keep the search summary's ID, name and
    # price authoritative; detail enrichment is used only for missing media.
    valid_candidates: list[tuple[str, Any, float, str, bool | None]] = []
    seen_ids: set[str] = set()

    for candidate in candidates:
        product_id = _valid_product_id(getattr(candidate, "product_id", None))
        name = str(getattr(candidate, "name", "") or "").strip()
        if not product_id or not name or product_id in seen_ids:
            continue

        try:
            price = float(getattr(candidate, "price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        stock = getattr(candidate, "in_stock", None)
        if stock is False:
            continue

        valid_candidates.append(
            (
                product_id,
                candidate,
                price,
                str(getattr(candidate, "currency", None) or "LKR"),
                stock,
            )
        )
        seen_ids.add(product_id)

        if len(valid_candidates) >= MAX_PRODUCTS_PER_ITEM:
            break

    # Fetch missing images with bounded concurrency. Do not fetch product
    # details when the search response already contains an image URL.
    semaphore = asyncio.Semaphore(IMAGE_ENRICHMENT_CONCURRENCY)
    enrichment_tasks: dict[str, asyncio.Task[tuple[str | None, list[str], str | None]]] = {}

    for product_id, candidate, _price, _currency, _stock in valid_candidates:
        if _candidate_images(candidate):
            continue
        enrichment_tasks[product_id] = asyncio.create_task(
            _load_product_image_detail(product_id, semaphore)
        )

    if enrichment_tasks:
        enrichment_ids = list(enrichment_tasks)
        enrichment_results = await asyncio.gather(
            *(enrichment_tasks[product_id] for product_id in enrichment_ids),
            return_exceptions=True,
        )
        enriched_media: dict[str, tuple[str | None, list[str], str | None]] = {}
        for product_id, result in zip(enrichment_ids, enrichment_results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Shopping-list image task failed",
                    extra={"product_id": product_id, "error_type": type(result).__name__},
                )
                continue
            enriched_media[product_id] = result
    else:
        enriched_media = {}

    products: list[ShoppingListProduct] = []
    enriched_image_count = 0

    for product_id, candidate, price, currency, stock in valid_candidates:
        images = _candidate_images(candidate)
        image_url = images[0] if images else None
        product_url = _clean_image(getattr(candidate, "url", None))

        if not image_url and product_id in enriched_media:
            detail_image, detail_images, detail_url = enriched_media[product_id]
            image_url = detail_image
            images = detail_images
            if not product_url:
                product_url = detail_url

        if image_url:
            enriched_image_count += 1

        products.append(
            ShoppingListProduct(
                product_id=product_id,
                name=str(getattr(candidate, "name", "")).strip(),
                price=price,
                currency=currency,
                image_url=image_url,
                url=product_url,
                in_stock=stock,
            )
        )

    logger.info(
        "Shopping list item search completed",
        extra={
            "item_query": query,
            "result_count": len(candidates),
            "shown_count": len(products),
            "images_available": enriched_image_count,
            "image_enrichment_requests": len(enrichment_tasks),
            "image_enrichment_concurrency": IMAGE_ENRICHMENT_CONCURRENCY,
            "limit": MAX_PRODUCTS_PER_ITEM,
        },
    )

    return ShoppingListItemSearchResponse(
        item_query=query,
        products=products,
        shown_count=len(products),
        limit=MAX_PRODUCTS_PER_ITEM,
        message=(
            None
            if products
            else f"No in-stock matches were found for '{query}'. You can skip this item and continue."
        ),
    )
