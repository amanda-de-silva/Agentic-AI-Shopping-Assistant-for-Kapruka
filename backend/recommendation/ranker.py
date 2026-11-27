"""Ranking over already-eligible products."""

from __future__ import annotations

import json

from backend.core.logging import get_logger
from backend.llm.openrouter_client import (
    get_openrouter_client,
)
from backend.llm.prompt_loader import (
    render_prompt,
)
from backend.observability.tracing import traced
from backend.recommendation.models import (
    ProductFacts,
    RecipientContext,
    ShoppingSpec,
)

logger = get_logger(__name__)


def _norm(value: str) -> str:
    return " ".join(
        str(value)
        .strip()
        .lower()
        .split()
    )


def _audience_score(
    product: ProductFacts,
    recipient: RecipientContext | None,
) -> tuple[
    float,
    list[str],
]:
    if (
        recipient is None
        or recipient.gender_hint == "unknown"
    ):
        return (
            0.0,
            [],
        )

    audience = {
        _norm(value)
        for value in product.audience
    }

    if not audience:
        return (
            0.0,
            [],
        )

    if recipient.gender_hint == "male":
        if "male" in audience:
            return (
                40.0,
                ["matches male recipient"],
            )

        if "unisex" in audience:
            return (
                28.0,
                ["unisex recipient fit"],
            )

        if "couple" in audience:
            return (
                8.0,
                ["couple-oriented"],
            )

        if "female" in audience:
            return (
                -30.0,
                ["female-oriented"],
            )

    if recipient.gender_hint == "female":
        if "female" in audience:
            return (
                40.0,
                ["matches female recipient"],
            )

        if "unisex" in audience:
            return (
                28.0,
                ["unisex recipient fit"],
            )

        if "couple" in audience:
            return (
                8.0,
                ["couple-oriented"],
            )

        if "male" in audience:
            return (
                -30.0,
                ["male-oriented"],
            )

    return (
        0.0,
        [],
    )


def _deterministic_score(
    product: ProductFacts,
    spec: ShoppingSpec,
    recipient: RecipientContext | None,
) -> tuple[
    float,
    list[str],
]:
    score = 0.0
    reasons: list[str] = []

    text = _norm(
        product.searchable_text()
    )

    if (
        spec.occasion
        and _norm(spec.occasion)
        in text
    ):
        score += 45
        reasons.append(
            "occasion match"
        )

    for preference in (
        spec.preferred_attributes
    ):
        if _norm(preference) in text:
            score += 18
            reasons.append(
                f"matches preference: "
                f"{preference}"
            )

    audience_score, audience_reasons = (
        _audience_score(
            product,
            recipient,
        )
    )

    score += audience_score
    reasons.extend(
        audience_reasons
    )

    if (
        spec.quantity
        and product.package_quantity
    ):
        score += 20
        reasons.append(
            "package size identified"
        )

    if (
        spec.budget
        and spec.budget.max_price
    ):
        ratio = max(
            0.0,
            min(
                product.price
                / spec.budget.max_price,
                1.0,
            ),
        )

        score += (
            1.0 - ratio
        ) * 4.0

    if product.in_stock:
        score += 2

    return (
        score,
        reasons,
    )


def deterministic_rank(
    products: list[ProductFacts],
    spec: ShoppingSpec,
    recipient: RecipientContext | None,
) -> list[ProductFacts]:
    scored = []

    for product in products:
        score, reasons = (
            _deterministic_score(
                product,
                spec,
                recipient,
            )
        )

        scored.append(
            (
                score,
                product.product_id,
                product,
                reasons,
            )
        )

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        item[2]
        for item in scored
    ]


@traced(
    "recommendation.rank"
)
async def rank_products(
    *,
    products: list[ProductFacts],
    spec: ShoppingSpec,
    recipient: RecipientContext | None,
    user_message: str,
    final_limit: int,
) -> list[ProductFacts]:
    if len(products) <= 1:
        return products[:final_limit]

    deterministic = deterministic_rank(
        products,
        spec,
        recipient,
    )

    client = get_openrouter_client()

    candidate_lines = []

    for product in deterministic:
        candidate_lines.append(
            {
                "id": product.product_id,
                "name": product.name,
                "price": product.price,
                "currency": product.currency,
                "category": product.category,
                "audience": product.audience,
                "attributes": product.attributes,
                "components": product.components,
                "description": (
                    product.description[:900]
                ),
            }
        )

    payload = {
        "current_request": user_message,
        "shopping_spec": spec.model_dump(),
        "recipient": (
            recipient.model_dump()
            if recipient
            else None
        ),
        "eligible_candidates": candidate_lines,
    }

    try:
        prompt = render_prompt(
            "recommendation_ranker_system.jinja",
            context=json.dumps(
                payload,
                default=str,
                indent=2,
            ),
        )

        response = await client.chat_completion(
            task="recommendation_ranker",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Rank only the supplied "
                        "eligible product IDs."
                    ),
                },
            ],
            response_format={
                "type": "json_object"
            },
        )

        parsed = json.loads(
            client.extract_text(
                response
            )
        )

        ids = (
            parsed.get(
                "ranked_product_ids",
                [],
            )
            if isinstance(
                parsed,
                dict,
            )
            else []
        )

        allowed = {
            product.product_id: product
            for product in products
        }

        ranked: list[
            ProductFacts
        ] = []

        for product_id in ids:
            product = allowed.get(
                str(product_id)
            )

            if (
                product
                and product not in ranked
            ):
                ranked.append(
                    product
                )

        for product in deterministic:
            if product not in ranked:
                ranked.append(
                    product
                )

        return ranked[
            :final_limit
        ]

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Recommendation ranking failed; "
            "deterministic rank will be used: %s",
            exc,
        )

        return deterministic[
            :final_limit
        ]