"""Read-only Gift Composer API."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_session
from backend.db.models import SessionRow
from backend.schemas.gift_composer import (
    GiftComposerRequest,
    GiftComposerResponse,
)
from backend.services.gift_composer_service import compose_gift_preview

router = APIRouter(tags=["gift-composer"])


@router.post(
    "/gift-composer/preview",
    response_model=GiftComposerResponse,
)
async def preview_gift_composer(
    payload: GiftComposerRequest,
    session: SessionRow = Depends(get_session),
) -> GiftComposerResponse:
    """Build candidate gift bundles without modifying the cart or creating an order."""
    return await compose_gift_preview(
        user_id=session.user_id or "",
        session_id=session.id,
        request=payload,
    )
