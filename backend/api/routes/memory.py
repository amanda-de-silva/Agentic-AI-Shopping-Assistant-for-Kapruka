"""User-owned recipient profile CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_session
from backend.core.exceptions import ProfileNotFoundError
from backend.db.models import (
    RecipientProfileRow,
    SessionRow,
)
from backend.db.repositories.profile_repo import (
    get_profile,
    list_profiles,
    upsert_profile,
)
from backend.schemas.profile import (
    RecipientProfileCreate,
    RecipientProfileRead,
    RecipientProfileUpdate,
)


router = APIRouter(
    tags=["recipients"]
)


@router.get(
    "/recipients",
    response_model=list[RecipientProfileRead],
)
async def list_recipients(
    session: SessionRow = Depends(get_session),
):
    if not session.user_id:
        return []

    return await list_profiles(
        session.user_id
    )


@router.post(
    "/recipients",
    response_model=RecipientProfileRead,
)
async def create_recipient(
    payload: RecipientProfileCreate,
    session: SessionRow = Depends(get_session),
):
    if not session.user_id:
        raise ValueError(
            "Authenticated session has no user_id."
        )

    row = RecipientProfileRow(
        user_id=session.user_id,
        session_id=session.id,
        **payload.model_dump(),
    )

    return await upsert_profile(row)


@router.patch(
    "/recipients/{profile_id}",
    response_model=RecipientProfileRead,
)
async def update_recipient(
    profile_id: str,
    payload: RecipientProfileUpdate,
    session: SessionRow = Depends(get_session),
):
    if not session.user_id:
        raise ValueError(
            "Authenticated session has no user_id."
        )

    existing = await get_profile(
        user_id=session.user_id,
        profile_id=profile_id,
    )

    if existing is None:
        raise ProfileNotFoundError(
            profile_id
        )

    for key, value in (
        payload.model_dump(
            exclude_none=True
        ).items()
    ):
        setattr(
            existing,
            key,
            value,
        )

    existing.session_id = session.id

    return await upsert_profile(
        existing
    )