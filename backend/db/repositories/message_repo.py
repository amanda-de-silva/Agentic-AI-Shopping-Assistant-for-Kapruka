"""CRUD + TTL/windowed query for messages."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.db.models import MessageRow
from backend.db.supabase_client import get_supabase, run_sync

TABLE = "messages"


async def add_message(session_id: str, role: str, content: str, intent: str | None = None) -> None:
    payload = {"session_id": session_id, "role": role, "content": content, "intent": intent}

    def op():
        return get_supabase().table(TABLE).insert(payload).execute()

    await run_sync(op)


async def get_recent_messages(
    session_id: str,
    limit: int,
    *,
    ttl_minutes: int | None = None,
) -> list[MessageRow]:
    def op():
        query = get_supabase().table(TABLE).select("*").eq("session_id", session_id)
        if ttl_minutes is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)).isoformat()
            query = query.gte("created_at", cutoff)
        return query.order("created_at", desc=True).limit(limit).execute()

    resp = await run_sync(op)
    rows = [MessageRow(**r) for r in resp.data]
    return list(reversed(rows))


async def count_user_messages(session_id: str) -> int:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select("id", count="exact")
            .eq("session_id", session_id)
            .eq("role", "user")
            .execute()
        )

    resp = await run_sync(op)
    return int(resp.count or 0)
