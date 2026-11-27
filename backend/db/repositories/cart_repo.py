"""CRUD for cart_items, keyed by session_id."""
from __future__ import annotations

from backend.db.models import CartItemRow
from backend.db.supabase_client import get_supabase, run_sync

TABLE = "cart_items"


async def list_cart_items(
    session_id: str,
) -> list[CartItemRow]:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select("*")
            .eq("session_id", session_id)
            .execute()
        )

    resp = await run_sync(op)

    return [
        CartItemRow(**r)
        for r in resp.data
    ]


async def get_cart_item(
    session_id: str,
    product_id: str,
) -> CartItemRow | None:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .select("*")
            .eq("session_id", session_id)
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return CartItemRow(**resp.data[0])


async def add_cart_item_atomic(
    item: CartItemRow,
    *,
    max_distinct_items: int,
) -> CartItemRow:
    """Atomically add quantity server-side, avoiding read/modify/write races."""
    args = {
        "p_session_id": item.session_id,
        "p_product_id": item.product_id,
        "p_name": item.name,
        "p_price": item.price,
        "p_currency": item.currency,
        "p_quantity": item.quantity,
        "p_recipient_id": item.recipient_id,
        "p_gift_message": item.gift_message,
        "p_max_distinct_items": max_distinct_items,
    }

    def op():
        return (
            get_supabase()
            .rpc(
                "add_cart_item_atomic",
                args,
            )
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        raise RuntimeError(
            "Cart mutation returned no row"
        )

    row = (
        resp.data[0]
        if isinstance(resp.data, list)
        else resp.data
    )

    return CartItemRow(**row)


async def upsert_cart_item(
    item: CartItemRow,
) -> CartItemRow:
    payload = item.model_dump(
        exclude={
            "id",
            "added_at",
        },
        exclude_none=True,
    )

    def op():
        supabase = get_supabase()

        if item.id:
            return (
                supabase
                .table(TABLE)
                .update(payload)
                .eq("id", item.id)
                .select("*")
                .execute()
            )

        return (
            supabase
            .table(TABLE)
            .upsert(
                payload,
                on_conflict="session_id,product_id",
            )
            .select("*")
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        raise RuntimeError(
            "Cart upsert returned no row."
        )

    row = (
        resp.data[0]
        if isinstance(resp.data, list)
        else resp.data
    )

    return CartItemRow(**row)


async def remove_cart_item(
    session_id: str,
    product_id: str,
) -> None:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .delete()
            .eq("session_id", session_id)
            .eq("product_id", product_id)
            .execute()
        )

    await run_sync(op)


async def clear_cart(
    session_id: str,
) -> None:
    def op():
        return (
            get_supabase()
            .table(TABLE)
            .delete()
            .eq("session_id", session_id)
            .execute()
        )

    await run_sync(op)