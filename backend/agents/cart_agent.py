"""
Owns internal cart mutations (add/remove/update) against cart_repo.

Kapruka's MCP has no server-side cart -- every mutation here happens against our own
Supabase cart_items table via backend/services/cart_service.py. This module is the
thin agent-facing wrapper that also fetches product details (price/name) from the
Catalog Agent when adding an item, so the cart always stores an accurate price snapshot.
"""
from __future__ import annotations

from backend.agents import catalog_agent
from backend.core.exceptions import CartError
from backend.schemas.cart import CartView
from backend.services import cart_service


async def add_item(
    *, session_id: str, product_id: str, quantity: int = 1, recipient_name: str | None = None,
    gift_message: str | None = None,
) -> CartView:
    product = await catalog_agent.get_product(product_id)
    if product is None:
        raise CartError(f"Product '{product_id}' could not be found.")
    return await cart_service.add_item(
        session_id=session_id,
        product_id=product.product_id,
        name=product.name,
        price=product.price,
        currency=product.currency,
        quantity=quantity,
        recipient_name=recipient_name,
        gift_message=gift_message,
    )


async def remove_item(*, session_id: str, product_id: str) -> CartView:
    return await cart_service.remove_item(session_id=session_id, product_id=product_id)


async def update_quantity(*, session_id: str, product_id: str, quantity: int) -> CartView:
    return await cart_service.update_quantity(session_id=session_id, product_id=product_id, quantity=quantity)


async def view_cart(*, session_id: str) -> CartView:
    return await cart_service.get_cart(session_id=session_id)
