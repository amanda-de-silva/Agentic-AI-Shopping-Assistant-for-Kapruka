"""Checkout and order-tracking API schemas."""
from __future__ import annotations

from datetime import date as Date
from typing import Literal, Optional

from pydantic import BaseModel, Field


CheckoutMode = Literal["gift", "self"]


class RecipientDetails(BaseModel):
    """The person receiving the order."""

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class DeliveryDetails(BaseModel):
    """Delivery data accepted by the local checkout API."""

    address: str = Field(min_length=1)
    city: str = Field(min_length=1)

    # Optional because the backend can automatically determine
    # the earliest Kapruka-valid delivery date.
    date: Optional[Date] = None


class SenderDetails(BaseModel):
    """Optional sender information for gift checkout."""

    name: str = Field(min_length=1)


class OrderCreateRequest(BaseModel):
    """Checkout request for gift/self modes."""

    mode: CheckoutMode = "gift"

    recipient: RecipientDetails

    delivery: DeliveryDetails

    sender: SenderDetails | None = None

    gift_message: str | None = None

    currency: str = Field(
        default="LKR",
        min_length=3,
        max_length=3,
    )


class DeliveryOptionsRequest(BaseModel):
    """Request for automatic earliest delivery-date resolution."""

    city: str = Field(
        min_length=1
    )

    start_date: Optional[Date] = None


class DeliveryOptionsResponse(BaseModel):
    """Earliest common delivery date for the current server-side cart."""

    requested_city: str

    canonical_city: str

    earliest_date: Optional[Date]

    checked_from: Date

    checked_until: Date

    days_checked: int

    message: str | None = None


class OrderCreateResponse(BaseModel):
    order_id: str

    kapruka_order_number: str | None = None

    checkout_url: str

    total: float | None = None

    currency: str = "LKR"


class OrderStatus(BaseModel):
    order_number: str

    status: str

    message: str | None = None

    recipient: str | None = None

    items: list[dict] = Field(
        default_factory=list
    )

    timeline: list[dict] = Field(
        default_factory=list
    )