"""Session-authorized order tracking endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_session
from backend.core.exceptions import AppError
from backend.db.models import SessionRow
from backend.db.repositories.order_repo import get_order_for_session
from backend.schemas.order import OrderStatus
from backend.services import order_tracking_service

router = APIRouter(tags=["orders"])


@router.get("/orders/{order_number}", response_model=OrderStatus)
async def get_order(
    order_number: str,
    refresh: bool = False,
    session: SessionRow = Depends(get_session),
) -> OrderStatus:
    order = await get_order_for_session(session.id, order_number)
    if order is None:
        raise AppError("Order not found.", code="order_not_found", status_code=404)
    return await order_tracking_service.get_status(order_number, force_refresh=refresh)