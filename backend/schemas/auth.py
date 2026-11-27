"""Authentication schemas for phone-number sign-in."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PhoneLoginRequest(BaseModel):
    phone: str = Field(
        min_length=8,
        max_length=20,
    )


class AuthResponse(BaseModel):
    session_id: str
    phone: str