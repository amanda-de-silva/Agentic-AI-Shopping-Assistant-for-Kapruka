"""
GET /delivery/check -- delivery feasibility check for a city/date/product.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.services import delivery_service

router = APIRouter(tags=["delivery"])


@router.get("/delivery/check")
async def check_delivery(city: str, delivery_date: str, product_id: str | None = None) -> dict:
    return await delivery_service.check(city=city, delivery_date=delivery_date, product_id=product_id)
