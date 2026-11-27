"""Item-by-item shopping-list search API."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_session
from backend.db.models import SessionRow
from backend.schemas.shopping_list import (
    ShoppingListItemSearchRequest,
    ShoppingListItemSearchResponse,
)
from backend.services.shopping_list_service import search_shopping_list_item

router = APIRouter(tags=["shopping-list"])


@router.post(
    "/shopping-list/search",
    response_model=ShoppingListItemSearchResponse,
)
async def search_one_shopping_list_item(
    payload: ShoppingListItemSearchRequest,
    _session: SessionRow = Depends(get_session),
) -> ShoppingListItemSearchResponse:
    """Search one list entry only; cart mutation is a separate user action."""
    return await search_shopping_list_item(item_query=payload.item_query)
