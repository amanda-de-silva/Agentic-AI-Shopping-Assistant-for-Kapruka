"""Deterministic hard-constraint engine.

This module is the authority on whether a product may be recommended.
"""

from __future__ import annotations

import re
from typing import Iterable

from backend.recommendation.safety import (
    analyze_allergy_exclusion,
    analyze_exclusion,
)
from backend.recommendation.models import (
    EligibilityResult,
    ProductFacts,
    QuantityConstraint,
    ShoppingSpec,
)


_SYN = {
    "peanut": {
        "peanut",
        "peanuts",
        "groundnut",
        "groundnuts",
    },
    "milk": {
        "milk",
        "dairy",
        "casein",
        "whey",
        "lactose",
    },
    "egg": {
        "egg",
        "eggs",
        "albumin",
        "mayonnaise",
    },
    "tree nut": {
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "pistachio",
        "hazelnut",
        "macadamia",
    },
    "fish": {
        "fish",
        "anchovy",
        "anchovies",
        "tuna",
        "salmon",
        "cod",
    },
    "shellfish": {
        "shellfish",
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "mussel",
        "clam",
        "oyster",
    },
    "soy": {
        "soy",
        "soya",
        "soybean",
    },
    "wheat": {
        "wheat",
        "gluten",
        "barley",
        "rye",
    },
}


_ACCESSORY_TERMS = {
    "case",
    "box",
    "holder",
    "storage",
    "display",
    "stand",
    "strap",
    "cover",
    "charger",
    "cable",
    "adapter",
    "replacement",
    "spare",
    "battery",
    "protective",
    "organizer",
    "topper",
    "knife",
    "turning table",
}


def _norm(
    value: str,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value).strip().lower(),
    )


def _text(
    fact: ProductFacts,
) -> str:
    return _norm(
        fact.searchable_text()
    )


def _phrase_match(
    text: str,
    phrase: str,
) -> bool:
    phrase_n = _norm(phrase)

    if not phrase_n:
        return True

    if phrase_n in text:
        return True

    tokens = [
        token
        for token in phrase_n.split()
        if token
    ]

    if len(tokens) == 1:
        return bool(
            re.search(
                rf"(?<![a-z0-9])"
                rf"{re.escape(tokens[0])}"
                rf"(?![a-z0-9])",
                text,
            )
        )

    text_tokens = text.split()

    positions: dict[str, list[int]] = {}

    for index, token in enumerate(
        text_tokens
    ):
        positions.setdefault(
            token,
            [],
        ).append(index)

    starts = positions.get(
        tokens[0],
        [],
    )

    for start in starts:
        window = text_tokens[
            start:start + 8
        ]

        if all(
            token in window
            for token in tokens
        ):
            return True

    return False


def _terms(
    term: str,
) -> set[str]:
    normalized = _norm(term)

    return _SYN.get(
        normalized,
        {normalized},
    )


def _raw_product_texts(
    fact: ProductFacts,
) -> list[str]:
    return [
        fact.name,
        fact.description,
        fact.safety_text,
        fact.category or "",
        fact.vendor or "",
    ]


def _exclusion_status(
    fact: ProductFacts,
    term: str,
    *,
    safety_critical: bool = False,
) -> tuple[str, str | None]:
    normalized = _norm(term)

    # --------------------------------------------------------------
    # ALLERGY / SAFETY-CRITICAL PATH
    # --------------------------------------------------------------
    #
    # The experiment requested for this test is:
    #
    #   - allergen mentioned in product name/description -> reject
    #   - allergen explicitly negated -> allow
    #   - allergen absent from name/description -> allow
    #
    # Therefore the allergy path deliberately bypasses the cached generic
    # safety flag. The generic flag may have been produced from safety_text,
    # category, vendor, or other metadata and may therefore be "unknown" even
    # when the current experiment says that absence is sufficient.
    if safety_critical:
        return analyze_allergy_exclusion(
            normalized,
            product_name=fact.name,
            product_description=fact.description,
        )

    # --------------------------------------------------------------
    # NORMAL / NON-ALLERGY PATH
    # --------------------------------------------------------------
    #
    # Keep the existing behavior unchanged for ordinary exclusions/dislikes.
    cached = fact.safety_flags.get(
        normalized
    )

    if cached in {
        "present",
        "not_found",
        "unknown",
    }:
        return (
            cached,
            fact.safety_evidence.get(
                normalized
            ),
        )

    return analyze_exclusion(
        normalized,
        _raw_product_texts(fact),
    )


def _contains_exclusion(
    fact: ProductFacts,
    term: str,
) -> bool:
    status, _ = _exclusion_status(
        fact,
        term,
    )

    return status == "present"


def _singular_type(
    value: str | None,
) -> str | None:
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

        if (
            word.endswith("ies")
            and len(word) > 3
        ):
            word = (
                word[:-3]
                + "y"
            )

        elif (
            word.endswith("ses")
            and len(word) > 3
        ):
            word = word[:-2]

        elif (
            word.endswith("es")
            and len(word) > 2
        ):
            word = word[:-2]

        elif (
            word.endswith("s")
            and not word.endswith("ss")
            and len(word) > 1
        ):
            word = word[:-1]

        return word

    last = words[-1]

    if (
        last.endswith("ies")
        and len(last) > 3
    ):
        words[-1] = (
            last[:-3]
            + "y"
        )

    elif (
        last.endswith("ses")
        and len(last) > 3
    ):
        words[-1] = last[:-2]

    elif (
        last.endswith("es")
        and len(last) > 2
    ):
        words[-1] = last[:-2]

    elif (
        last.endswith("s")
        and not last.endswith("ss")
        and len(last) > 1
    ):
        words[-1] = last[:-1]

    return " ".join(words)


def _role_is_explicitly_incompatible(
    fact: ProductFacts,
    requested_role: str,
) -> bool:
    if requested_role != "primary_product":
        return False

    return fact.item_role in {
        "accessory",
        "component",
        "replacement",
        "service",
    }


def _legacy_family_match_exact(
    fact: ProductFacts,
    requested: str | None,
) -> bool:
    """
    Exact fallback implementation of the previous working family matcher.

    This intentionally preserves the old behavior rather than adding another
    semantic guard. It is used only after strict semantic canonicalization does
    not produce an exact match.
    """
    if not requested:
        return True

    req = _singular_type(
        requested
    )

    if not req:
        return True

    if req in {
        "gift",
        "gifts",
        "present",
        "presents",
        "item",
        "product",
    }:
        return True

    family = _norm(
        fact.product_family or ""
    )

    text = _text(fact)

    if family and (
        _phrase_match(
            family,
            req,
        )
        or _phrase_match(
            req,
            family,
        )
    ):
        return True

    req_tokens = [
        token
        for token in req.split()
        if len(token) > 1
    ]

    if not req_tokens:
        return True

    if all(
        _phrase_match(
            text,
            token.rstrip("s"),
        )
        or _phrase_match(
            text,
            token,
        )
        for token in req_tokens
    ):
        accessory = (
            set(text.split())
            & _ACCESSORY_TERMS
        )

        if (
            len(req_tokens) == 1
            and accessory
            and req not in accessory
        ):
            return False

        return True

    return False


def _semantic_product_match(
    fact: ProductFacts,
    spec: ShoppingSpec,
) -> bool:
    """
    Validate product identity before ranking or soft preferences.

    Strategy:
      1. Use the new semantic canonical fields when they agree exactly.
      2. When canonicalization differs, fall back to the exact previous
         working family/name matcher.

    The fallback is deliberately the old implementation so valid products
    such as "chicken sausage" or "anniversary gift pack" are not rejected
    merely because the LLM chose a more specific canonical label.
    """
    requested = _singular_type(
        spec.canonical_product_type
        or spec.product_family
    )

    if not requested:
        return True

    if requested in {
        "gift",
        "gifts",
        "present",
        "presents",
        "item",
        "product",
    }:
        return True

    actual = _singular_type(
        fact.canonical_product_type
    )

    # --------------------------------------------------------------
    # LEVEL 1: strict semantic match
    # --------------------------------------------------------------
    if actual == requested:
        # Preserve the protection introduced by the new semantic matcher when
        # the semantic identities actually agree.
        if _role_is_explicitly_incompatible(
            fact,
            spec.item_role,
        ):
            return False

        if (
            spec.item_role
            in {
                "accessory",
                "component",
                "replacement",
            }
            and fact.item_role
            not in {
                spec.item_role,
                "unknown",
            }
        ):
            return False

        if (
            spec.item_role == "bundle"
            and fact.item_role
            not in {
                "bundle",
                "unknown",
            }
        ):
            return False

        return True

    # --------------------------------------------------------------
    # LEVEL 2: exact previous working matcher
    # --------------------------------------------------------------
    return _legacy_family_match_exact(
        fact,
        requested,
    )


def _family_match(
    fact: ProductFacts,
    requested: str | None,
) -> bool:
    """Backward-compatible strict product-family helper."""
    if not requested:
        return True

    req = _singular_type(
        requested
    )

    if not req:
        return True

    class _SpecShim:
        canonical_product_type = req
        product_family = req
        item_role = "primary_product"

    return _semantic_product_match(
        fact,
        _SpecShim(),
    )


def _quantity_to_base(
    value: float,
    unit: str,
) -> tuple[float, str]:
    unit = _norm(unit)

    if unit == "kg":
        return (
            value * 1000,
            "g",
        )

    if unit == "g":
        return (
            value,
            "g",
        )

    if unit == "lb":
        return (
            value * 453.59237,
            "g",
        )

    if unit == "oz":
        return (
            value * 28.349523125,
            "g",
        )

    if unit == "l":
        return (
            value * 1000,
            "ml",
        )

    if unit == "ml":
        return (
            value,
            "ml",
        )

    return (
        value,
        unit,
    )


def _quantity_matches(
    actual: ProductFacts,
    requested: QuantityConstraint,
) -> tuple[bool, str]:
    if actual.package_quantity is None:
        return (
            False,
            "package_size_unknown",
        )

    actual_value, actual_unit = (
        _quantity_to_base(
            actual.package_quantity.value,
            actual.package_quantity.unit,
        )
    )

    requested_value, requested_unit = (
        _quantity_to_base(
            requested.value,
            requested.unit,
        )
    )

    if actual_unit != requested_unit:
        return (
            False,
            "package_unit_mismatch",
        )

    tolerance = max(
        abs(requested_value)
        * 0.005,
        0.01,
    )

    if (
        requested.mode == "exact"
        and abs(
            actual_value
            - requested_value
        ) > tolerance
    ):
        return (
            False,
            "package_size_mismatch",
        )

    if (
        requested.mode == "at_least"
        and actual_value + tolerance
        < requested_value
    ):
        return (
            False,
            "package_size_below_requirement",
        )

    if (
        requested.mode == "at_most"
        and actual_value - tolerance
        > requested_value
    ):
        return (
            False,
            "package_size_above_requirement",
        )

    return (
        True,
        "package_size_match",
    )


def evaluate_product(
    fact: ProductFacts,
    spec: ShoppingSpec,
) -> EligibilityResult:
    failures: list[str] = []
    matched: list[str] = []

    if (
        spec.product_family
        or spec.canonical_product_type
    ):
        if _semantic_product_match(
            fact,
            spec,
        ):
            matched.append(
                "product_family"
            )
        else:
            failures.append(
                "product_type_mismatch"
            )

    if spec.budget:
        if (
            _norm(spec.budget.currency)
            != _norm(fact.currency)
        ):
            failures.append(
                "budget_currency_mismatch"
            )

        elif (
            spec.budget.max_price is not None
            and fact.price
            > spec.budget.max_price
        ):
            failures.append(
                "over_maximum_budget"
            )

        elif (
            spec.budget.min_price is not None
            and fact.price
            < spec.budget.min_price
        ):
            failures.append(
                "below_minimum_budget"
            )

        else:
            matched.append(
                "budget"
            )

    if (
        spec.in_stock_only
        and fact.in_stock is False
    ):
        failures.append(
            "out_of_stock"
        )
    elif spec.in_stock_only:
        matched.append(
            "stock"
        )

    for constraint in (
        spec.required_attributes
    ):
        if _phrase_match(
            _text(fact),
            constraint.term,
        ):
            matched.append(
                f"required:{constraint.term}"
            )
        else:
            failures.append(
                "required_attribute_missing:"
                + constraint.term
            )

    for constraint in (
        spec.excluded_attributes
    ):
        status, _ = _exclusion_status(
            fact,
            constraint.term,
            safety_critical=constraint.safety_critical,
        )

        if status == "present":
            failures.append(
                "excluded_term_present:"
                + constraint.term
            )
        elif (
            constraint.safety_critical
            and status == "unknown"
        ):
            failures.append(
                "safety_evidence_unknown:"
                + constraint.term
            )
        else:
            matched.append(
                "excluded_term_absent:"
                + constraint.term
            )

    normalized_components = {
        _norm(value)
        for value in fact.components
    }

    for component in (
        spec.required_components
    ):
        if (
            _norm(component)
            in normalized_components
            or _phrase_match(
                _text(fact),
                component,
            )
        ):
            matched.append(
                f"component:{component}"
            )
        else:
            failures.append(
                "required_component_missing:"
                + component
            )

    for component in (
        spec.excluded_components
    ):
        status, _ = _exclusion_status(
            fact,
            component,
        )

        if (
            status == "present"
            or _norm(component)
            in normalized_components
        ):
            failures.append(
                "excluded_component_present:"
                + component
            )

    if spec.quantity:
        ok, reason = _quantity_matches(
            fact,
            spec.quantity,
        )

        if ok:
            matched.append(
                reason
            )
        else:
            failures.append(
                reason
            )

    return EligibilityResult(
        product_id=fact.product_id,
        eligible=not failures,
        hard_failures=failures,
        matched_constraints=matched,
    )


def evaluate_all(
    products: Iterable[ProductFacts],
    spec: ShoppingSpec,
) -> list[EligibilityResult]:
    return [
        evaluate_product(
            product,
            spec,
        )
        for product in products
    ]