"""User-owned recipient profile repository."""

from __future__ import annotations

import uuid

from backend.db.models import RecipientProfileRow
from backend.db.supabase_client import (
    get_supabase,
    run_sync,
)


TABLE = "recipient_profiles"

SELECT_COLUMNS = (
    "id,"
    "user_id,"
    "session_id,"
    "name,"
    "relationship,"
    "allergies,"
    "likes,"
    "dislikes,"
    "notes,"
    "created_at,"
    "updated_at"
)


def _normalize_name(
    value: str,
) -> str:
    return " ".join(
        value.strip().split()
    )


def _to_profile(
    row: dict,
) -> RecipientProfileRow:
    return RecipientProfileRow(
        id=(
            str(row["id"])
            if row.get("id")
            else None
        ),
        user_id=str(
            row["user_id"]
        ),
        session_id=(
            str(row["session_id"])
            if row.get("session_id")
            else None
        ),
        name=str(
            row["name"]
        ),
        relationship=row.get(
            "relationship"
        ),
        allergies=list(
            row.get(
                "allergies"
            )
            or []
        ),
        likes=list(
            row.get(
                "likes"
            )
            or []
        ),
        dislikes=list(
            row.get(
                "dislikes"
            )
            or []
        ),
        notes=row.get(
            "notes"
        ),
        created_at=row.get(
            "created_at"
        ),
        updated_at=row.get(
            "updated_at"
        ),
    )


async def list_profiles(
    user_id: str,
) -> list[RecipientProfileRow]:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select(
                SELECT_COLUMNS
            )
            .eq(
                "user_id",
                user_id,
            )
            .order(
                "created_at",
                desc=False,
            )
            .execute()
        )

    resp = await run_sync(
        op
    )

    return [
        _to_profile(row)
        for row in (
            resp.data
            or []
        )
    ]


async def find_profile_by_name(
    user_id: str,
    name: str,
) -> RecipientProfileRow | None:
    """
    Exact normalized name lookup.

    .eq() is intentional; .ilike() would interpret % and _ as
    wildcard characters.
    """

    clean_name = _normalize_name(
        name
    )

    if not clean_name:
        return None

    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select(
                SELECT_COLUMNS
            )
            .eq(
                "user_id",
                user_id,
            )
            .eq(
                "name",
                clean_name,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(
        op
    )

    if not resp.data:
        return None

    return _to_profile(
        resp.data[0]
    )


async def get_profile(
    user_id: str,
    profile_id: str,
) -> RecipientProfileRow | None:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select(
                SELECT_COLUMNS
            )
            .eq(
                "user_id",
                user_id,
            )
            .eq(
                "id",
                profile_id,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(
        op
    )

    if not resp.data:
        return None

    return _to_profile(
        resp.data[0]
    )


async def upsert_profile(
    profile: RecipientProfileRow,
) -> RecipientProfileRow:
    clean_name = _normalize_name(
        profile.name
    )

    if not clean_name:
        raise ValueError(
            "Recipient profile name cannot be empty."
        )

    payload = {
        "user_id": profile.user_id,
        "session_id": profile.session_id,
        "name": clean_name,
        "relationship": (
            profile.relationship
        ),
        "allergies": list(
            profile.allergies
            or []
        ),
        "likes": list(
            profile.likes
            or []
        ),
        "dislikes": list(
            profile.dislikes
            or []
        ),
        "notes": profile.notes,
    }

    existing = (
        await find_profile_by_name(
            user_id=profile.user_id,
            name=clean_name,
        )
    )

    if existing and existing.id:

        def update_op():
            return (
                get_supabase()
                .table(TABLE)
                .update(
                    payload
                )
                .eq(
                    "id",
                    existing.id,
                )
                .eq(
                    "user_id",
                    profile.user_id,
                )
                .select(
                    SELECT_COLUMNS
                )
                .execute()
            )

        resp = await run_sync(
            update_op
        )

    else:
        payload["id"] = str(
            uuid.uuid4()
        )

        def insert_op():
            return (
                get_supabase()
                .table(TABLE)
                .insert(
                    payload
                )
                .select(
                    SELECT_COLUMNS
                )
                .execute()
            )

        resp = await run_sync(
            insert_op
        )

    if not resp.data:
        raise RuntimeError(
            "Recipient profile write returned no row."
        )

    return _to_profile(
        resp.data[0]
    )


async def append_field_value(
    user_id: str,
    session_id: str,
    profile_id: str,
    field: str,
    value: str,
) -> RecipientProfileRow:
    allowed_fields = {
        "allergies",
        "likes",
        "dislikes",
    }

    if field not in allowed_fields:
        raise ValueError(
            f"Unsupported profile field: {field}"
        )

    profile = await get_profile(
        user_id=user_id,
        profile_id=profile_id,
    )

    if profile is None:
        raise ValueError(
            "Recipient profile not found."
        )

    clean_value = value.strip()

    if not clean_value:
        raise ValueError(
            "Profile value cannot be empty."
        )

    current = list(
        getattr(
            profile,
            field,
        )
        or []
    )

    if not any(
        existing.lower()
        == clean_value.lower()
        for existing in current
    ):
        current.append(
            clean_value
        )

    setattr(
        profile,
        field,
        current,
    )

    profile.session_id = (
        session_id
    )

    return await upsert_profile(
        profile
    )


async def delete_profile_for_user(
    *,
    user_id: str,
    profile_id: str,
) -> None:
    """Delete one profile owned by this user."""

    await run_sync(
        lambda: (
            get_supabase()
            .table(TABLE)
            .delete()
            .eq(
                "user_id",
                user_id,
            )
            .eq(
                "id",
                profile_id,
            )
            .execute()
        )
    )


async def delete_profiles_for_user(
    user_id: str,
) -> None:
    """Delete all durable recipient profiles for a user."""

    await run_sync(
        lambda: (
            get_supabase()
            .table(TABLE)
            .delete()
            .eq(
                "user_id",
                user_id,
            )
            .execute()
        )
    )