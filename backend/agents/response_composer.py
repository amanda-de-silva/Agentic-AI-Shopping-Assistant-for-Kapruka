"""Compose natural language and derive deterministic UI actions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from backend.llm.openrouter_client import (
    get_openrouter_client,
)
from backend.llm.prompt_loader import (
    render_prompt,
)
from backend.observability.tracing import traced
from backend.schemas.ui_actions import (
    UIAction,
)


def build_ui_actions(
    specialist_outputs: dict[str, Any],
) -> list[UIAction]:
    """Build UI state events only from authoritative service output."""

    actions: list[UIAction] = []

    catalog = specialist_outputs.get(
        "catalog"
    )

    if (
        isinstance(
            catalog,
            dict,
        )
        and catalog.get("products")
    ):
        actions.append(
            UIAction(
                type="products_shown",
                payload={
                    "products": catalog[
                        "products"
                    ]
                },
            )
        )

    cart = specialist_outputs.get(
        "cart"
    )

    if (
        isinstance(
            cart,
            dict,
        )
        and isinstance(
            cart.get("cart"),
            dict,
        )
    ):
        actions.append(
            UIAction(
                type="cart_updated",
                payload=cart[
                    "cart"
                ],
            )
        )

    checkout = specialist_outputs.get(
        "checkout"
    )

    if (
        isinstance(
            checkout,
            dict,
        )
        and checkout.get(
            "checkout_url"
        )
    ):
        actions.append(
            UIAction(
                type="navigate_checkout",
                payload={
                    "checkout_url": (
                        checkout[
                            "checkout_url"
                        ]
                    )
                },
            )
        )

    order_status = specialist_outputs.get(
        "order_status"
    )

    if (
        isinstance(
            order_status,
            dict,
        )
        and order_status.get(
            "order_number"
        )
    ):
        actions.append(
            UIAction(
                type="order_status",
                payload=order_status,
            )
        )

    profile = specialist_outputs.get(
        "profile_recall"
    )

    if (
        isinstance(
            profile,
            dict,
        )
        and profile.get(
            "profile_text"
        )
    ):
        actions.append(
            UIAction(
                type="memory_recalled",
                payload={
                    "profile_text": (
                        profile[
                            "profile_text"
                        ]
                    )
                },
            )
        )

    return actions


def _language_context(
    specialist_outputs: dict[str, Any],
    fallback: str = "en",
) -> str:
    metadata = specialist_outputs.get(
        "_language"
    )

    if not isinstance(
        metadata,
        dict,
    ):
        return fallback

    value = str(
        metadata.get(
            "response_language",
            fallback,
        )
        or fallback
    ).lower()

    return (
        "si"
        if value == "si"
        else "en"
    )


@traced(
    "agent.response.compose"
)
async def compose(
    *,
    specialist_outputs: dict[str, Any],
    user_message: str,
    original_user_message: str | None = None,
    response_language: str | None = None,
) -> tuple[
    str,
    list[UIAction],
]:
    client = get_openrouter_client()

    target_language = (
        response_language
        or _language_context(
            specialist_outputs
        )
    )

    prompt = render_prompt(
        "response_composer_system.jinja",
        specialist_outputs=json.dumps(
            specialist_outputs,
            default=str,
            indent=2,
            ensure_ascii=False,
        ),
        user_message=user_message,
        original_user_message=(
            original_user_message
            or user_message
        ),
        response_language=target_language,
    )

    response = await client.chat_completion(
        task="composer",
        messages=[
            {
                "role": "system",
                "content": prompt,
            },
            {
                "role": "user",
                "content": (
                    "Write the final reply now."
                ),
            },
        ],
    )

    reply = (
        client.extract_text(
            response
        )
        .strip()
    )

    if not reply:
        reply = (
            "Sorry, I hit a snag putting "
            "that together."
        )

    return (
        reply,
        build_ui_actions(
            specialist_outputs
        ),
    )


async def compose_stream(
    *,
    specialist_outputs: dict[str, Any],
    user_message: str,
    original_user_message: str | None = None,
    response_language: str | None = None,
) -> AsyncIterator[str]:
    """Stream the response in the requested user language."""

    client = get_openrouter_client()

    target_language = (
        response_language
        or _language_context(
            specialist_outputs
        )
    )

    prompt = render_prompt(
        "response_composer_system.jinja",
        specialist_outputs=json.dumps(
            specialist_outputs,
            default=str,
            indent=2,
            ensure_ascii=False,
        ),
        user_message=user_message,
        original_user_message=(
            original_user_message
            or user_message
        ),
        response_language=target_language,
    )

    async for token in (
        client.chat_completion_stream(
            task="composer",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Write the final reply now."
                    ),
                },
            ],
        )
    ):
        yield token