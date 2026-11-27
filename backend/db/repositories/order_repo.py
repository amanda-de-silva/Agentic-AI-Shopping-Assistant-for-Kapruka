"""CRUD for orders, checkout attempts, and order_status_cache."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.db.models import (
    CheckoutAttemptRow,
    OrderRow,
    OrderStatusCacheRow,
)
from backend.db.supabase_client import (
    get_supabase,
    run_sync,
)

ORDERS_TABLE = "orders"
CACHE_TABLE = "order_status_cache"
ATTEMPTS_TABLE = "checkout_attempts"


def _first_row(resp):
    if not resp.data:
        return None

    return (
        resp.data[0]
        if isinstance(resp.data, list)
        else resp.data
    )


async def create_order_record(
    order: OrderRow,
) -> OrderRow:
    payload = order.model_dump(
        exclude={
            "id",
            "created_at",
        },
        exclude_none=True,
    )

    def op():
        return (
            get_supabase()
            .table(ORDERS_TABLE)
            .insert(payload)
            .select("*")
            .execute()
        )

    resp = await run_sync(op)
    row = _first_row(resp)

    if row is None:
        raise RuntimeError(
            "Order creation returned no row."
        )

    return OrderRow(**row)


async def get_order_for_session(
    session_id: str,
    order_number: str,
) -> OrderRow | None:
    def op():
        return (
            get_supabase()
            .table(ORDERS_TABLE)
            .select("*")
            .eq("session_id", session_id)
            .eq(
                "kapruka_order_number",
                order_number,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return OrderRow(**resp.data[0])


async def get_cached_order_status(
    order_number: str,
) -> OrderStatusCacheRow | None:
    def op():
        return (
            get_supabase()
            .table(CACHE_TABLE)
            .select("*")
            .eq(
                "order_number",
                order_number,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return OrderStatusCacheRow(
        **resp.data[0]
    )


async def upsert_order_status_cache(
    order_number: str,
    status: str,
    raw_payload: dict,
) -> None:
    payload = {
        "order_number": order_number,
        "last_status": status,
        "last_synced_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "raw_payload": raw_payload,
    }

    def op():
        return (
            get_supabase()
            .table(CACHE_TABLE)
            .upsert(
                payload,
                on_conflict="order_number",
            )
            .execute()
        )

    await run_sync(op)


async def get_checkout_attempt(
    session_id: str,
    idempotency_key: str,
) -> CheckoutAttemptRow | None:
    def op():
        return (
            get_supabase()
            .table(ATTEMPTS_TABLE)
            .select("*")
            .eq(
                "session_id",
                session_id,
            )
            .eq(
                "idempotency_key",
                idempotency_key,
            )
            .limit(1)
            .execute()
        )

    resp = await run_sync(op)

    if not resp.data:
        return None

    return CheckoutAttemptRow(
        **resp.data[0]
    )


async def create_checkout_attempt(
    session_id: str,
    idempotency_key: str,
) -> CheckoutAttemptRow:
    payload = {
        "session_id": session_id,
        "idempotency_key": idempotency_key,
        "status": "pending",
    }

    def op():
        return (
            get_supabase()
            .table(ATTEMPTS_TABLE)
            .insert(payload)
            .select("*")
            .execute()
        )

    resp = await run_sync(op)
    row = _first_row(resp)

    if row is None:
        raise RuntimeError(
            "Checkout attempt creation returned no row."
        )

    return CheckoutAttemptRow(**row)


async def update_checkout_attempt(
    attempt_id: str,
    *,
    status: str,
    order_id: str | None = None,
    external_order_number: str | None = None,
    checkout_url: str | None = None,
    total: float | None = None,
    currency: str | None = None,
    error_message: str | None = None,
) -> CheckoutAttemptRow:
    payload: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "order_id": order_id,
        "external_order_number":
            external_order_number,
        "checkout_url": checkout_url,
        "total": total,
        "currency": currency,
        "error_message": error_message,
    }

    def op():
        return (
            get_supabase()
            .table(ATTEMPTS_TABLE)
            .update(payload)
            .eq("id", attempt_id)
            .select("*")
            .execute()
        )

    resp = await run_sync(op)
    row = _first_row(resp)

    if row is None:
        raise RuntimeError(
            f"Checkout attempt {attempt_id} "
            "was not found after update."
        )

    return CheckoutAttemptRow(**row)