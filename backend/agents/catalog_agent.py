"""Catalog search, enrichment, relevance filtering, and safety guards."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from backend.llm.openrouter_client import get_openrouter_client
from backend.llm.prompt_loader import render_prompt
from backend.core.logging import get_logger
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import (
    extract_markdown_result,
    parse_categories_markdown,
    parse_product_detail_markdown,
    parse_search_products_markdown,
)
from backend.mcp.tool_schemas import (
    GetProductInput,
    ListCategoriesInput,
    SearchProductsInput,
)
from backend.schemas.product import ProductDetail, ProductSummary
from backend.observability.tracing import traced

logger = get_logger(__name__)


SAFETY_SYNONYMS = {
    "peanut": ("peanuts", "groundnut", "groundnuts"),
    "milk": ("dairy", "casein", "whey", "lactose"),
    "egg": ("eggs", "albumin", "mayonnaise"),
    "tree nut": (
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "pistachio",
        "hazelnut",
        "macadamia",
    ),
    "fish": ("anchovy", "anchovies", "tuna", "salmon", "cod"),
    "shellfish": (
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "mussel",
        "clam",
        "oyster",
    ),
    "soy": ("soya", "soybean"),
    "wheat": ("gluten", "barley", "rye"),
}


def _to_summary(raw: dict[str, Any]) -> ProductSummary:
    images = raw.get("images")
    first_image = (
        images[0]
        if isinstance(images, list) and images
        else raw.get("image_url")
    )

    return ProductSummary(
        product_id=str(
            raw.get("product_id")
            or raw.get("id")
        ),
        name=raw.get(
            "name",
            "Unknown product",
        ),
        price=float(
            raw.get("price", 0)
            or 0
        ),
        currency=raw.get(
            "currency",
            "LKR",
        ),
        in_stock=raw.get(
            "in_stock"
        ),
        image_url=first_image,
        url=raw.get("url"),
        safety_text=str(
            raw.get("safety_text")
            or ""
        ),
    )


def _parse_search_page(
    data: dict[str, Any],
) -> tuple[list[ProductSummary], str | None]:
    markdown = extract_markdown_result(
        data
    )

    if markdown is not None:
        parsed = parse_search_products_markdown(
            markdown
        )
        items = parsed.get(
            "products",
            [],
        )

        # Markdown search responses may include pagination information.
        next_cursor = (
            parsed.get("next_cursor")
            or data.get("next_cursor")
        )

    else:
        items = (
            data.get("products")
            or data.get("items")
            or []
        )
        next_cursor = data.get(
            "next_cursor"
        )

    products = [
        _to_summary(item)
        for item in items
        if isinstance(item, dict)
    ]

    return (
        products,
        str(next_cursor)
        if next_cursor
        else None,
    )


@traced("agent.catalog.search_products")
async def search_products(
    *,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool | None = None,
    sort: str | None = None,
    limit: int = 20,
) -> list[ProductSummary]:
    result = await get_mcp_client().search_products(
        SearchProductsInput(
            q=query,
            category=category,
            min_price=min_price,
            max_price=max_price,
            in_stock_only=in_stock_only,
            sort=sort,
            limit=limit,
        )
    )

    data = result.data or {}

    products, _ = _parse_search_page(
        data
    )

    return products


@traced("agent.catalog.search_products_page")
async def search_products_page(
    *,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool | None = None,
    sort: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """
    Search Kapruka with pagination support.

    Existing search_products() remains unchanged for callers that only need
    ProductSummary objects. Recommendation continuation uses this page-aware
    function so "show more" can request the next MCP result page rather than
    repeating the first page.
    """
    result = await get_mcp_client().search_products(
        SearchProductsInput(
            q=query,
            category=category,
            min_price=min_price,
            max_price=max_price,
            in_stock_only=in_stock_only,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
    )

    data = result.data or {}

    products, next_cursor = _parse_search_page(
        data
    )

    return {
        "products": products,
        "next_cursor": next_cursor,
    }


@traced("agent.catalog.get_product")
async def get_product(
    product_id: str,
) -> ProductDetail | None:
    result = await get_mcp_client().get_product(
        GetProductInput(
            product_id=product_id
        )
    )

    data = result.data or {}

    if not data:
        return None

    markdown = extract_markdown_result(
        data
    )

    raw = (
        parse_product_detail_markdown(
            markdown
        )
        if markdown is not None
        else data
    )

    if (
        not raw.get("product_id")
        and not raw.get("name")
    ):
        return None

    if markdown is not None:
        raw["safety_text"] = markdown
    elif not raw.get("safety_text"):
        raw["safety_text"] = json.dumps(
            raw,
            default=str,
        )

    return ProductDetail(
        **_to_summary(raw).model_dump(),
        description=raw.get(
            "description"
        ),
        variants=raw.get(
            "variants",
            [],
        ),
        images=raw.get(
            "images",
            [],
        ),
        shipping=raw.get(
            "shipping"
        ),
    )


async def _get_product_resilient(
    product_id: str,
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
) -> ProductDetail | None:
    """Fetch one product with bounded retries and explicit failure logging."""
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            product = await get_product(product_id)

            if product is not None:
                if attempt > 1:
                    logger.info(
                        "Product enrichment recovered after retry: id=%s attempt=%d",
                        product_id,
                        attempt,
                    )
                return product

            last_error = RuntimeError(
                "MCP returned no product detail"
            )

        except Exception as exc:  # noqa: BLE001
            last_error = exc

        if attempt < max_attempts:
            await asyncio.sleep(
                base_delay_seconds * (2 ** (attempt - 1))
            )

    logger.warning(
        "Product enrichment failed after %d attempts: id=%s error=%s",
        max_attempts,
        product_id,
        last_error,
    )
    return None


async def enrich_products_with_failures(
    products: list[ProductSummary],
    *,
    max_concurrency: int = 8,
    max_attempts: int = 3,
) -> tuple[list[ProductDetail], list[str]]:
    """Enrich products with bounded concurrency and return failed IDs explicitly."""
    if not products:
        return [], []

    semaphore = asyncio.Semaphore(
        max(1, int(max_concurrency))
    )

    async def enrich_one(product: ProductSummary):
        async with semaphore:
            detail = await _get_product_resilient(
                product.product_id,
                max_attempts=max(1, int(max_attempts)),
            )
            return product.product_id, detail

    results = await asyncio.gather(
        *(enrich_one(product) for product in products),
        return_exceptions=True,
    )

    details: list[ProductDetail] = []
    failed_ids: list[str] = []

    for original, result in zip(products, results):
        if isinstance(result, Exception):
            logger.warning(
                "Unexpected enrichment task failure: id=%s error=%s",
                original.product_id,
                result,
            )
            failed_ids.append(
                str(original.product_id)
            )
            continue

        product_id, detail = result

        if isinstance(detail, ProductDetail):
            details.append(detail)
        else:
            failed_ids.append(str(product_id))

    return details, failed_ids


async def enrich_products(
    products: list[ProductSummary],
    *,
    max_concurrency: int = 8,
    max_attempts: int = 3,
) -> list[ProductDetail]:
    details, _ = await enrich_products_with_failures(
        products,
        max_concurrency=max_concurrency,
        max_attempts=max_attempts,
    )
    return details


async def list_categories(
    depth: int = 1,
) -> list[dict[str, Any]]:
    result = await get_mcp_client().list_categories(
        ListCategoriesInput(
            depth=depth
        )
    )

    data = result.data or {}

    markdown = extract_markdown_result(
        data
    )

    if markdown is not None:
        return parse_categories_markdown(
            markdown
        ).get(
            "categories",
            [],
        )

    return (
        data.get("categories")
        or []
    )


def _expanded_terms(
    forbidden_terms: list[str],
) -> list[str]:
    terms: list[str] = []

    for term in forbidden_terms:
        normalized = (
            term.strip().lower()
        )

        if not normalized:
            continue

        terms.append(
            normalized
        )

        terms.extend(
            SAFETY_SYNONYMS.get(
                normalized,
                (),
            )
        )

    return list(
        dict.fromkeys(terms)
    )


def violates_constraints(
    product: ProductSummary | ProductDetail,
    forbidden_terms: list[str],
) -> bool:
    """Deterministic fail-closed text guard using word/phrase matching."""
    haystack = " ".join(
        part
        for part in (
            product.name,
            product.safety_text,
            getattr(
                product,
                "description",
                None,
            ),
        )
        if part
    ).lower()

    for term in _expanded_terms(
        forbidden_terms
    ):
        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(term)}"
            rf"(?![a-z0-9])"
        )

        if re.search(
            pattern,
            haystack,
            re.IGNORECASE,
        ):
            return True

    return False


@traced(
    "agent.catalog.filter_for_relevance"
)
async def filter_for_relevance(
    *,
    products: list[ProductSummary],
    user_query: str,
    memory_context: str | None = None,
) -> list[ProductSummary]:
    if not products:
        return products

    client = (
        get_openrouter_client()
    )

    candidates_text = "\n".join(
        (
            f"- id={p.product_id}, "
            f"name={p.name!r}, "
            f"price={p.price} {p.currency}, "
            f"details={p.safety_text[:1200]!r}"
        )
        for p in products
    )

    system_prompt = render_prompt(
        "catalog_agent_system.jinja",
        memory_context=(
            memory_context
            or "(none)"
        ),
        user_message=user_query,
        candidates=candidates_text,
    )

    try:
        response = await client.chat_completion(
            task="catalog_agent",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Return the "
                        "suitable_product_ids now."
                    ),
                },
            ],
            response_format={
                "type": "json_object"
            },
        )

        parsed = json.loads(
            client.extract_text(
                response
            )
        )

    except Exception:
        return products

    suitable_ids = parsed.get(
        "suitable_product_ids"
    )

    if (
        not isinstance(
            suitable_ids,
            list,
        )
        or not suitable_ids
    ):
        return products

    suitable_id_set = set(
        map(
            str,
            suitable_ids,
        )
    )

    return [
        product
        for product in products
        if product.product_id
        in suitable_id_set
    ]
