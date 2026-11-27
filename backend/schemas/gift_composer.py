"""Request/response schemas for the read-only Gift Composer."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


DeliveryStatus = Literal[
    "not_checked",
    "confirmed",
    "unavailable",
    "unknown",
]


class GiftComposerRequest(BaseModel):
    recipient: str = Field(min_length=1, max_length=100)
    occasion: str = Field(min_length=1, max_length=100)
    budget_lkr: float | None = Field(default=None, gt=0, le=10_000_000)
    preferences: str = Field(default="", max_length=500)
    # Ignore saved profile likes for this preview only. Saved allergies and
    # dislikes remain in force as exclusions for safety.
    ignore_saved_preferences: bool = False
    avoid: str = Field(default="", max_length=300)
    allergies: str = Field(default="", max_length=300)
    delivery_city: str | None = Field(default=None, max_length=100)
    delivery_date: date | None = None


class GiftProductOption(BaseModel):
    """One swappable product option within a gift-bundle slot."""

    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    category: str | None = None
    group: str
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    url: str | None = None
    description: str = ""
    in_stock: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    delivery_status: DeliveryStatus = "not_checked"
    delivery_message: str | None = None


class GiftProductPreview(GiftProductOption):
    """Selected product displayed in a bundle, with alternatives for its slot."""

    options: list[GiftProductOption] = Field(default_factory=list)


class GiftBundlePreview(BaseModel):
    id: str
    title: str
    description: str
    total: float
    currency: str = "LKR"
    products: list[GiftProductPreview] = Field(default_factory=list)
    delivery_status: DeliveryStatus = "not_checked"
    delivery_message: str | None = None


class GiftComposerResponse(BaseModel):
    status: Literal["bundles_found", "single_items_only", "no_eligible_products"]
    recipient: str
    occasion: str
    budget_lkr: float | None = None
    delivery_city: str | None = None
    delivery_date: date | None = None
    message: str
    bundles: list[GiftBundlePreview] = Field(default_factory=list)
    standalone_options: list[GiftProductPreview] = Field(default_factory=list)
    delivery_checked: bool = False
