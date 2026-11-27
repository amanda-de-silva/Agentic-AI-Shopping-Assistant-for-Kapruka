"""Typed UI action events emitted deterministically by the backend."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

UIActionType = Literal[
    "cart_updated",
    "navigate_checkout",
    "navigate_cart",
    "order_status",
    "memory_recalled",
    "products_shown",
]


class UIAction(BaseModel):
    type: UIActionType
    payload: dict[str, Any] = Field(default_factory=dict)