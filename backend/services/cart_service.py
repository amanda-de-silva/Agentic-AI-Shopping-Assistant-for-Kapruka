"""Cart mutation logic with atomic increments and server-side validation."""
from __future__ import annotations

from backend.core.exceptions import CartError
from backend.db.models import CartItemRow
from backend.db.repositories.cart_repo import (
    add_cart_item_atomic,
    get_cart_item,
    list_cart_items,
    remove_cart_item,
    upsert_cart_item,
)
from backend.db.repositories.profile_repo import find_profile_by_name
from backend.schemas.cart import CartItem, CartView
from config.settings import get_params_config


async def add_item(
    *,
    session_id: str,
    product_id: str,
    name: str,
    price: float,
    currency: str,
    quantity: int,
    recipient_name: str | None = None,
    gift_message: str | None = None,
) -> CartView:
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")

    max_items = int(get_params_config()["cart"]["max_items"])
    recipient_id = None
    if recipient_name:
        profile = await find_profile_by_name(session_id, recipient_name)
        recipient_id = profile.id if profile else None

    try:
        await add_cart_item_atomic(
            CartItemRow(
                session_id=session_id,
                product_id=product_id,
                name=name,
                price=price,
                currency=currency,
                quantity=quantity,
                recipient_id=recipient_id,
                gift_message=gift_message,
            ),
            max_distinct_items=max_items,
        )
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "maximum number of distinct items" in text.lower():
            raise CartError(f"Your cart is at its {max_items}-item limit.") from exc
        raise

    return await get_cart(session_id=session_id)


async def remove_item(*, session_id: str, product_id: str) -> CartView:
    await remove_cart_item(session_id, product_id)
    return await get_cart(session_id=session_id)


async def update_quantity(*, session_id: str, product_id: str, quantity: int) -> CartView:
    if quantity < 1:
        return await remove_item(session_id=session_id, product_id=product_id)

    existing = await get_cart_item(session_id, product_id)
    if existing is None:
        raise CartError(f"Product '{product_id}' is not in your cart.")
    existing.quantity = quantity
    await upsert_cart_item(existing)
    return await get_cart(session_id=session_id)


async def get_cart(*, session_id: str) -> CartView:
    rows = await list_cart_items(session_id)
    items = [
        CartItem(
            product_id=r.product_id,
            name=r.name,
            price=r.price,
            currency=r.currency,
            quantity=r.quantity,
            gift_message=r.gift_message,
            line_total=round(r.price * r.quantity, 2),
        )
        for r in rows
    ]
    subtotal = round(sum(i.line_total for i in items), 2)
    currency = items[0].currency if items else "LKR"
    return CartView(
        session_id=session_id,
        items=items,
        item_count=sum(i.quantity for i in items),
        subtotal=subtotal,
        currency=currency,
    )