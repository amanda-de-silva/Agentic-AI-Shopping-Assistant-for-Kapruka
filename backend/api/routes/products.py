"""Read-only product-detail endpoint for interactive frontend cards."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.core.exceptions import MCPToolError
from backend.core.logging import get_logger
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import (
    parse_product_detail_payload,
)
from backend.mcp.tool_schemas import GetProductInput

router = APIRouter(tags=["products"])
logger = get_logger(__name__)


def _as_price(value: Any) -> float:
    """Safely normalize Kapruka's price representation."""
    try:
        if isinstance(value, dict):
            value = value.get("amount", 0)

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        return float(value or 0)

    except (TypeError, ValueError):
        return 0.0


async def _get_product_detail_from_mcp(
    product_id: str,
) -> dict[str, Any]:
    """
    Fetch product details using Kapruka MCP.

    Prefer JSON because its structured description field may be more complete
    than the human-readable Markdown response. If JSON format is rejected,
    retry with the existing default format.
    """
    client = get_mcp_client()

    try:
        result = await client.get_product(
            GetProductInput(
                product_id=product_id,
                response_format="json",
            )
        )

        return parse_product_detail_payload(
            result.data or {}
        )

    except MCPToolError as json_error:
        logger.warning(
            "Kapruka JSON product detail failed; "
            "retrying with default format for product %s: %s",
            product_id,
            json_error.detail,
        )

        try:
            result = await client.get_product(
                GetProductInput(
                    product_id=product_id,
                )
            )

            return parse_product_detail_payload(
                result.data or {}
            )

        except MCPToolError as markdown_error:
            logger.warning(
                "Kapruka product detail failed for %s: %s",
                product_id,
                markdown_error.detail,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Kapruka could not load the product details "
                    "right now."
                ),
            ) from markdown_error

    except Exception as exc:
        logger.exception(
            "Unexpected product-detail failure for %s",
            product_id,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Kapruka could not load the product details "
                "right now."
            ),
        ) from exc


@router.get("/products/{product_id}")
async def product_detail(
    product_id: str,
) -> dict[str, Any]:
    """
    Return public product details for existing frontend cards and comparison.

    This endpoint does not alter recommendation selection, ranking, the cart,
    checkout, or product eligibility.
    """
    raw = await _get_product_detail_from_mcp(product_id)

    resolved_id = str(
        raw.get("product_id")
        or raw.get("id")
        or product_id
    ).strip()

    name = str(
        raw.get("name") or ""
    ).strip()

    if not name:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    raw_images = raw.get("images") or []

    images = (
        [
            str(url)
            for url in raw_images
            if url
        ]
        if isinstance(raw_images, list)
        else []
    )

    image_url = (
        raw.get("image_url")
        or (images[0] if images else None)
    )

    if image_url and not images:
        images = [str(image_url)]

    description = raw.get("description")

    if isinstance(description, str):
        # Preserve the entire description; do not truncate it here.
        description = description.strip()
    else:
        description = None

    return {
        "product_id": resolved_id,
        "name": name,
        "price": _as_price(raw.get("price")),
        "currency": str(raw.get("currency") or "LKR"),
        "in_stock": raw.get("in_stock"),
        "stock_note": raw.get("stock_note"),
        "image_url": image_url,
        "images": images,
        "url": raw.get("url"),
        "description": description,
        "category": raw.get("category"),
        "vendor": raw.get("vendor"),
        "weight": raw.get("weight"),
        "ships_internationally": raw.get(
            "ships_internationally"
        ),
        "variants": raw.get("variants") or [],
        "shipping": raw.get("shipping"),
    }