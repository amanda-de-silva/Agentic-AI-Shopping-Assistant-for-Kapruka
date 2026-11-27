"""CartItem / CartView schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    quantity: int
    recipient_name: str | None = None
    gift_message: str | None = None
    line_total: float


class CartView(BaseModel):
    session_id: str
    items: list[CartItem] = Field(default_factory=list)
    item_count: int
    subtotal: float
    currency: str = "LKR"