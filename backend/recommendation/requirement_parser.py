"""Convert the current natural-language request into a typed ShoppingSpec."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.core.logging import get_logger
from backend.llm.openrouter_client import (
    get_openrouter_client,
)
from backend.llm.prompt_loader import (
    render_prompt,
)
from backend.observability.tracing import traced
from backend.recommendation.models import (
    AttributeConstraint,
    BudgetConstraint,
    ItemRole,
    QuantityConstraint,
    ShoppingSpec,
)

logger = get_logger(__name__)

_CURRENCY_RE = (
    r"(?:lkr|rs\.?|rupees|₨|usd|eur|gbp|\$)"
)

_NUMBER_RE = (
    r"([0-9][0-9,]*(?:\.[0-9]+)?)"
)


_STOP_WORDS = {
    "a",
    "an",
    "the",
    "my",
    "me",
    "for",
    "him",
    "her",
    "them",
    "his",
    "their",
    "please",
    "some",
    "any",
    "best",
    "good",
    "suitable",
    "top",
    "options",
    "option",
    "recommend",
    "recommendations",
    "recommendation",
    "suggest",
    "suggestions",
    "buy",
    "get",
    "find",
    "show",
    "want",
    "need",
    "would",
    "like",
    "looking",
    "to",
    "of",
    "with",
    "and",
    "or",
}


_ALLOWED_ATTRIBUTE_KINDS = {
    "attribute",
    "brand",
    "model",
    "variant",
    "feature",
    "style",
    "color",
    "material",
    "audience",
    "other",
}


def _norm(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value).strip().lower(),
    )


def _clean_terms(
    values: Any,
) -> list[str]:
    if not isinstance(
        values,
        list,
    ):
        return []

    output: list[str] = []

    for value in values:
        text = _norm(
            str(value)
        )

        if (
            text
            and text not in output
        ):
            output.append(text)

    return output


def _parse_amount(
    value: Any,
) -> float | None:
    if value is None:
        return None

    try:
        return float(
            str(value)
            .replace(",", "")
            .replace(" ", "")
        )
    except (
        TypeError,
        ValueError,
    ):
        return None


def _extract_budget(
    text: str,
) -> BudgetConstraint | None:
    normalized = _norm(text)

    between = re.search(
        rf"\bbetween\s+"
        rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}"
        rf"\s+and\s+"
        rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}",
        normalized,
        re.IGNORECASE,
    )

    if between:
        return BudgetConstraint(
            min_price=_parse_amount(
                between.group(1)
            ),
            max_price=_parse_amount(
                between.group(2)
            ),
        )

    maximum_patterns = [
        (
            rf"\b(?:maximum|max)\s+"
            rf"(?:budget|amount|price|spend|spending)\b"
            rf".{{0,50}}?"
            rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}"
        ),
        (
            rf"\b(?:under|below|up\s+to|"
            rf"at\s+most|no\s+more\s+than|"
            rf"less\s+than)\s+"
            rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}"
        ),
        (
            rf"\bbudget\s*"
            rf"(?:is|of|:)?\s*"
            rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}"
        ),
        (
            rf"\b(?:i\s+can|i\s+could|"
            rf"i\s+am\s+willing\s+to)\s+"
            rf"spend\s+"
            rf"{_CURRENCY_RE}?\s*{_NUMBER_RE}"
        ),
        (
            rf"\b{_CURRENCY_RE}\s*{_NUMBER_RE}"
        ),
        (
            rf"\b{_NUMBER_RE}\s*"
            rf"{_CURRENCY_RE}\b"
        ),
    ]

    for pattern in maximum_patterns:
        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )

        if not match:
            continue

        amount = _parse_amount(
            match.group(1)
        )

        if amount is not None:
            return BudgetConstraint(
                max_price=amount
            )

    return None


def _extract_quantity(
    text: str,
) -> QuantityConstraint | None:
    normalized = _norm(text)

    unit_pattern = (
        r"kg|kgs|kilograms?|g|grams?|"
        r"ml|millilit(?:er|re)s?|"
        r"l|lit(?:er|re)s?|"
        r"lb|lbs|pounds?|"
        r"oz|ounces?|pieces?|pcs?|"
        r"pack(?:s|age|ages)?"
    )

    patterns = [
        (
            rf"\b{_NUMBER_RE}\s*"
            rf"(?P<unit>{unit_pattern})\b"
        ),
        (
            rf"\bpack\s+of\s+"
            rf"{_NUMBER_RE}\b"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )

        if not match:
            continue

        value = _parse_amount(
            match.group(1)
        )

        if value is None:
            continue

        unit = (
            match.groupdict().get(
                "unit"
            )
            or "pack"
        ).lower()

        unit_map = {
            "kgs": "kg",
            "kilogram": "kg",
            "kilograms": "kg",
            "grams": "g",
            "gram": "g",
            "milliliter": "ml",
            "milliliters": "ml",
            "millilitre": "ml",
            "millilitres": "ml",
            "liters": "l",
            "liter": "l",
            "litres": "l",
            "litre": "l",
            "lbs": "lb",
            "pounds": "lb",
            "pound": "lb",
            "ounces": "oz",
            "ounce": "oz",
            "pieces": "piece",
            "pcs": "piece",
            "packs": "pack",
            "packages": "pack",
            "package": "pack",
        }

        unit = unit_map.get(
            unit,
            unit,
        )

        return QuantityConstraint(
            value=value,
            unit=unit,
            mode="exact",
        )

    return None


def _extract_product_family(
    user_message: str,
    router_slots: dict[str, Any],
) -> str | None:
    for key in (
        "product_family",
        "product_type",
        "product",
        "category",
    ):
        value = router_slots.get(
            key
        )

        if value:
            text = _norm(
                str(value)
            )

            if text:
                return text

    text = _norm(
        user_message
    )

    patterns = [
        (
            r"\bwhat\s+are\s+the\s+"
            r"(?:best|good|suitable|top)\s+"
            r"(.+?)(?:\s+for\s+|\?|$)"
        ),
        (
            r"\b(?:find|show|recommend|suggest)"
            r"\s+(?:me\s+)?(.+?)"
            r"(?:\s+for\s+|\?|$)"
        ),
        (
            r"\b(?:want|need|would\s+like|looking\s+for)"
            r"\s+(?:to\s+buy\s+)?"
            r"(?:a|an|some)?\s*(.+?)"
            r"(?:\s+for\s+|\?|$)"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if not match:
            continue

        phrase = re.sub(
            r"\s+",
            " ",
            match.group(1),
        ).strip(
            " .,!?:;"
        )

        # Remove common request tails that are not part of the product family.
        phrase = re.split(
            r"\b(?:under|below|up to|"
            r"within|max(?:imum)?\s+budget|"
            r"budget\s+is)\b",
            phrase,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

        words = [
            word
            for word in phrase.split()
            if word not in _STOP_WORDS
        ]

        if words:
            return " ".join(words)

    return None


def _fallback_components(
    text: str,
) -> list[str]:
    normalized = _norm(text)

    component_markers = [
        r"\bwith\s+"
        r"(?:a\s+|an\s+|some\s+)?"
        r"([^.!?]+)",
        r"\bcontaining\s+([^.!?]+)",
        r"\bincluding\s+([^.!?]+)",
        r"\bcomes\s+with\s+([^.!?]+)",
    ]

    for pattern in component_markers:
        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )

        if not match:
            continue

        phrase = match.group(1)

        parts = re.split(
            r"\s*,\s*|\s+and\s+",
            phrase,
        )

        cleaned = []

        for part in parts:
            value = part.strip(
                " .,!?:;"
            )

            if (
                value
                and len(value) <= 80
            ):
                cleaned.append(
                    value
                )

        if len(cleaned) >= 2:
            return cleaned

    return []


# Product-role terms are used only to make the deterministic fallback agree
# with the same semantic distinction used by the LLM. They are intentionally
# generic; the final product-type gate remains the authority.
_ACCESSORY_ROLE_TERMS = {
    "case",
    "cover",
    "charger",
    "cable",
    "adapter",
    "stand",
    "holder",
    "strap",
    "protector",
    "screen protector",
    "topper",
    "candle",
    "candles",
    "card",
    "cards",
    "knife",
    "storage",
    "box",
}

_REPLACEMENT_ROLE_TERMS = {
    "replacement",
    "spare",
    "replacement part",
}

_COMPONENT_ROLE_TERMS = {
    "component",
    "part",
    "module",
}

_BUNDLE_ROLE_TERMS = {
    "bundle",
    "set",
    "kit",
    "combo",
}


def _singularize_product_type(value: str | None) -> str | None:
    """Conservative normalization for base product-type labels."""
    if not value:
        return None

    text = _norm(value)

    if not text:
        return None

    text = re.sub(
        r"\bsmart\s*phones?\b",
        "mobile phone",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bsmartphones?\b",
        "mobile phone",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bmobile\s+phones?\b",
        "mobile phone",
        text,
        flags=re.IGNORECASE,
    )

    words = text.split()

    if len(words) == 1:
        word = words[0]
        if word.endswith("ies") and len(word) > 3:
            word = word[:-3] + "y"
        elif word.endswith("ses") and len(word) > 3:
            word = word[:-2]
        elif word.endswith("es") and len(word) > 2:
            word = word[:-2]
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 1:
            word = word[:-1]
        return word

    if words[-1].endswith("ies") and len(words[-1]) > 3:
        words[-1] = words[-1][:-3] + "y"
    elif words[-1].endswith("ses") and len(words[-1]) > 3:
        words[-1] = words[-1][:-2]
    elif words[-1].endswith("es") and len(words[-1]) > 2:
        words[-1] = words[-1][:-2]
    elif words[-1].endswith("s") and not words[-1].endswith("ss") and len(words[-1]) > 1:
        words[-1] = words[-1][:-1]

    return " ".join(words)


def _infer_item_role(product_family: str | None) -> ItemRole:
    text = _norm(product_family or "")

    if not text:
        return "primary_product"

    if any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        )
        for term in _REPLACEMENT_ROLE_TERMS
    ):
        return "replacement"

    if any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        )
        for term in _COMPONENT_ROLE_TERMS
    ):
        return "component"

    if any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        )
        for term in _BUNDLE_ROLE_TERMS
    ):
        return "bundle"

    if any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        )
        for term in _ACCESSORY_ROLE_TERMS
    ):
        return "accessory"

    return "primary_product"

def _fallback_spec(
    user_message: str,
    router_slots: dict[str, Any],
) -> ShoppingSpec:
    budget = _extract_budget(
        user_message
    )

    quantity = _extract_quantity(
        user_message
    )

    family = _extract_product_family(
        user_message,
        router_slots,
    )

    required: list[
        AttributeConstraint
    ] = []

    excluded: list[
        AttributeConstraint
    ] = []

    text = _norm(
        user_message
    )

    for pattern in (
        r"\bwithout\s+([^,.;!?]+)",
        r"\bavoid\s+([^,.;!?]+)",
        r"\bno\s+([^,.;!?]+)",
    ):
        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            value = match.group(1).strip()

            if value:
                excluded.append(
                    AttributeConstraint(
                        term=value,
                        polarity="exclude",
                    )
                )

    for pattern in (
        r"\bwith\s+([^,.;!?]+)",
        r"\bthat\s+has\s+([^,.;!?]+)",
        r"\bmust\s+have\s+([^,.;!?]+)",
    ):
        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            value = match.group(1).strip()

            if value:
                required.append(
                    AttributeConstraint(
                        term=value,
                        polarity="include",
                    )
                )

    recipient = router_slots.get(
        "recipient_name"
    )

    occasion = router_slots.get(
        "occasion"
    )

    components = _fallback_components(
        user_message
    )

    query_parts = []

    if family:
        query_parts.append(
            family
        )

    if occasion:
        query_parts.append(
            _norm(
                str(occasion)
            )
        )

    query_parts.extend(
        item.term
        for item in required[:3]
    )

    query_parts.extend(
        components[:3]
    )

    return ShoppingSpec(
        product_family=family,
        canonical_product_type=(
            _singularize_product_type(family)
        ),
        item_role=_infer_item_role(family),
        parent_product_type=None,
        recipient_name=(
            str(recipient).strip()
            if recipient
            else None
        ),
        occasion=(
            _norm(
                str(occasion)
            )
            if occasion
            else None
        ),
        budget=budget,
        required_attributes=required,
        excluded_attributes=excluded,
        required_components=[
            _norm(value)
            for value in components
        ],
        quantity=quantity,
        search_queries=[
            " ".join(
                query_parts
            ).strip()
        ]
        if query_parts
        else [],
    )



_DETERMINISTIC_SEMANTIC_PATTERNS = (
    # Durable/soft preference language that can change ranking semantics.
    r"\b(?:i|we)\s+(?:really\s+)?(?:like|love|prefer|want|favor|favour)\b",
    r"\b(?:likes|loves|prefers|favorite|favourite|preferred)\b",
    # Open-ended / ambiguous requests are intentionally sent through the LLM.
    r"\b(?:something|anything|whatever|surprise|gift|present)\b",
    r"\b(?:special|elegant|luxurious|luxury|premium|romantic|unique|beautiful|cute|stylish|modern|traditional)\b",
    r"\b(?:similar|matching|according\s+to|based\s+on)\b",
    r"\b(?:maybe|perhaps|not\s+sure|i\s+am\s+not\s+sure)\b",
    # Semantic constraint phrases whose meaning is safer to leave to the
    # existing LLM parser rather than approximating with regexes.
    r"\b(?:with|without|avoid|contains?|containing|including|comes\s+with|has|have|must\s+have)\b",
    r"\b(?:free\s+from|allergy|allergic|suitable\s+for|meant\s+for)\b",
)

_DETERMINISTIC_OCCASION_WORDS = (
    "birthday",
    "anniversary",
    "wedding",
    "valentine",
    "christmas",
    "new year",
    "mother's day",
    "fathers day",
    "father's day",
    "mothers day",
    "graduation",
    "baby shower",
    "newborn",
    "congratulations",
    "thank you",
    "farewell",
    "get well",
)


def _extract_current_safety_terms(user_message: str) -> set[str]:
    """Extract explicit current-turn allergy terms for fail-closed filtering."""
    text = _norm(user_message)
    if not text:
        return set()

    patterns = (
        r"\b(?:allergic|allergy)\s+(?:to|with|from)\s+([^,.;!?]+)",
        r"\b(?:allergic|allergy)\s*[:=-]\s*([^,.;!?]+)",
        r"\b(?:cannot|can't|cant)\s+have\s+([^,.;!?]+)\s+(?:because|due|from)\s+allerg",
    )

    values: set[str] = set()

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue

        raw = _norm(match.group(1))
        if raw:
            raw = re.split(
                r"\b(?:and|but|while|so|because|since|that|which)\b",
                raw,
                maxsplit=1,
            )[0].strip()
            if raw:
                values.add(raw)

    return values


def _mark_current_safety_constraints(
    spec: ShoppingSpec,
    user_message: str,
) -> ShoppingSpec:
    safety_terms = _extract_current_safety_terms(
        user_message
    )

    if not safety_terms:
        return spec

    normalized_terms = {
        _norm(term)
        for term in safety_terms
        if _norm(term)
    }

    existing = {
        _norm(item.term): item
        for item in spec.excluded_attributes
        if item.term
    }

    for term, constraint in existing.items():
        if any(
            term == safety_term
            or term in safety_term
            or safety_term in term
            for safety_term in normalized_terms
        ):
            constraint.safety_critical = True

    for safety_term in normalized_terms:
        if safety_term not in existing:
            spec.excluded_attributes.append(
                AttributeConstraint(
                    term=safety_term,
                    polarity="exclude",
                    source="current_message",
                    safety_critical=True,
                )
            )

    return spec


def _requires_requirement_llm(
    *,
    user_message: str,
    router_slots: dict[str, Any],
    fallback: ShoppingSpec,
) -> bool:
    """Return True only when the deterministic spec may miss semantics.

    This gate is deliberately conservative. Simple requests such as
    "find birthday cakes for my wife under 5000" can be handled by the
    existing deterministic parser. Requests containing preference,
    ambiguity, semantic constraint, or unresolved-occasion language still
    use the existing LLM parser unchanged.
    """

    if not fallback.product_family:
        return True

    # Multi-word product phrases often contain modifiers that must be
    # separated from the base product type by the semantic parser, e.g.
    # "vanilla cakes" -> product type "cake" + preference "vanilla",
    # or "phone case" -> product type "phone case" + accessory role.
    # Keep the existing LLM path for those cases instead of guessing with
    # regexes.
    if len(fallback.product_family.split()) > 1:
        return True

    text = _norm(user_message)

    if not text:
        return True

    for pattern in _DETERMINISTIC_SEMANTIC_PATTERNS:
        if re.search(
            pattern,
            text,
            re.IGNORECASE,
        ):
            return True

    # If the router did not already resolve an occasion, an explicit occasion
    # phrase is a reason to keep the semantic parser in the path.
    router_occasion = router_slots.get(
        "occasion"
    )

    if not router_occasion:
        for occasion in _DETERMINISTIC_OCCASION_WORDS:
            if occasion in text:
                return True

    # Alternative/compound requests are intentionally left to the existing
    # LLM parser rather than guessing which product family is authoritative.
    if re.search(
        r"\b(?:either|or|instead|as\s+well\s+as)\b",
        text,
        re.IGNORECASE,
    ):
        return True

    return False


def _from_llm(
    raw: dict[str, Any],
) -> ShoppingSpec:
    budget_raw = (
        raw.get(
            "budget"
        )
        or {}
    )

    budget = None

    if isinstance(
        budget_raw,
        dict,
    ):
        minimum = _parse_amount(
            budget_raw.get(
                "min_price"
            )
        )

        maximum = _parse_amount(
            budget_raw.get(
                "max_price"
            )
        )

        if (
            minimum is not None
            or maximum is not None
        ):
            budget = BudgetConstraint(
                min_price=minimum,
                max_price=maximum,
                currency=(
                    str(
                        budget_raw.get(
                            "currency"
                        )
                        or "LKR"
                    )
                    .upper()
                ),
            )

    required = []

    for item in (
        raw.get(
            "required_attributes",
            [],
        )
        or []
    ):
        if isinstance(
            item,
            str,
        ):
            required.append(
                AttributeConstraint(
                    term=_norm(item)
                )
            )

        elif (
            isinstance(
                item,
                dict,
            )
            and item.get(
                "term"
            )
        ):
            kind = _norm(
                str(
                    item.get(
                        "kind"
                    )
                    or "attribute"
                )
            )

            if (
                kind
                not in _ALLOWED_ATTRIBUTE_KINDS
            ):
                kind = "attribute"

            required.append(
                AttributeConstraint(
                    term=_norm(
                        item["term"]
                    ),
                    kind=kind,
                )
            )

    excluded = []

    for item in (
        raw.get(
            "excluded_attributes",
            [],
        )
        or []
    ):
        if isinstance(
            item,
            str,
        ):
            excluded.append(
                AttributeConstraint(
                    term=_norm(item),
                    polarity="exclude",
                )
            )

        elif (
            isinstance(
                item,
                dict,
            )
            and item.get(
                "term"
            )
        ):
            kind = _norm(
                str(
                    item.get(
                        "kind"
                    )
                    or "attribute"
                )
            )

            if (
                kind
                not in _ALLOWED_ATTRIBUTE_KINDS
            ):
                kind = "attribute"

            excluded.append(
                AttributeConstraint(
                    term=_norm(
                        item["term"]
                    ),
                    kind=kind,
                    polarity="exclude",
                )
            )

    quantity_raw = raw.get(
        "quantity"
    )

    quantity = None

    if (
        isinstance(
            quantity_raw,
            dict,
        )
        and quantity_raw.get(
            "value"
        )
        is not None
    ):
        try:
            quantity = QuantityConstraint(
                value=float(
                    quantity_raw["value"]
                ),
                unit=_norm(
                    str(
                        quantity_raw.get(
                            "unit"
                        )
                        or "piece"
                    )
                ),
                mode=(
                    quantity_raw.get(
                        "mode"
                    )
                    if quantity_raw.get(
                        "mode"
                    )
                    in {
                        "exact",
                        "at_least",
                        "at_most",
                    }
                    else "exact"
                ),
            )

        except (
            TypeError,
            ValueError,
        ):
            quantity = None

    family = raw.get(
        "product_family"
    )

    canonical_type = raw.get(
        "canonical_product_type"
    )

    if canonical_type:
        canonical_type = _singularize_product_type(
            str(canonical_type)
        )
    else:
        canonical_type = _singularize_product_type(
            str(family)
            if family
            else None
        )

    raw_role = _norm(
        str(
            raw.get(
                "item_role"
            )
            or "primary_product"
        )
    )

    allowed_roles = {
        "primary_product",
        "accessory",
        "component",
        "replacement",
        "bundle",
        "service",
        "unknown",
    }

    if raw_role not in allowed_roles:
        raw_role = "primary_product"

    parent_product_type = raw.get(
        "parent_product_type"
    )

    if parent_product_type:
        parent_product_type = _singularize_product_type(
            str(parent_product_type)
        )

    return ShoppingSpec(
        product_family=(
            _norm(
                str(family)
            )
            if family
            else None
        ),
        canonical_product_type=canonical_type,
        item_role=raw_role,
        parent_product_type=parent_product_type,
        recipient_name=(
            _norm(
                str(
                    raw["recipient_name"]
                )
            )
            if raw.get(
                "recipient_name"
            )
            else None
        ),
        occasion=(
            _norm(
                str(
                    raw["occasion"]
                )
            )
            if raw.get(
                "occasion"
            )
            else None
        ),
        budget=budget,
        required_attributes=required,
        excluded_attributes=excluded,
        required_components=_clean_terms(
            raw.get(
                "required_components",
                [],
            )
        ),
        excluded_components=_clean_terms(
            raw.get(
                "excluded_components",
                [],
            )
        ),
        quantity=quantity,
        in_stock_only=bool(
            raw.get(
                "in_stock_only",
                True,
            )
        ),
        preferred_attributes=_clean_terms(
            raw.get(
                "preferred_attributes",
                [],
            )
        ),
        search_queries=_clean_terms(
            raw.get(
                "search_queries",
                [],
            )
        ),
        ambiguities=_clean_terms(
            raw.get(
                "ambiguities",
                [],
            )
        ),
    )


@traced(
    "recommendation.requirement.parse"
)
async def parse_requirements(
    *,
    user_message: str,
    memory_context: str,
    router_slots: dict[str, Any],
) -> ShoppingSpec:
    """Parse the CURRENT turn.

    Durable memory is context, not current budget.
    """

    deterministic_budget = (
        _extract_budget(
            user_message
        )
    )

    fallback = _fallback_spec(
        user_message,
        router_slots,
    )

    # ---------------------------------------------------------
    # DETERMINISTIC-FIRST FAST PATH
    #
    # The deterministic parser already extracts product family, recipient,
    # occasion from router slots, budget, quantity, basic include/exclude
    # constraints, components, and search terms. For simple, unambiguous
    # requests we return that spec immediately.
    #
    # Anything semantically richer is still sent through the exact existing
    # LLM parsing path below. This keeps the accurate LLM behavior available
    # whenever the deterministic parser could plausibly miss meaning.
    # ---------------------------------------------------------
    if not _requires_requirement_llm(
        user_message=user_message,
        router_slots=router_slots,
        fallback=fallback,
    ):

        fallback.budget = deterministic_budget

        return fallback

    client = get_openrouter_client()

    prompt = render_prompt(
        "shopping_spec_system.jinja",
        user_message=user_message,
        memory_context=(
            memory_context
            or "(none)"
        ),
        router_slots=json.dumps(
            router_slots,
            default=str,
        ),
    )

    prompt += """

PRODUCT-IDENTITY RULES — REQUIRED:

Interpret what the user is actually asking to purchase, not merely words that
appear somewhere in a product name.

Return these additional fields:
- canonical_product_type: the base purchasable product type, singular and
  generic. Remove brand, model, color, flavor, occasion, style, and other
  descriptive details. Examples: "iPhone 15" -> "mobile phone";
  "vanilla birthday cake" -> "cake"; "chicken sausage" -> "sausage".
- item_role: one of primary_product, accessory, component, replacement,
  bundle, service, unknown.
- parent_product_type: the canonical primary product type when the requested
  item is an accessory/component/replacement; otherwise null.

CRITICAL: do not classify an accessory as its parent product merely because the
parent product name appears in the accessory name. Examples:
- "phone" request: "Samsung Galaxy phone" -> mobile phone / primary_product;
  "Samsung phone case" -> phone case / accessory / mobile phone parent.
- "cake" request: "vanilla cake" -> cake / primary_product;
  "cake topper" -> cake topper / accessory / cake parent;
  "cake candle set" -> cake candle set / accessory / cake parent.
- "sausage" request does NOT imply that a category named "Sausages" exists.
The semantic product type must come from the product itself, not from a catalog
category name.

When the user explicitly asks for an accessory, component, replacement, or
bundle, canonical_product_type should describe that requested item itself.
"""

    try:
        response = await client.chat_completion(
            task="recommendation_parser",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Return the "
                        "ShoppingSpec JSON now."
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

        if not isinstance(
            parsed,
            dict,
        ):
            raise ValueError(
                "Requirement parser returned "
                "a non-object."
            )

        raw = parsed.get(
            "shopping_spec",
            parsed,
        )

        spec = _from_llm(
            raw
            if isinstance(
                raw,
                dict,
            )
            else {}
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Requirement parser failed; "
            "deterministic fallback will be used: %s",
            exc,
        )

        spec = fallback

    # Absolute current-turn budget boundary.
    spec.budget = deterministic_budget

    if not spec.product_family:
        spec.product_family = (
            fallback.product_family
        )

    if not spec.canonical_product_type:
        spec.canonical_product_type = (
            fallback.canonical_product_type
            or _singularize_product_type(
                spec.product_family
            )
        )

    if not spec.item_role or spec.item_role == "unknown":
        spec.item_role = (
            fallback.item_role
            or _infer_item_role(
                spec.product_family
            )
        )

    if not spec.parent_product_type:
        spec.parent_product_type = (
            fallback.parent_product_type
        )

    if not spec.recipient_name:
        spec.recipient_name = (
            fallback.recipient_name
        )

    if not spec.occasion:
        spec.occasion = (
            fallback.occasion
        )

    if not spec.quantity:
        spec.quantity = (
            fallback.quantity
        )

    if not spec.search_queries:
        spec.search_queries = (
            fallback.search_queries
        )

    if not spec.excluded_attributes:
        spec.excluded_attributes = (
            fallback.excluded_attributes
        )

    if not spec.required_attributes:
        spec.required_attributes = (
            fallback.required_attributes
        )

    if not spec.required_components:
        spec.required_components = (
            fallback.required_components
        )

    spec = _mark_current_safety_constraints(
        spec,
        user_message,
    )

    return spec