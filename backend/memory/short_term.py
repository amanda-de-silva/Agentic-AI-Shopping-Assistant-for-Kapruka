"""Session-scoped rolling message window with configurable TTL."""
from __future__ import annotations

from backend.db.repositories.message_repo import add_message, get_recent_messages
from config.settings import get_params_config


async def record_turn(session_id: str, role: str, content: str, intent: str | None = None) -> None:
    await add_message(session_id, role, content, intent)


async def get_context(session_id: str) -> str:
    memory_cfg = get_params_config()["memory"]
    messages = await get_recent_messages(
        session_id,
        memory_cfg["short_term_window"],
        ttl_minutes=memory_cfg.get("short_term_ttl_minutes"),
    )
    if not messages:
        return "(no prior messages this session)"
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


async def get_context_as_chat_messages(session_id: str) -> list[dict]:
    memory_cfg = get_params_config()["memory"]
    messages = await get_recent_messages(
        session_id,
        memory_cfg["short_term_window"],
        ttl_minutes=memory_cfg.get("short_term_ttl_minutes"),
    )
    return [{"role": m.role, "content": m.content} for m in messages]