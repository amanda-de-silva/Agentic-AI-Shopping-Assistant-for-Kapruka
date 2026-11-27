"""RecipientProfile create/update/read schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RecipientProfileCreate(BaseModel):
    name: str
    relationship: str | None = None
    allergies: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    notes: str | None = None


class RecipientProfileUpdate(BaseModel):
    relationship: str | None = None
    allergies: list[str] | None = None
    likes: list[str] | None = None
    dislikes: list[str] | None = None
    notes: str | None = None


class RecipientProfileRead(BaseModel):
    id: str
    name: str
    relationship: str | None = None
    allergies: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    notes: str | None = None
