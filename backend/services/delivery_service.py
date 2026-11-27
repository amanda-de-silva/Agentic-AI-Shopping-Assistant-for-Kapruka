"""Kapruka-aware delivery city/date resolution and cart delivery validation."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.exceptions import CheckoutError, MCPToolError
from backend.core.logging import get_logger
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import (
    extract_markdown_result,
    parse_check_delivery_markdown,
    parse_delivery_cities_markdown,
)
from backend.mcp.tool_schemas import (
    CheckDeliveryInput,
    ListDeliveryCitiesInput,
)
from config.settings import get_params_config

logger = get_logger(__name__)


def _delivery_timezone() -> ZoneInfo:
    name = str(
        get_params_config()
        .get("checkout", {})
        .get("delivery_timezone", "Asia/Colombo")
    )

    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Colombo")


def today_in_delivery_timezone() -> date:
    return datetime.now(_delivery_timezone()).date()


def _normalize_city(value: str) -> str:
    return "".join(
        character.lower()
        for character in str(value or "").strip()
        if character.isalnum()
    )


async def resolve_canonical_city(
    city_query: str,
) -> str:
    """Resolve a user-entered city/alias to Kapruka's canonical city name."""
    query = str(city_query or "").strip()

    if not query:
        raise CheckoutError(
            "Delivery city is required.",
            status_code=422,
            code="delivery_city_required",
        )

    client = get_mcp_client()

    try:
        result = await client.list_delivery_cities(
            ListDeliveryCitiesInput(
                query=query,
                limit=10,
            )
        )
    except MCPToolError as exc:
        raise CheckoutError(
            f"Kapruka could not resolve the delivery city: {exc.detail}",
            status_code=502,
            code="delivery_city_lookup_failed",
        ) from exc
    except Exception as exc:
        logger.exception(
            "Delivery city lookup failed",
            extra={"city_query": query},
        )

        raise CheckoutError(
            "Kapruka could not check the delivery city right now.",
            status_code=502,
            code="delivery_city_lookup_failed",
        ) from exc

    data = result.data or {}
    markdown = extract_markdown_result(data)

    if markdown is not None:
        parsed = parse_delivery_cities_markdown(markdown)
    elif isinstance(data, dict):
        parsed = data
    else:
        parsed = {}

    raw_cities = parsed.get("cities") or []

    candidates: list[tuple[str, list[str]]] = []

    for item in raw_cities:
        if isinstance(item, str):
            candidates.append(
                (
                    item.strip(),
                    [],
                )
            )
            continue

        if not isinstance(item, dict):
            continue

        name = str(
            item.get("name") or ""
        ).strip()

        aliases = item.get(
            "aliases"
        ) or []

        if name:
            candidates.append(
                (
                    name,
                    [
                        str(alias).strip()
                        for alias in aliases
                        if str(alias).strip()
                    ],
                )
            )

    normalized_query = _normalize_city(
        query
    )

    # Exact canonical-name / alias match.
    exact_matches: list[str] = []

    for name, aliases in candidates:
        if (
            _normalize_city(name)
            == normalized_query
        ):
            exact_matches.append(name)
            continue

        if any(
            _normalize_city(alias)
            == normalized_query
            for alias in aliases
        ):
            exact_matches.append(name)

    exact_matches = list(
        dict.fromkeys(exact_matches)
    )

    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(exact_matches) > 1:
        raise CheckoutError(
            f"'{query}' matches more than one Kapruka delivery city. "
            "Please enter a more specific city.",
            status_code=422,
            code="delivery_city_ambiguous",
        )

    # Conservative partial fallback.
    partial_matches: list[str] = []

    for name, aliases in candidates:
        searchable = [
            name,
            *aliases,
        ]

        if any(
            normalized_query
            in _normalize_city(value)
            or _normalize_city(value)
            in normalized_query
            for value in searchable
            if value
        ):
            partial_matches.append(name)

    partial_matches = list(
        dict.fromkeys(partial_matches)
    )

    if len(partial_matches) == 1:
        return partial_matches[0]

    raise CheckoutError(
        f"Kapruka does not recognize '{query}' as a delivery city.",
        status_code=422,
        code="delivery_city_not_found",
    )


async def _check_single_delivery(
    *,
    city: str,
    delivery_date: date,
    product_id: str | None = None,
) -> dict[str, Any]:
    client = get_mcp_client()

    try:
        result = await client.check_delivery(
            CheckDeliveryInput(
                city=city,
                delivery_date=delivery_date.isoformat(),
                product_id=product_id,
            )
        )

    except MCPToolError as exc:
        raise CheckoutError(
            f"Kapruka could not validate delivery: {exc.detail}",
            status_code=502,
            code="delivery_check_failed",
        ) from exc

    except Exception as exc:
        logger.exception(
            "Delivery check failed",
            extra={
                "city": city,
                "delivery_date": delivery_date.isoformat(),
                "product_id": product_id,
            },
        )

        raise CheckoutError(
            "Kapruka could not validate delivery right now.",
            status_code=502,
            code="delivery_check_failed",
        ) from exc

    data = result.data or {}
    markdown = extract_markdown_result(data)

    if markdown is not None:
        parsed = parse_check_delivery_markdown(
            markdown
        )
    elif isinstance(data, dict):
        parsed = dict(data)
    else:
        parsed = {}

    if (
        parsed.get("error_code")
        and parsed.get("deliverable") is False
    ):
        parsed.setdefault(
            "notes",
            parsed.get("message")
            or parsed.get("error_code"),
        )

    return parsed


async def validate_cart_delivery(
    *,
    city: str,
    delivery_date: date,
    product_ids: list[str],
) -> str:
    """Validate one concrete date for the entire cart.

    Returns the canonical Kapruka city that must be sent to create_order.
    The first failing item/date short-circuits later checks.
    """
    canonical_city = await resolve_canonical_city(
        city
    )

    distinct_product_ids = list(
        dict.fromkeys(
            str(product_id).strip()
            for product_id in product_ids
            if str(product_id).strip()
        )
    )

    if not distinct_product_ids:
        raise CheckoutError(
            "Your cart does not contain any valid products.",
            status_code=422,
            code="checkout_cart_invalid",
        )

    # First perform city/date-level validation.
    general = await _check_single_delivery(
        city=canonical_city,
        delivery_date=delivery_date,
        product_id=None,
    )

    if general.get("deliverable") is not True:
        reason = (
            general.get("notes")
            or general.get("message")
            or general.get("error_code")
            or (
                "Kapruka cannot deliver to this city "
                "on the selected date."
            )
        )

        raise CheckoutError(
            f"Delivery is unavailable for {canonical_city} on "
            f"{delivery_date.isoformat()}. {reason}",
            status_code=422,
            code="delivery_unavailable",
        )

    # Product-specific validation.
    for product_id in distinct_product_ids:
        item_check = await _check_single_delivery(
            city=canonical_city,
            delivery_date=delivery_date,
            product_id=product_id,
        )

        if item_check.get("deliverable") is not True:
            reason = (
                item_check.get("notes")
                or item_check.get("message")
                or item_check.get("error_code")
                or (
                    "This product is not deliverable "
                    "to the selected city/date."
                )
            )

            raise CheckoutError(
                f"Product {product_id} is not deliverable to "
                f"{canonical_city} on {delivery_date.isoformat()}. "
                f"{reason}",
                status_code=422,
                code="product_delivery_unavailable",
            )

    return canonical_city


async def find_earliest_delivery_date(
    *,
    city: str,
    product_ids: list[str],
    start_date: date | None = None,
    max_days: int | None = None,
) -> dict[str, Any]:
    """Find the earliest common delivery date for the entire cart.

    Kapruka's MCP exposes date-specific check_delivery rather than a dedicated
    earliest-date tool, so we derive the earliest valid date by checking
    successive dates and stopping at the first fully valid date.
    """
    canonical_city = await resolve_canonical_city(
        city
    )

    distinct_product_ids = list(
        dict.fromkeys(
            str(product_id).strip()
            for product_id in product_ids
            if str(product_id).strip()
        )
    )

    if not distinct_product_ids:
        raise CheckoutError(
            "Your cart does not contain any valid products.",
            status_code=422,
            code="checkout_cart_invalid",
        )

    config = get_params_config().get(
        "checkout",
        {}
    )

    horizon = int(
        max_days
        if max_days is not None
        else config.get(
            "earliest_delivery_search_days",
            7,
        )
    )

    # Hard upper bound so a frontend/client cannot turn this into a large
    # upstream request loop.
    horizon = max(
        1,
        min(horizon, 14),
    )

    first_date = (
        start_date
        if start_date is not None
        else today_in_delivery_timezone()
    )

    for offset in range(horizon):
        candidate = (
            first_date
            + timedelta(days=offset)
        )

        try:
            await validate_cart_delivery(
                city=canonical_city,
                delivery_date=candidate,
                product_ids=distinct_product_ids,
            )

            logger.info(
                "Earliest Kapruka delivery date found",
                extra={
                    "city": canonical_city,
                    "date": candidate.isoformat(),
                    "days_checked": offset + 1,
                    "product_count": len(
                        distinct_product_ids
                    ),
                },
            )

            return {
                "requested_city": city,
                "canonical_city": canonical_city,
                "earliest_date": candidate,
                "checked_from": first_date,
                "checked_until": candidate,
                "days_checked": offset + 1,
            }

        except CheckoutError as exc:

            # These are legitimate "try another date" results.
            if exc.code in {
                "delivery_unavailable",
                "product_delivery_unavailable",
            }:
                logger.info(
                    "Delivery date unavailable; trying next date",
                    extra={
                        "city": canonical_city,
                        "date": candidate.isoformat(),
                        "reason": exc.message,
                    },
                )
                continue

            # Infrastructure / upstream errors must propagate.
            raise

    return {
        "requested_city": city,
        "canonical_city": canonical_city,
        "earliest_date": None,
        "checked_from": first_date,
        "checked_until": (
            first_date
            + timedelta(days=horizon - 1)
        ),
        "days_checked": horizon,
    }