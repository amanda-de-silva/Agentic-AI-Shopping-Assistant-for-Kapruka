"""Pydantic models for the Supabase schema."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserRow(BaseModel):
    id: str
    phone: str
    created_at: datetime | None = None
    last_active_at: datetime | None = None


class SessionRow(BaseModel):
    id: str
    user_id: str | None = None
    created_at: datetime | None = None
    last_active_at: datetime | None = None
    logged_out_at: datetime | None = None
    channel: str = "web"


class MessageRow(BaseModel):
    id: str | None = None
    session_id: str
    role: str
    content: str
    intent: str | None = None
    created_at: datetime | None = None


class RecipientProfileRow(BaseModel):
    """
    Matches the current recipient_profiles table:

        id
        user_id
        session_id
        name
        relationship
        allergies
        likes
        dislikes
        notes
        created_at
        updated_at
    """

    id: str | None = None
    user_id: str
    session_id: str | None = None

    name: str
    relationship: str | None = None

    allergies: list[str] = Field(
        default_factory=list
    )

    likes: list[str] = Field(
        default_factory=list
    )

    dislikes: list[str] = Field(
        default_factory=list
    )

    notes: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class CartItemRow(BaseModel):
    id: str | None = None
    session_id: str
    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    quantity: int = 1
    recipient_id: str | None = None
    gift_message: str | None = None
    added_at: datetime | None = None


class OrderRow(BaseModel):
    id: str | None = None
    session_id: str
    kapruka_order_number: str | None = None
    status: str = "pending"
    checkout_url: str | None = None
    total: float | None = None
    currency: str = "LKR"
    created_at: datetime | None = None


class CheckoutAttemptRow(BaseModel):
    id: str
    session_id: str
    idempotency_key: str
    status: str = "pending"
    order_id: str | None = None
    external_order_number: str | None = None
    checkout_url: str | None = None
    total: float | None = None
    currency: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderStatusCacheRow(BaseModel):
    order_number: str
    last_status: str | None = None
    last_synced_at: datetime | None = None
    raw_payload: dict[str, Any] | None = None