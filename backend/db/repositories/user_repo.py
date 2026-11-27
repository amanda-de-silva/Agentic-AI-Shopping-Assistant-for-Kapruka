"""Persistent user identity keyed by canonical phone number."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.db.models import UserRow
from backend.db.supabase_client import (
    get_supabase,
    run_sync,
)

TABLE = "users"


def normalize_phone(phone: str) -> str:
    """
    Canonical format:

        0771925487
        +94771925487
        94771925487

    all become:

        +94771925487
    """

    value = "".join(
        ch
        for ch in phone.strip()
        if ch.isdigit() or ch == "+"
    )

    if value.startswith("00"):
        value = "+" + value[2:]

    if value.startswith("+"):
        digits = value[1:]
    else:
        digits = value

    if not digits.isdigit():
        raise ValueError(
            "Invalid phone number."
        )

    # Sri Lankan local format.
    if (
        len(digits) == 10
        and digits.startswith("0")
    ):
        return "+94" + digits[1:]

    # Sri Lankan international format without +.
    if (
        len(digits) == 11
        and digits.startswith("94")
    ):
        return "+" + digits

    # Already E.164-style.
    if 8 <= len(digits) <= 15:
        return "+" + digits

    raise ValueError(
        "Invalid phone number."
    )


def _to_user(row: dict) -> UserRow:
    return UserRow(
        id=str(row["id"]),
        phone=str(row["phone"]),
        created_at=row.get(
            "created_at"
        ),
        last_active_at=row.get(
            "last_active_at"
        ),
    )


async def get_user_by_phone(
    phone: str,
) -> UserRow | None:
    canonical_phone = normalize_phone(
        phone
    )

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select(
                "id,phone,created_at,last_active_at"
            )
            .eq(
                "phone",
                canonical_phone,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return _to_user(
        resp.data[0]
    )


async def get_user_by_id(
    user_id: str,
) -> UserRow | None:
    try:
        uuid.UUID(user_id)
    except (
        ValueError,
        AttributeError,
    ):
        return None

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select(
                "id,phone,created_at,last_active_at"
            )
            .eq(
                "id",
                user_id,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return _to_user(
        resp.data[0]
    )


async def get_or_create_user(
    phone: str,
) -> UserRow:
    canonical_phone = normalize_phone(
        phone
    )

    existing = await get_user_by_phone(
        canonical_phone
    )

    if existing:
        return await touch_user(
            existing.id
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    payload = {
        "id": str(uuid.uuid4()),
        "phone": canonical_phone,
        "created_at": now,
        "last_active_at": now,
    }

    def insert_op():
        return (
            get_supabase()
            .table(TABLE)
            .insert(payload)
            .select(
                "id,phone,created_at,last_active_at"
            )
            .execute()
        )

    try:
        resp = await run_sync(
            insert_op
        )

        if not resp.data:
            raise RuntimeError(
                "User creation returned no row."
            )

        return _to_user(
            resp.data[0]
        )

    except Exception:
        # Protect against a simultaneous first-login race.
        existing = await get_user_by_phone(
            canonical_phone
        )

        if existing:
            return existing

        raise


async def touch_user(
    user_id: str,
) -> UserRow:
    now = datetime.now(
        timezone.utc
    ).isoformat()

    def op():
        return (
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
                user_id,
            )
            .select(
                "id,phone,created_at,last_active_at"
            )
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        raise RuntimeError(
            f"User {user_id} was not found."
        )

    return _to_user(
        resp.data[0]
    )