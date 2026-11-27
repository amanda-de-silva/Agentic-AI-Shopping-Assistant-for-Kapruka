"""FastAPI authentication dependencies."""
from __future__ import annotations

from fastapi import (
    Cookie,
    Header,
    Response,
)

from backend.core.exceptions import (
    AppError,
)
from backend.db.models import (
    SessionRow,
)
from backend.db.repositories.session_repo import (
    get_authenticated_session,
)
from config.settings import (
    get_settings,
)

SESSION_COOKIE = (
    "kapruka_session_id"
)


async def get_session(
    response: Response,
    x_session_id: str | None = Header(
        default=None
    ),
    cookie_session_id: str | None = Cookie(
        default=None,
        alias=SESSION_COOKIE,
    ),
) -> SessionRow:
    """
    Resolve and validate the current authenticated session.

    The HttpOnly cookie is preferred.
    X-Session-Id remains supported for API clients.

    A valid session must have:
        - valid session UUID
        - user_id
        - no logged_out_at
    """

    session_id = (
        cookie_session_id
        or x_session_id
    )

    session = (
        await get_authenticated_session(
            session_id
        )
    )

    if session is None:
        raise AppError(
            "A phone-number login is required.",
            code="authentication_required",
            status_code=401,
        )

    if not session.user_id:
        raise AppError(
            "This session is not linked to a user.",
            code="user_identity_missing",
            status_code=401,
        )

    settings = get_settings()

    response.headers[
        "X-Session-Id"
    ] = session.id

    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.id,
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

    return session