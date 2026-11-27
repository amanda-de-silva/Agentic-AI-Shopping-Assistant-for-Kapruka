"""Persistence for short-term recommendation continuation state."""
from __future__ import annotations

from typing import Any

from backend.core.logging import get_logger
from backend.db.supabase_client import get_supabase, run_sync

TABLE = "sessions"
logger = get_logger(__name__)


async def get_recommendation_state(
    session_id: str,
) -> dict[str, Any] | None:
    """Return persisted recommendation state for one session, if available."""

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select("recommendation_state")
            .eq("id", session_id)
            .limit(1)
            .execute()
        )

    try:
        response = await run_sync(op)
    except Exception as exc:  # noqa: BLE001
        # The recommendation pipeline has an in-memory fallback, so a missing
        # migration or temporary DB issue must not break recommendations.
        logger.warning(
            "Could not read recommendation continuation state: %s",
            exc,
        )
        return None

    if not response.data:
        return None

    state = response.data[0].get("recommendation_state")

    return state if isinstance(state, dict) else None


async def save_recommendation_state(
    session_id: str,
    state: dict[str, Any],
) -> bool:
    """Persist recommendation continuation state for one session."""

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .update({"recommendation_state": state})
            .eq("id", session_id)
            .execute()
        )

    try:
        await run_sync(op)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not persist recommendation continuation state: %s",
            exc,
        )
        return False
