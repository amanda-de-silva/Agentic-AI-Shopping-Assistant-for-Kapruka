"""
Pydantic models mirroring the live Kapruka MCP tool input/output contracts.

The create-order models are intentionally strict because the live
``kapruka_create_order`` validator is the final contract for checkout.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchProductsInput(BaseModel):
    q: str | None = None
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    in_stock_only: bool | None = None
    sort: Literal["relevance", "price_asc", "price_desc", "newest"] | None = None
    limit: int | None = Field(default=20, le=60)
    cursor: str | None = None
    currency: str | None = "LKR"


class GetProductInput(BaseModel):
    product_id: str
    currency: str | None = "LKR"
    # Optional to preserve the existing Markdown behavior for current callers.
    # The product-details endpoint explicitly requests JSON so it can receive
    # the complete structured description when Kapruka provides it.
    response_format: Literal["markdown", "json"] | None = None


class ListCategoriesInput(BaseModel):
    depth: int | None = 1


class ListDeliveryCitiesInput(BaseModel):
    query: str
    limit: int | None = Field(default=10, le=50)


class CheckDeliveryInput(BaseModel):
    city: str
    delivery_date: str
    product_id: str | None = None


class CartLineInput(BaseModel):
    product_id: str
    quantity: int = Field(ge=1)


class RecipientInput(BaseModel):
    """
    Live Kapruka recipient schema.

    The live MCP validation error confirmed that recipient accepts
    ``name`` and ``phone`` but rejects ``address`` and ``city``.
    """

    name: str
    phone: str


class DeliveryInput(BaseModel):
    """
    Live Kapruka delivery schema.

    The live MCP validation error confirmed the field is ``date``, not
    ``delivery_date``, and that address and city are required here.
    """

    address: str
    city: str
    date: str


class SenderInput(BaseModel):
    """
    Live Kapruka sender schema.

    The live validator requires ``name`` and rejects phone/email fields.
    """

    name: str


class CreateOrderInput(BaseModel):
    """Input contract for ``kapruka_create_order``."""

    cart: list[CartLineInput]
    recipient: RecipientInput
    delivery: DeliveryInput
    sender: SenderInput | None = None
    gift_message: str | None = None
    currency: str | None = "LKR"


class TrackOrderInput(BaseModel):
    order_number: str


class MCPToolResult(BaseModel):
    tool_name: str
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: float | None = None