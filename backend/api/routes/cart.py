"""GET/PATCH /cart plus an isolated all-or-rollback bundle-add endpoint."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agents import cart_agent
from backend.api.deps import get_session
from backend.core.exceptions import CartError
from backend.core.logging import get_logger
from backend.db.models import SessionRow
from backend.schemas.cart import CartView

router = APIRouter(tags=["cart"])
logger = get_logger(__name__)


class CartMutation(BaseModel):
    action: Literal["add", "remove", "update_quantity"]
    product_id: str = Field(min_length=1)
    quantity: int = 1


class CartBatchItem(BaseModel):
    product_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=20)


class CartBatchMutation(BaseModel):
    """A bounded list of products to add as one user-requested bundle action."""

    items: list[CartBatchItem] = Field(min_length=1, max_length=12)


@router.get("/cart", response_model=CartView)
async def get_cart(session: SessionRow = Depends(get_session)) -> CartView:
    return await cart_agent.view_cart(session_id=session.id)


@router.patch("/cart", response_model=CartView)
async def mutate_cart(payload: CartMutation, session: SessionRow = Depends(get_session)) -> CartView:
    if payload.action == "add":
        return await cart_agent.add_item(session_id=session.id, product_id=payload.product_id, quantity=payload.quantity)
    if payload.action == "remove":
        return await cart_agent.remove_item(session_id=session.id, product_id=payload.product_id)
    if payload.action == "update_quantity":
        return await cart_agent.update_quantity(session_id=session.id, product_id=payload.product_id, quantity=payload.quantity)
    raise CartError("Unsupported cart action.")


@router.post("/cart/add-batch", response_model=CartView)
async def add_cart_batch(
    payload: CartBatchMutation,
    session: SessionRow = Depends(get_session),
) -> CartView:
    """Add every line in a gift bundle; restore the previous cart if one fails.

    This endpoint is separate from the existing PATCH /cart contract. Product
    details and prices continue to be resolved by the existing cart agent. Adds
    are deliberately sequential to avoid a burst of MCP product lookups.
    """
    # Collapse duplicate IDs while preserving order so the same product is not
    # looked up or added more than once in a single bundle operation.
    quantities: dict[str, int] = {}
    for item in payload.items:
        product_id = item.product_id.strip()
        if not product_id:
            raise HTTPException(status_code=422, detail="Product ID cannot be blank.")
        quantities[product_id] = quantities.get(product_id, 0) + item.quantity
        if quantities[product_id] > 20:
            raise HTTPException(status_code=422, detail="A product quantity cannot exceed 20.")

    before = await cart_agent.view_cart(session_id=session.id)
    previous_quantities = {
        item.product_id: item.quantity
        for item in before.items
    }
    touched: list[str] = []

    try:
        for product_id, quantity in quantities.items():
            # Track before awaiting: if a downstream call raises after the DB
            # mutation, rollback still knows this line may have changed.
            touched.append(product_id)
            await cart_agent.add_item(
                session_id=session.id,
                product_id=product_id,
                quantity=quantity,
            )

        return await cart_agent.view_cart(session_id=session.id)

    except Exception as exc:  # noqa: BLE001
        rollback_errors: list[str] = []
        for product_id in reversed(list(dict.fromkeys(touched))):
            try:
                if product_id in previous_quantities:
                    await cart_agent.update_quantity(
                        session_id=session.id,
                        product_id=product_id,
                        quantity=previous_quantities[product_id],
                    )
                else:
                    await cart_agent.remove_item(
                        session_id=session.id,
                        product_id=product_id,
                    )
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"{product_id}: {rollback_exc}")

        if rollback_errors:
            logger.error(
                "Gift bundle cart rollback was incomplete",
                extra={
                    "session_id": session.id,
                    "product_ids": list(quantities),
                    "rollback_errors": rollback_errors,
                },
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "The bundle could not be added completely and the cart "
                    "could not be fully restored. Please refresh your cart "
                    "and verify its contents before retrying."
                ),
            ) from exc

        logger.info(
            "Gift bundle add failed; original cart state restored",
            extra={
                "session_id": session.id,
                "product_ids": list(quantities),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "The entire gift bundle could not be added. "
                "Your cart has been restored; please try again."
            ),
        ) from exc
