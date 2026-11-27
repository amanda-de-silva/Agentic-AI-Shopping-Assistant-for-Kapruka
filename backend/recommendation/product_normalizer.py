"""Normalize Kapruka product detail responses into generic ProductFacts."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from backend.core.logging import get_logger
from backend.llm.openrouter_client import get_openrouter_client
from backend.llm.prompt_loader import render_prompt
from backend.observability.tracing import traced
from backend.recommendation.models import ProductFacts, QuantityValue
from backend.recommendation.safety import analyze_exclusion

logger = get_logger(__name__)


_UNIT_ALIASES = {
    "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "grams": "g", "gram": "g",
    "milliliters": "ml", "milliliter": "ml", "millilitres": "ml", "millilitre": "ml",
    "liters": "l", "liter": "l", "litres": "l", "litre": "l",
    "lbs": "lb", "pounds": "lb", "pound": "lb",
    "ounces": "oz", "ounce": "oz",
    "pieces": "piece", "pcs": "piece",
    "packs": "pack", "package": "pack", "packages": "pack",
}


_AUDIENCE_TERMS = {
    "male": ("men", "mens", "men's", "gents", "gent's", "boys", "boy"),
    "female": ("women", "womens", "women's", "ladies", "girls", "girl"),
    "unisex": ("unisex",),
    "couple": ("couple", "couples", "his and hers", "him and her"),
    "kids": ("kids", "children", "child", "boys", "girls"),
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _parse_quantity(text: str) -> QuantityValue | None:
    patterns = [
        r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kg|kgs|kilograms?|g|grams?|ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|lb|lbs|pounds?|oz|ounces?|pieces?|pcs?|packs?|packages?)\b",
        r"\bpack\s+of\s+(?P<value>\d+(?:\.\d+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue

        value = float(match.group("value"))
        unit = (match.groupdict().get("unit") or "pack").lower()
        unit = _UNIT_ALIASES.get(unit, unit)

        return QuantityValue(value=value, unit=unit)

    return None


def _audience(text: str) -> list[str]:
    normalized = _norm(text)
    values: list[str] = []

    for label, terms in _AUDIENCE_TERMS.items():
        if any(
            re.search(
                rf"(?<![a-z]){re.escape(term)}(?![a-z])",
                normalized,
            )
            for term in terms
        ):
            values.append(label)

    return values


def _baseline(product: Any) -> ProductFacts:
    text = " ".join(
        part
        for part in (
            getattr(product, "name", ""),
            getattr(product, "category", "") or "",
            getattr(product, "vendor", "") or "",
            getattr(product, "description", "") or "",
            getattr(product, "safety_text", "") or "",
        )
        if part
    )

    return ProductFacts(
        product_id=str(product.product_id),
        name=str(product.name),
        price=float(product.price),
        currency=str(product.currency),
        in_stock=product.in_stock,
        category=product.category,
        vendor=product.vendor,
        url=product.url,
        image_url=product.image_url,
        description=getattr(product, "description", None) or "",
        safety_text=getattr(product, "safety_text", "") or "",
        product_family=product.category or None,
        audience=_audience(text),
        package_quantity=_parse_quantity(text),
        raw=product.model_dump() if hasattr(product, "model_dump") else {},
    )


def _apply_llm_fact(raw: dict[str, Any], fact: ProductFacts) -> ProductFacts:
    family = raw.get("product_family")
    canonical_type = raw.get("canonical_product_type")
    item_role = raw.get("item_role")
    parent_product_type = raw.get("parent_product_type")
    audience = raw.get("audience")
    attributes = raw.get("attributes")
    components = raw.get("components")

    if family:
        fact.product_family = _norm(str(family))

    if canonical_type:
        fact.canonical_product_type = _norm(str(canonical_type))

    allowed_roles = {
        "primary_product",
        "accessory",
        "component",
        "replacement",
        "bundle",
        "service",
        "unknown",
    }

    if item_role:
        normalized_role = _norm(str(item_role))
        if normalized_role in allowed_roles:
            fact.item_role = normalized_role

    if parent_product_type:
        fact.parent_product_type = _norm(str(parent_product_type))

    if isinstance(audience, list):
        fact.audience = list(
            dict.fromkeys(
                _norm(str(x))
                for x in audience
                if str(x).strip()
            )
        )

    if isinstance(attributes, list):
        fact.attributes = list(
            dict.fromkeys(
                _norm(str(x))
                for x in attributes
                if str(x).strip()
            )
        )

    if isinstance(components, list):
        fact.components = list(
            dict.fromkeys(
                _norm(str(x))
                for x in components
                if str(x).strip()
            )
        )

    quantity = raw.get("package_quantity")

    if isinstance(quantity, dict) and quantity.get("value") is not None:
        try:
            fact.package_quantity = QuantityValue(
                value=float(quantity["value"]),
                unit=_norm(str(quantity.get("unit") or "piece")),
            )
        except (TypeError, ValueError):
            pass

    return fact


def _set_safety_flags(
    facts: list[ProductFacts],
    excluded_terms: list[str],
) -> None:
    """Build deterministic exclusion evidence from raw product text.

    This intentionally runs after LLM normalization so hard safety decisions
    never depend on an LLM-generated attribute/component list.
    """
    for fact in facts:
        raw_texts = [
            fact.name,
            fact.description,
            fact.safety_text,
            fact.category or "",
            fact.vendor or "",
        ]

        for term in excluded_terms:
            normalized = _norm(term)

            if not normalized:
                continue

            status, evidence = analyze_exclusion(
                normalized,
                raw_texts,
            )

            fact.safety_flags[normalized] = status  # type: ignore[assignment]

            if evidence:
                fact.safety_evidence[normalized] = evidence


async def _llm_normalize(
    facts: list[ProductFacts],
) -> list[ProductFacts]:
    if not facts:
        return facts

    client = get_openrouter_client()

    lines = []

    for fact in facts:
        lines.append(
            json.dumps(
                {
                    "id": fact.product_id,
                    "name": fact.name,
                    "category": fact.category,
                    "description": fact.description[:1500],
                    "details": fact.safety_text[:2200],
                },
                ensure_ascii=False,
            )
        )

    prompt = render_prompt(
        "product_normalizer_system.jinja",
        products="\n".join(lines),
    )

    prompt += """

PRODUCT IDENTITY / ROLE CLASSIFICATION — REQUIRED:

For every product, additionally return:
- canonical_product_type: the base purchasable type represented by the product itself. Use singular, generic labels. Strip brand, model, color, flavor, occasion, style, and descriptive modifiers. Examples: "Samsung Galaxy A55" -> "mobile phone"; "Creamy Eggless Vanilla Cake" -> "cake"; "Chicken Sausage" -> "sausage".
- item_role: one of primary_product, accessory, component, replacement, bundle, service, unknown.
- parent_product_type: the canonical primary product type if the product is an accessory/component/replacement; otherwise null.

IMPORTANT: classify from the product itself, not from a word appearing in its name or description. Do NOT collapse accessories into their parent products:
- "iPhone 15" -> mobile phone / primary_product
- "iPhone 15 case" -> phone case / accessory / mobile phone parent
- "Birthday cake" -> cake / primary_product
- "Cake topper" -> cake topper / accessory / cake parent
- "Cake candle set" -> cake candle set / accessory / cake parent
- "Greeting card" -> greeting card / primary_product when considered by itself

The category field is supporting metadata only. It is NOT proof that the product itself is the requested product type.
"""

    try:
        response = await client.chat_completion(
            task="product_normalizer",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": "Return normalized product facts as JSON.",
                },
            ],
            response_format={"type": "json_object"},
        )

        parsed = json.loads(client.extract_text(response))
        items = (
            parsed.get("products", parsed)
            if isinstance(parsed, dict)
            else []
        )

        if not isinstance(items, list):
            raise ValueError(
                "Product normalizer returned an invalid payload."
            )

        by_id = {
            fact.product_id: fact
            for fact in facts
        }

        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue

            fact = by_id.get(str(item["id"]))

            if fact:
                _apply_llm_fact(item, fact)

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Product semantic normalization failed; "
            "baseline facts will be used: %s",
            exc,
        )

    return facts


@traced("recommendation.product_normalize")
async def normalize_products(
    products: list[Any],
    *,
    excluded_terms: list[str] | None = None,
    batch_size: int = 12,
) -> list[ProductFacts]:
    baseline = [
        _baseline(product)
        for product in products
    ]

    chunks = [
        baseline[index : index + batch_size]
        for index in range(0, len(baseline), batch_size)
    ]

    if chunks:
        # Each batch contains different ProductFacts, so the LLM
        # normalization calls are independent and can safely run
        # concurrently.
        #
        # asyncio.gather() preserves the order of the awaitables,
        # so the final product order remains identical to the
        # original sequential implementation.
        batch_results = await asyncio.gather(
            *(
                _llm_normalize(chunk)
                for chunk in chunks
            )
        )

        normalized = [
            fact
            for batch in batch_results
            for fact in batch
        ]
    else:
        normalized = []

    _set_safety_flags(
        normalized,
        excluded_terms or [],
    )

    return normalized