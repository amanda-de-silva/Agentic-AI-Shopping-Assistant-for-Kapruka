"""Phone-number authentication routes."""
from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Response,
)

from backend.api.deps import (
    SESSION_COOKIE,
    get_session,
)
from backend.core.exceptions import (
    AppError,
)
from backend.db.models import (
    SessionRow,
)
from backend.db.repositories.session_repo import (
    get_or_create_session_for_user,
    revoke_session,
)
from backend.db.repositories.user_repo import (
    get_or_create_user,
    normalize_phone,
)
from backend.schemas.auth import (
    AuthResponse,
    PhoneLoginRequest,
)
from config.settings import (
    get_settings,
)

router = APIRouter(
    tags=["auth"]
)


def _set_session_cookie(
    response: Response,
    session_id: str,
) -> None:
    settings = get_settings()

    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=settings.app_env.lower()
        not in {
            "development",
            "dev",
            "test",
        },
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )

    response.headers[
        "X-Session-Id"
    ] = session_id


@router.post(
    "/auth/login",
    response_model=AuthResponse,
)
async def login(
    payload: PhoneLoginRequest,
    response: Response,
) -> AuthResponse:
    try:
        phone = normalize_phone(
            payload.phone
        )

    except ValueError as exc:
        raise AppError(
            str(exc),
            code="invalid_phone",
            status_code=422,
        ) from exc

    # ------------------------------------------------------------------
    # Permanent identity.
    # ------------------------------------------------------------------
    #
    # +94771925487
    #      ↓
    # users.id = UUID
    #
    user = await get_or_create_user(
        phone
    )

    # ------------------------------------------------------------------
    # Authenticated session.
    # ------------------------------------------------------------------
    #
    # users.id
    #    ↓
    # sessions.user_id
    #
    session = (
        await get_or_create_session_for_user(
            user.id
        )
    )

    _set_session_cookie(
        response,
        session.id,
    )

    return AuthResponse(
        session_id=session.id,
        phone=user.phone,
    )


@router.post(
    "/auth/logout",
)
async def logout(
    response: Response,
    session: SessionRow = Depends(
        get_session
    ),
) -> dict[str, bool]:
    """
    Logout only ends authentication.

    This deliberately does NOT delete:
        - recipient profiles
        - likes/preferences
        - allergies
        - dislikes
        - conversation memory
        - cart
    """

    await revoke_session(
        session.id
    )

    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
    )

    return {
        "ok": True
    }