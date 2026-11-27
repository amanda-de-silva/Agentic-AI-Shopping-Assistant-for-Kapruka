"""ProductSummary / ProductDetail schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProductSummary(BaseModel):
    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    in_stock: bool | None = None
    image_url: str | None = None
    url: str | None = None

    # Preserved from Kapruka's product metadata so recommendation
    # filtering can distinguish cakes, watches, flowers, hampers, etc.
    category: str | None = None
    vendor: str | None = None

    # Raw product/search/detail text used for deterministic safety
    # and semantic relevance checks.
    safety_text: str = ""


class ProductDetail(ProductSummary):
    description: str | None = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    shipping: dict[str, Any] | None = None