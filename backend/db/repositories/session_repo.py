"""Authenticated session repository."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.db.models import SessionRow
from backend.db.supabase_client import (
    get_supabase,
    run_sync,
)

TABLE = "sessions"


def _valid_uuid(
    value: str | None,
) -> bool:
    if not value:
        return False

    try:
        uuid.UUID(value)
        return True
    except (
        ValueError,
        AttributeError,
    ):
        return False


async def get_or_create_session_for_user(
    user_id: str,
) -> SessionRow:
    if not _valid_uuid(user_id):
        raise ValueError(
            "Invalid user UUID."
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    def find_op():
        return (
            get_supabase()
            .table(TABLE)
            .select("*")
            .eq(
                "user_id",
                user_id,
            )
            .order(
                "last_active_at",
                desc=True,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(
        find_op
    )

    if resp.data:
        row = resp.data[0]

        def reactivate_op():
            return (
                get_supabase()
                .table(TABLE)
                .update(
                    {
                        "last_active_at":
                            now,
                        "logged_out_at":
                            None,
                    }
                )
                .eq(
                    "id",
                    row["id"],
                )
                .execute()
            )

        await run_sync(
            reactivate_op
        )

        row[
            "last_active_at"
        ] = now

        row[
            "logged_out_at"
        ] = None

        return SessionRow(
            **row
        )

    session_id = str(
        uuid.uuid4()
    )

    payload = {
        "id": session_id,
        "user_id": user_id,
        "created_at": now,
        "last_active_at": now,
        "logged_out_at": None,
        "channel": "web",
    }

    def insert_op():
        return (
            get_supabase()
            .table(TABLE)
            .insert(payload)
            .select("*")
            .execute()
        )

    resp = await run_sync(
        insert_op
    )

    if not resp.data:
        raise RuntimeError(
            "Session creation returned no row."
        )

    return SessionRow(
        **resp.data[0]
    )


async def get_authenticated_session(
    session_id: str | None,
) -> SessionRow | None:
    if not _valid_uuid(
        session_id
    ):
        return None

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select("*")
            .eq(
                "id",
                session_id,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    row = resp.data[0]

    if not row.get(
        "user_id"
    ):
        return None

    if row.get(
        "logged_out_at"
    ) is not None:
        return None

    now = datetime.now(
        timezone.utc
    ).isoformat()

    await run_sync(
        lambda: (
            get_supabase()
            .table(TABLE)
            .update(
                {
                    "last_active_at":
                        now
                }
            )
            .eq(
                "id",
                session_id,
            )
            .execute()
        )
    )

    row[
        "last_active_at"
    ] = now

    return SessionRow(
        **row
    )


async def revoke_session(
    session_id: str,
) -> None:
    now = datetime.now(
        timezone.utc
    ).isoformat()

    await run_sync(
        lambda: (
            get_supabase()
            .table(TABLE)
            .update(
                {
                    "logged_out_at":
                        now
                }
            )
            .eq(
                "id",
                session_id,
            )
            .execute()
        )
    )