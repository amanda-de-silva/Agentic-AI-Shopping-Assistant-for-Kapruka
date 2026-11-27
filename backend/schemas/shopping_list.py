"""Schemas for the conversational, item-by-item shopping-list flow."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ShoppingListItemSearchRequest(BaseModel):
    item_query: str = Field(min_length=2, max_length=160)


class ShoppingListProduct(BaseModel):
    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    image_url: str | None = None
    url: str | None = None
    in_stock: bool | None = None


class ShoppingListItemSearchResponse(BaseModel):
    item_query: str
    products: list[ShoppingListProduct] = Field(default_factory=list)
    shown_count: int = 0
    limit: int = 12
    message: str | None = None
