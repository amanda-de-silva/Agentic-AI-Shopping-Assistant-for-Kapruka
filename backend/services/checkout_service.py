"""Checkout workflow with automatic delivery-date resolution and idempotency."""
from __future__ import annotations

from datetime import date
from typing import Any

from backend.core.exceptions import (
    CheckoutError,
    MCPToolError,
)
from backend.core.logging import get_logger
from backend.db.models import OrderRow
from backend.db.repositories.cart_repo import (
    clear_cart,
    list_cart_items,
)
from backend.db.repositories.order_repo import (
    create_checkout_attempt,
    create_order_record,
    get_checkout_attempt,
    update_checkout_attempt,
)
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import (
    extract_markdown_result,
    parse_create_order_markdown,
)
from backend.mcp.tool_schemas import (
    CartLineInput,
    CreateOrderInput,
    DeliveryInput,
    RecipientInput,
    SenderInput,
)
from backend.services import delivery_service

logger = get_logger(__name__)


def _required_slot(
    slots: dict[str, Any],
    key: str,
    label: str,
) -> str:
    value = slots.get(key)

    if value is None or not str(value).strip():
        raise CheckoutError(
            f"{label} is required for checkout.",
            status_code=422,
            code="checkout_field_required",
        )

    return str(value).strip()


async def checkout(
    *,
    session_id: str,
    slots: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:

    if not idempotency_key.strip():
        raise CheckoutError(
            "An idempotency key is required for checkout."
        )

    # --------------------------------------------------------------
    # Resolve idempotency attempt
    # --------------------------------------------------------------
    existing_attempt = await get_checkout_attempt(
        session_id,
        idempotency_key,
    )

    if existing_attempt:

        if (
            existing_attempt.status == "succeeded"
            and existing_attempt.order_id
            and existing_attempt.checkout_url
        ):
            return {
                "order_id":
                    existing_attempt.order_id,

                "kapruka_order_number":
                    existing_attempt.external_order_number,

                "checkout_url":
                    existing_attempt.checkout_url,

                "total":
                    existing_attempt.total,

                "currency": (
                    existing_attempt.currency
                    or slots.get("currency")
                    or "LKR"
                ),
            }

        if existing_attempt.status in {
            "pending",
            "uncertain",
        }:
            raise CheckoutError(
                "This checkout attempt is already in progress "
                "or has an uncertain external result. "
                "Please do not create another checkout with a "
                "new key; the existing attempt must be resolved.",
                status_code=409,
                code="checkout_attempt_in_progress",
            )

        attempt = existing_attempt

    else:

        try:
            attempt = await create_checkout_attempt(
                session_id,
                idempotency_key,
            )

        except Exception:

            attempt = await get_checkout_attempt(
                session_id,
                idempotency_key,
            )

            if attempt is None:
                raise

            if attempt.status != "failed":
                raise CheckoutError(
                    "This checkout attempt is already in progress. "
                    "Please wait for it to finish.",
                    status_code=409,
                    code="checkout_attempt_in_progress",
                )

    # --------------------------------------------------------------
    # Authoritative server-side cart
    # --------------------------------------------------------------
    items = await list_cart_items(
        session_id
    )

    if not items:
        await update_checkout_attempt(
            attempt.id,
            status="failed",
            error_message="Cart is empty",
        )

        raise CheckoutError(
            "Your cart is empty -- add something before checking out."
        )

    # --------------------------------------------------------------
    # Checkout mode
    # --------------------------------------------------------------
    mode = str(
        slots.get("mode")
        or "gift"
    ).strip().lower()

    if mode not in {
        "gift",
        "self",
    }:
        await update_checkout_attempt(
            attempt.id,
            status="failed",
            error_message=(
                f"Unsupported checkout mode: {mode}"
            ),
        )

        raise CheckoutError(
            "Please choose either 'It's a gift' or 'It's for me'.",
            status_code=422,
            code="unsupported_checkout_mode",
        )

    recipient_name = _required_slot(
        slots,
        "recipient_name",
        (
            "Recipient name"
            if mode == "gift"
            else "Your name"
        ),
    )

    recipient_phone = _required_slot(
        slots,
        "phone",
        (
            "Recipient phone number"
            if mode == "gift"
            else "Your phone number"
        ),
    )

    delivery_address = _required_slot(
        slots,
        "address",
        "Delivery address",
    )

    delivery_city = _required_slot(
        slots,
        "city",
        "Delivery city",
    )

    # --------------------------------------------------------------
    # Delivery date
    #
    # If the browser supplies a date, validate it.
    # If not, automatically resolve the earliest common date.
    # --------------------------------------------------------------
    raw_requested_date = slots.get(
        "delivery_date"
    )

    requested_date: date | None = None

    if raw_requested_date:

        try:
            requested_date = date.fromisoformat(
                str(raw_requested_date)
            )

        except ValueError as exc:

            await update_checkout_attempt(
                attempt.id,
                status="failed",
                error_message=(
                    f"Invalid delivery date: "
                    f"{raw_requested_date}"
                ),
            )

            raise CheckoutError(
                "Delivery date must use YYYY-MM-DD format.",
                status_code=422,
                code="invalid_delivery_date",
            ) from exc

    try:

        if requested_date is None:

            automatic = (
                await delivery_service.find_earliest_delivery_date(
                    city=delivery_city,
                    product_ids=[
                        item.product_id
                        for item in items
                    ],
                )
            )

            requested_date = automatic[
                "earliest_date"
            ]

            if requested_date is None:
                raise CheckoutError(
                    f"No delivery date could be confirmed "
                    f"for {automatic['canonical_city']} "
                    "within the configured search window.",
                    status_code=422,
                    code="delivery_date_not_found",
                )

        # Always revalidate the final selected date at checkout time.
        # This prevents a stale frontend date from bypassing live Kapruka
        # delivery rules.
        canonical_city = (
            await delivery_service.validate_cart_delivery(
                city=delivery_city,
                delivery_date=requested_date,
                product_ids=[
                    item.product_id
                    for item in items
                ],
            )
        )

    except CheckoutError as exc:

        await update_checkout_attempt(
            attempt.id,
            status="failed",
            error_message=exc.message,
        )

        raise

    # --------------------------------------------------------------
    # Live Kapruka create-order payload
    # --------------------------------------------------------------
    recipient = RecipientInput(
        name=recipient_name,
        phone=recipient_phone,
    )

    delivery = DeliveryInput(
        address=delivery_address,
        city=canonical_city,
        date=requested_date.isoformat(),
    )

    # Kapruka's live create-order schema requires sender.name even for
    # a self order. The frontend does not need to expose a separate sender
    # field in self mode; internally, the buyer becomes the sender.
    sender: SenderInput
    gift_message = None

    if mode == "gift":

        sender_name = slots.get(
            "sender_name"
        )

        if (
            sender_name
            and str(sender_name).strip()
        ):
            sender = SenderInput(
                name=str(
                    sender_name
                ).strip()
            )
        else:
            # Anonymous gift sender. Kapruka still requires sender.name.
            sender = SenderInput(
                name="Anonymous"
            )

        raw_message = slots.get(
            "gift_message"
        )

        if (
            raw_message is not None
            and str(
                raw_message
            ).strip()
        ):
            gift_message = str(
                raw_message
            ).strip()

    else:
        # "It's for me":
        # The same person is both recipient and sender from Kapruka's
        # create_order perspective.
        sender = SenderInput(
            name=recipient_name
        )

    cart_lines = [
        CartLineInput(
            product_id=item.product_id,
            quantity=item.quantity,
        )
        for item in items
    ]

    currency = (
        slots.get("currency")
        or items[0].currency
        or "LKR"
    ).upper()

    # --------------------------------------------------------------
    # kapruka_create_order
    # --------------------------------------------------------------
    client = get_mcp_client()

    try:

        result = await client.create_order(
            CreateOrderInput(
                cart=cart_lines,
                recipient=recipient,
                delivery=delivery,
                sender=sender,
                gift_message=gift_message,
                currency=currency,
            )
        )

    except MCPToolError as exc:

        detail = exc.detail

        error_code = (
            exc.remote_code
            or "mcp_tool_error"
        )

        logger.error(
            "Kapruka checkout tool rejected the request",
            extra={
                "session_id":
                    session_id,

                "checkout_attempt_id":
                    attempt.id,

                "tool":
                    exc.tool_name,

                "remote_error_code":
                    exc.remote_code,

                "remote_error":
                    detail,

                "checkout_mode":
                    mode,

                "delivery_city":
                    canonical_city,

                "delivery_date":
                    requested_date.isoformat(),
            },
        )

        await update_checkout_attempt(
            attempt.id,
            status="failed",
            error_message=(
                f"[{error_code}] {detail}"
            ),
        )

        raise CheckoutError(
            f"Kapruka checkout was rejected: {detail}",
            status_code=502,
            code="kapruka_checkout_failed",
        ) from exc

    except Exception as exc:

        logger.exception(
            "Kapruka checkout result is uncertain after transport failure",
            extra={
                "session_id":
                    session_id,

                "checkout_attempt_id":
                    attempt.id,

                "checkout_mode":
                    mode,
            },
        )

        await update_checkout_attempt(
            attempt.id,
            status="uncertain",
            error_message=str(exc),
        )

        raise CheckoutError(
            "Kapruka could not confirm the checkout result "
            "because the external request failed. "
            "Do not create another checkout with a new key "
            "until this attempt is reconciled.",
            status_code=409,
            code="checkout_result_uncertain",
        ) from exc

    # --------------------------------------------------------------
    # Parse successful Kapruka response
    # --------------------------------------------------------------
    data = result.data or {}

    markdown = extract_markdown_result(
        data
    )

    if markdown is not None:

        parsed = parse_create_order_markdown(
            markdown
        )

        data = {
            **data,
            **parsed,
        }

        if parsed.get("error_code"):

            error_code = str(
                parsed[
                    "error_code"
                ]
            )

            message = str(
                parsed.get(
                    "message"
                )
                or "Kapruka rejected the checkout."
            )

            await update_checkout_attempt(
                attempt.id,
                status="failed",
                error_message=(
                    f"[{error_code}] "
                    f"{message}"
                ),
            )

            raise CheckoutError(
                f"Kapruka checkout was rejected: {message}",
                status_code=502,
                code="kapruka_checkout_failed",
            )

    if isinstance(
        data,
        dict,
    ):

        raw_markdown = data.get(
            "raw_markdown"
        )

        if (
            isinstance(
                raw_markdown,
                str,
            )
            and raw_markdown
            .lstrip()
            .lower()
            .startswith("error ")
        ):

            await update_checkout_attempt(
                attempt.id,
                status="failed",
                error_message=(
                    raw_markdown.strip()
                ),
            )

            raise CheckoutError(
                "Kapruka checkout was rejected: "
                f"{raw_markdown.strip()}",
                status_code=502,
                code="kapruka_checkout_failed",
            )

    checkout_url = (
        data.get("checkout_url")
        or data.get("pay_url")
        or data.get("url")
    )

    if not checkout_url:

        await update_checkout_attempt(
            attempt.id,
            status="uncertain",
            error_message=(
                "Kapruka create_order succeeded but "
                "no checkout URL was returned. "
                "The external result must be reconciled "
                "before retrying."
            ),
        )

        raise CheckoutError(
            "Kapruka accepted the checkout request but did not "
            "return a payment link. Do not retry with a new "
            "checkout key until the attempt is reconciled.",
            status_code=409,
            code="checkout_url_missing_uncertain",
        )

    # --------------------------------------------------------------
    # Persist local order
    # --------------------------------------------------------------
    subtotal = sum(
        item.price * item.quantity
        for item in items
    )

    try:

        order = await create_order_record(
            OrderRow(
                session_id=session_id,
                kapruka_order_number=(
                    data.get(
                        "order_number"
                    )
                ),
                status="awaiting_payment",
                checkout_url=checkout_url,
                total=float(
                    data.get(
                        "total",
                        subtotal,
                    )
                    or subtotal
                ),
                currency=currency,
            )
        )

    except Exception as exc:

        await update_checkout_attempt(
            attempt.id,
            status="uncertain",
            external_order_number=(
                data.get(
                    "order_number"
                )
            ),
            checkout_url=checkout_url,
            total=float(
                data.get(
                    "total",
                    subtotal,
                )
                or subtotal
            ),
            currency=currency,
            error_message=(
                "External order created but "
                "local persistence failed: "
                f"{exc}"
            ),
        )

        raise CheckoutError(
            "Kapruka created the order, but local confirmation failed. "
            "Do not retry this checkout; the attempt must be reconciled.",
            status_code=409,
            code="checkout_persistence_uncertain",
        ) from exc

    await update_checkout_attempt(
        attempt.id,
        status="succeeded",
        order_id=order.id,
        external_order_number=(
            order.kapruka_order_number
        ),
        checkout_url=checkout_url,
        total=order.total,
        currency=currency,
    )

    # --------------------------------------------------------------
    # Clear local cart only after external order + local order exist
    # --------------------------------------------------------------
    try:
        await clear_cart(
            session_id
        )

    except Exception:

        # Never create another external order just because cart cleanup failed.
        return {
            "order_id":
                order.id,

            "kapruka_order_number":
                order.kapruka_order_number,

            "checkout_url":
                checkout_url,

            "total":
                order.total,

            "currency":
                currency,
        }

    return {
        "order_id":
            order.id,

        "kapruka_order_number":
            order.kapruka_order_number,

        "checkout_url":
            checkout_url,

        "total":
            order.total,

        "currency":
            currency,
    }