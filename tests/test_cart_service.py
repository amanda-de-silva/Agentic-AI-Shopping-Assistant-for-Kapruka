"""
Tests for cart add/remove/update and max_items/price-lock validation.

These require a live Supabase connection (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY) --
skipped otherwise. Point them at a disposable test project, not production.
"""
from __future__ import annotations

import uuid

import pytest

from backend.core.exceptions import CartError
from backend.services import cart_service
from config.settings import get_settings

pytestmark = pytest.mark.skipif(
    not get_settings().supabase_url, reason="SUPABASE_URL not set"
)


async def test_add_and_view_cart():
    session_id = str(uuid.uuid4())
    await cart_service.add_item(
        session_id=session_id, product_id="P1", name="Test Cake", price=1500, currency="LKR", quantity=2
    )
    view = await cart_service.get_cart(session_id=session_id)
    assert view.item_count == 2
    assert view.subtotal == 3000


async def test_update_quantity_to_zero_removes_item():
    session_id = str(uuid.uuid4())
    await cart_service.add_item(
        session_id=session_id, product_id="P2", name="Test Flowers", price=2000, currency="LKR", quantity=1
    )
    view = await cart_service.update_quantity(session_id=session_id, product_id="P2", quantity=0)
    assert view.item_count == 0


async def test_max_items_enforced():
    session_id = str(uuid.uuid4())
    from config.settings import get_params_config

    max_items = get_params_config()["cart"]["max_items"]
    for i in range(max_items):
        await cart_service.add_item(
            session_id=session_id, product_id=f"P{i}", name=f"Item {i}", price=100, currency="LKR", quantity=1
        )
    with pytest.raises(CartError):
        await cart_service.add_item(
            session_id=session_id, product_id="OVER_LIMIT", name="One too many", price=100, currency="LKR", quantity=1
        )
