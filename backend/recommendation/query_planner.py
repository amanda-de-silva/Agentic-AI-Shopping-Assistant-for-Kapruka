"""Catalog query planning with live category filtering and MCP pagination."""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.agents import catalog_agent
from backend.core.logging import get_logger
from backend.recommendation.models import ShoppingSpec

logger = get_logger(__name__)


_CATEGORY_CACHE: list[dict[str, Any]] | None = None
_CATEGORY_CACHE_LOCK = asyncio.Lock()

_CATEGORY_FILTER_BLOCK_TERMS = {
    "card",
    "cards",
    "candle",
    "candles",
    "cutter",
    "cutters",
    "knife",
    "knives",
    "topper",
    "toppers",
    "stand",
    "stands",
    "box",
    "boxes",
    "holder",
    "holders",
    "decor",
    "decoration",
    "decorations",
    "decoration set",
    "accessory",
    "accessories",
}


@dataclass
class CandidateRetrieval:
    """Products plus the pagination state needed for the next continuation."""
    products: list[Any] = field(default_factory=list)
    next_cursors: dict[str, str] = field(default_factory=dict)
    category: str | None = None


def _norm(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value).strip().lower(),
    )


def _singular_forms(value: str) -> set[str]:
    text = _norm(value)

    if not text:
        return set()

    variants = {text}

    if text.endswith("ies") and len(text) > 3:
        variants.add(text[:-3] + "y")

    if text.endswith("ses") and len(text) > 3:
        variants.add(text[:-2])

    if text.endswith("es") and len(text) > 2:
        variants.add(text[:-2])

    if (
        text.endswith("s")
        and not text.endswith("ss")
        and len(text) > 1
    ):
        variants.add(text[:-1])

    return variants


def _category_slug_from_url(url: str) -> str:
    clean = (
        str(url or "")
        .strip()
        .rstrip("/")
    )

    if not clean:
        return ""

    return _norm(
        clean.rsplit(
            "/",
            1,
        )[-1]
    )


def _category_filter_is_blocked(
    spec: ShoppingSpec,
) -> bool:
    values: list[str] = []

    if spec.product_family:
        values.append(
            spec.product_family
        )

    values.extend(
        spec.search_queries[:3]
    )

    haystack = _norm(
        " ".join(values)
    )

    if not haystack:
        return False

    return any(
        re.search(
            rf"(?<![a-z0-9])"
            rf"{re.escape(term)}"
            rf"(?![a-z0-9])",
            haystack,
        )
        for term in _CATEGORY_FILTER_BLOCK_TERMS
    )


async def _get_live_categories() -> list[dict[str, Any]]:
    global _CATEGORY_CACHE

    if _CATEGORY_CACHE is not None:
        return _CATEGORY_CACHE

    async with _CATEGORY_CACHE_LOCK:
        if _CATEGORY_CACHE is not None:
            return _CATEGORY_CACHE

        try:
            categories = (
                await catalog_agent.list_categories(
                    depth=1
                )
            )

            normalized: list[
                dict[str, Any]
            ] = []

            for category in (
                categories or []
            ):
                if not isinstance(
                    category,
                    dict,
                ):
                    continue

                raw_name = str(
                    category.get(
                        "name"
                    )
                    or ""
                ).strip()

                if not raw_name:
                    continue

                url = str(
                    category.get(
                        "url"
                    )
                    or ""
                ).strip()

                normalized.append(
                    {
                        "name": raw_name,
                        "name_norm": _norm(
                            raw_name
                        ),
                        "url": url,
                        "slug": _category_slug_from_url(
                            url
                        ),
                    }
                )

            _CATEGORY_CACHE = normalized

            logger.info(
                "Loaded %d live Kapruka categories for search filtering.",
                len(normalized),
            )

        except Exception as exc:
            logger.warning(
                "Live Kapruka category lookup failed; "
                "unfiltered product search will be used: %s",
                exc,
            )
            _CATEGORY_CACHE = []

    return _CATEGORY_CACHE


def _category_matches_family(
    family: str,
    category: dict[str, Any],
) -> bool:
    family_n = _norm(family)

    category_name = _norm(
        str(
            category.get("name_norm")
            or category.get("name")
            or ""
        )
    )

    category_slug = _norm(
        str(
            category.get("slug")
            or ""
        )
    )

    if not family_n or not category_name:
        return False

    family_forms = _singular_forms(
        family_n
    )

    category_forms = _singular_forms(
        category_name
    )

    if family_forms.intersection(
        category_forms
    ):
        return True

    if category_slug:
        if family_forms.intersection(
            _singular_forms(
                category_slug
            )
        ):
            return True

    for candidate in (
        category_name,
        category_slug,
    ):
        candidate_n = _norm(
            candidate
        )

        if not candidate_n:
            continue

        for category_form in _singular_forms(
            candidate_n
        ):
            if (
                family_n == category_form
                or family_n.endswith(
                    f" {category_form}"
                )
            ):
                return True

    return False


async def _resolve_category_values(
    spec: ShoppingSpec,
    preferred_category: str | None = None,
) -> list[str]:
    """Return a small ordered set of exact live category values to try."""
    family = _norm(
        spec.product_family or ""
    )

    if (
        not family
        or _category_filter_is_blocked(
            spec
        )
    ):
        return []

    categories = (
        await _get_live_categories()
    )

    if not categories:
        return []

    matches: list[
        tuple[int, str]
    ] = []

    for category in categories:
        name = str(
            category.get("name")
            or ""
        ).strip()

        if not name:
            continue

        score = 0

        if (
            preferred_category
            and _norm(name)
            == _norm(
                preferred_category
            )
        ):
            score = 100

        family_forms = _singular_forms(
            family
        )
        name_forms = _singular_forms(
            name
        )

        if family_forms.intersection(
            name_forms
        ):
            score = max(
                score,
                90,
            )
        elif _category_matches_family(
            family,
            category,
        ):
            score = max(
                score,
                70,
            )

        if score:
            matches.append(
                (
                    score,
                    name,
                )
            )

    matches.sort(
        key=lambda item: (
            -item[0],
            item[1].lower(),
        )
    )

    values: list[str] = []

    def add(value: str | None) -> None:
        value = str(
            value or ""
        ).strip()

        if not value:
            return

        key = value.casefold()

        if key not in {
            existing.casefold()
            for existing in values
        }:
            values.append(value)

    if preferred_category:
        add(
            preferred_category
        )

    if matches:
        add(matches[0][1])

        # Kapruka has historically exposed some taxonomy values with
        # different casing. Try the title-cased form once before falling
        # back to the old unfiltered search procedure.
        add(
            matches[0][1].title()
        )

        # Also try the live URL slug when it differs.
        for category in categories:
            if _norm(
                str(
                    category.get("name")
                    or ""
                )
            ) == _norm(
                matches[0][1]
            ):
                add(
                    category.get(
                        "slug"
                    )
                )
                break

    return values[:3]


def build_search_queries(
    spec: ShoppingSpec,
    *,
    fallback_query: str | None = None,
) -> list[str]:
    queries: OrderedDict[
        str,
        None,
    ] = OrderedDict()

    base_parts: list[str] = []

    if spec.product_family:
        base_parts.append(
            spec.product_family
        )

    if spec.occasion:
        base_parts.append(
            spec.occasion
        )

    base_parts.extend(
        item.term
        for item in (
            spec.required_attributes[:5]
        )
    )

    base_parts.extend(
        spec.required_components[:4]
    )

    if spec.quantity:
        base_parts.append(
            f"{spec.quantity.value:g}"
            f"{spec.quantity.unit}"
        )

    if base_parts:
        queries[
            _norm(
                " ".join(base_parts)
            )
        ] = None

    for explicit_query in (
        spec.search_queries
    ):
        query = _norm(
            explicit_query
        )
        if query:
            queries[query] = None

    # Soft preferences remain retrieval expansions only.
    if spec.product_family:
        for preference in (
            spec.preferred_attributes[:2]
        ):
            query = _norm(
                " ".join(
                    [
                        spec.product_family,
                        preference,
                    ]
                )
            )
            if query:
                queries[query] = None
    else:
        for preference in (
            spec.preferred_attributes[:3]
        ):
            query = _norm(
                preference
            )
            if query:
                queries[query] = None

    if fallback_query:
        query = _norm(
            fallback_query
        )
        if query:
            queries[query] = None

    if not queries:
        queries["gift"] = None

    return list(
        queries
    )[:3]


def _cursor_key(
    category: str | None,
    query: str,
) -> str:
    return (
        f"category={_norm(category or '')}"
        f"|query={_norm(query)}"
    )


async def _run_search_page(
    *,
    query: str,
    category: str | None,
    spec: ShoppingSpec,
    search: Callable[..., Awaitable[list]],
    search_page: Callable[..., Awaitable[dict[str, Any]]] | None,
    search_limit: int,
    cursor: str | None,
) -> tuple[list[Any], str | None]:
    kwargs = {
        "query": query,
        "category": category,
        "min_price": (
            spec.budget.min_price
            if (
                spec.budget
                and spec.budget.currency.upper()
                == "LKR"
            )
            else None
        ),
        "max_price": (
            spec.budget.max_price
            if (
                spec.budget
                and spec.budget.currency.upper()
                == "LKR"
            )
            else None
        ),
        "in_stock_only": spec.in_stock_only,
        "limit": search_limit,
    }

    if search_page is not None:
        page = await search_page(
            **kwargs,
            cursor=cursor,
        )

        if not isinstance(
            page,
            dict,
        ):
            return [], None

        products = page.get(
            "products",
            [],
        )

        next_cursor = page.get(
            "next_cursor"
        )

        return (
            list(
                products or []
            ),
            (
                str(next_cursor)
                if next_cursor
                else None
            ),
        )

    # Backward-compatible path for unit tests/legacy callers that provide
    # only the original list-returning search function.
    products = await search(
        **kwargs
    )

    return (
        list(products or []),
        None,
    )


async def _gather_searches(
    spec: ShoppingSpec,
    queries: list[str],
    search: Callable[..., Awaitable[list]],
    search_page: Callable[..., Awaitable[dict[str, Any]]] | None,
    search_limit: int,
    *,
    category: str | None,
    cursors: dict[str, str] | None,
) -> tuple[list, dict[str, str]]:
    cursor_map = (
        cursors or {}
    )

    requests = []

    for query in queries:
        key = _cursor_key(
            category,
            query,
        )

        requests.append(
            _run_search_page(
                query=query,
                category=category,
                spec=spec,
                search=search,
                search_page=search_page,
                search_limit=search_limit,
                cursor=cursor_map.get(
                    key
                ),
            )
        )

    results = await asyncio.gather(
        *requests,
        return_exceptions=True,
    )

    merged: list[Any] = []
    next_cursors: dict[
        str,
        str,
    ] = {}

    for query, result in zip(
        queries,
        results,
    ):
        if isinstance(
            result,
            Exception,
        ):
            logger.warning(
                "Catalog search query failed: %s",
                result,
            )
            continue

        products, next_cursor = result

        merged.extend(
            products
        )

        key = _cursor_key(
            category,
            query,
        )

        if next_cursor:
            next_cursors[
                key
            ] = next_cursor

    return (
        merged,
        next_cursors,
    )


async def retrieve_candidates(
    spec: ShoppingSpec,
    *,
    search: Callable[..., Awaitable[list]],
    search_page: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    search_limit: int,
    fallback_query: str | None = None,
    continuation_cursors: dict[str, str] | None = None,
    preferred_category: str | None = None,
) -> CandidateRetrieval:
    queries = build_search_queries(
        spec,
        fallback_query=fallback_query,
    )

    category_values = (
        await _resolve_category_values(
            spec,
            preferred_category=preferred_category,
        )
    )

    # Try the exact category value first, then the small live-taxonomy
    # alternatives. Do not touch the old broad-search procedure unless all
    # category attempts produce no products.
    combined_cursors: dict[
        str,
        str,
    ] = dict(
        continuation_cursors or {}
    )

    if category_values:
        for category in category_values:
            results, next_cursors = (
                await _gather_searches(
                    spec,
                    queries,
                    search,
                    search_page,
                    search_limit,
                    category=category,
                    cursors=combined_cursors,
                )
            )

            if results:
                by_id: dict[
                    str,
                    object,
                ] = {}

                for product in results:
                    product_id = getattr(
                        product,
                        "product_id",
                        None,
                    )
                    if product_id:
                        by_id[
                            str(product_id)
                        ] = product

                # Replace only cursor entries for this category. Cursors for
                # unrelated/older categories remain harmlessly persisted.
                combined_cursors = {
                    key: value
                    for key, value
                    in combined_cursors.items()
                    if not key.startswith(
                        f"category={_norm(category)}|"
                    )
                }
                combined_cursors.update(
                    next_cursors
                )

                logger.info(
                    "Using Kapruka category-filtered retrieval with category '%s'.",
                    category,
                )

                return CandidateRetrieval(
                    products=list(
                        by_id.values()
                    ),
                    next_cursors=combined_cursors,
                    category=category,
                )

            logger.warning(
                "Category-filtered search returned no products for '%s'.",
                category,
            )

    # Preserve the existing broad-search procedure as the final fallback.
    results, next_cursors = (
        await _gather_searches(
            spec,
            queries,
            search,
            search_page,
            search_limit,
            category=None,
            cursors=combined_cursors,
        )
    )

    by_id: dict[str, object] = {}

    for product in results:
        product_id = getattr(
            product,
            "product_id",
            None,
        )
        if product_id:
            by_id[
                str(product_id)
            ] = product

    if category_values:
        logger.warning(
            "All live Kapruka category-filtered searches returned no products; "
            "falling back to the existing unfiltered search procedure."
        )

    combined_cursors = {
        key: value
        for key, value
        in combined_cursors.items()
        if not key.startswith(
            "category=|"
        )
    }
    combined_cursors.update(
        next_cursors
    )

    return CandidateRetrieval(
        products=list(
            by_id.values()
        ),
        next_cursors=combined_cursors,
        category=None,
    )
