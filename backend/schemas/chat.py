"""Chat API schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.schemas.ui_actions import UIAction


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    ui_actions: list[UIAction] = Field(default_factory=list)