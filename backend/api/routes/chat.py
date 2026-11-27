"""
POST /chat -- non-streaming chat endpoint. Delegates to agents.orchestrator.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.agents.orchestrator import handle_turn
from backend.api.deps import get_session
from backend.db.models import SessionRow
from backend.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, session: SessionRow = Depends(get_session)) -> ChatResponse:
    return await handle_turn(session_id=session.id, user_message=payload.message)
