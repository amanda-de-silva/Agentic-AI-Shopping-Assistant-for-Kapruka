"""Checkout and delivery-option endpoints."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header

from backend.api.deps import get_session
from backend.db.models import SessionRow
from backend.db.repositories.cart_repo import list_cart_items
from backend.schemas.order import (
    DeliveryOptionsRequest,
    DeliveryOptionsResponse,
    OrderCreateRequest,
    OrderCreateResponse,
)
from backend.services import (
    checkout_service,
    delivery_service,
)

router = APIRouter(
    tags=["checkout"]
)


@router.post(
    "/checkout/delivery-options",
    response_model=DeliveryOptionsResponse,
)
async def get_delivery_options(
    payload: DeliveryOptionsRequest,
    session: SessionRow = Depends(get_session),
) -> DeliveryOptionsResponse:
    """Find the earliest Kapruka-valid date for the current cart."""

    items = await list_cart_items(
        session.id
    )

    start_date = (
        payload.start_date
        or delivery_service.today_in_delivery_timezone()
    )

    if not items:
        return DeliveryOptionsResponse(
            requested_city=payload.city,
            canonical_city=payload.city,
            earliest_date=None,
            checked_from=start_date,
            checked_until=start_date,
            days_checked=0,
            message="Your cart is empty.",
        )

    result = (
        await delivery_service.find_earliest_delivery_date(
            city=payload.city,
            product_ids=[
                item.product_id
                for item in items
            ],
            start_date=payload.start_date,
        )
    )

    earliest_date = result[
        "earliest_date"
    ]

    message = None

    if earliest_date is None:
        message = (
            "No common delivery date was confirmed "
            f"within the configured search window "
            f"for {result['canonical_city']}."
        )

    return DeliveryOptionsResponse(
        requested_city=result[
            "requested_city"
        ],
        canonical_city=result[
            "canonical_city"
        ],
        earliest_date=earliest_date,
        checked_from=result[
            "checked_from"
        ],
        checked_until=result[
            "checked_until"
        ],
        days_checked=result[
            "days_checked"
        ],
        message=message,
    )


@router.post(
    "/checkout",
    response_model=OrderCreateResponse,
)
async def checkout(
    payload: OrderCreateRequest,
    session: SessionRow = Depends(get_session),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> OrderCreateResponse:

    if not idempotency_key:
        idempotency_key = str(
            uuid4()
        )

    slots = {
        "mode": payload.mode,

        "recipient_name":
            payload.recipient.name,

        "phone":
            payload.recipient.phone,

        "address":
            payload.delivery.address,

        "city":
            payload.delivery.city,

        "delivery_date": (
            payload.delivery.date.isoformat()
            if payload.delivery.date
            else None
        ),

        "sender_name": (
            payload.sender.name
            if payload.sender
            else None
        ),

        "gift_message":
            payload.gift_message,

        "currency":
            payload.currency,
    }

    result = await checkout_service.checkout(
        session_id=session.id,
        slots=slots,
        idempotency_key=idempotency_key,
    )

    return OrderCreateResponse(
        **result
    )