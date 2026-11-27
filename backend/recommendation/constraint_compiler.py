"""Merge current-turn requirements with durable recipient memory."""

from __future__ import annotations

import re

from backend.recommendation.models import (
    AttributeConstraint,
    RecipientContext,
    ShoppingSpec,
)


_MALE_RELATIONSHIPS = {
    "father",
    "dad",
    "husband",
    "brother",
    "son",
    "boyfriend",
    "grandfather",
    "grandpa",
    "uncle",
    "nephew",
    "father in law",
}

_FEMALE_RELATIONSHIPS = {
    "mother",
    "mom",
    "mum",
    "wife",
    "sister",
    "daughter",
    "girlfriend",
    "grandmother",
    "grandma",
    "aunt",
    "niece",
    "mother in law",
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def infer_gender_hint(relationship: str | None) -> str:
    if not relationship:
        return "unknown"

    value = _norm(relationship)

    if value in _MALE_RELATIONSHIPS:
        return "male"

    if value in _FEMALE_RELATIONSHIPS:
        return "female"

    return "unknown"


def compile_spec(
    spec: ShoppingSpec,
    recipient: RecipientContext | None,
) -> ShoppingSpec:
    """Apply durable preferences without contaminating current financial state."""

    if recipient is None:
        return spec

    spec.recipient_name = recipient.name or spec.recipient_name

    # Durable likes are SOFT.
    existing_soft = {_norm(value) for value in spec.preferred_attributes}

    for like in recipient.likes:
        value = _norm(like)

        if value and value not in existing_soft:
            spec.preferred_attributes.append(value)
            existing_soft.add(value)

    # Allergies/dislikes are HARD.
    existing_by_term = {
        _norm(item.term): item
        for item in spec.excluded_attributes
        if item.term
    }

    for term in recipient.allergies:
        value = _norm(term)

        if not value:
            continue

        existing = existing_by_term.get(value)
        if existing is not None:
            # An allergy from durable memory upgrades an already-parsed
            # current-turn exclusion to a fail-closed safety constraint.
            existing.safety_critical = True
            continue

        constraint = AttributeConstraint(
            term=value,
            polarity="exclude",
            source="durable_profile",
            safety_critical=True,
        )
        spec.excluded_attributes.append(constraint)
        existing_by_term[value] = constraint

    for term in recipient.dislikes:
        value = _norm(term)

        if not value or value in existing_by_term:
            continue

        constraint = AttributeConstraint(
            term=value,
            polarity="exclude",
            source="durable_profile",
            safety_critical=False,
        )
        spec.excluded_attributes.append(constraint)
        existing_by_term[value] = constraint

    # Budget is deliberately untouched.
    # It belongs only to the current turn.

    return spec


def build_recipient_context(
    *,
    name: str | None,
    relationship: str | None,
    likes: list[str] | None = None,
    dislikes: list[str] | None = None,
    allergies: list[str] | None = None,
) -> RecipientContext:
    return RecipientContext(
        name=name,
        relationship=relationship,
        likes=list(likes or []),
        dislikes=list(dislikes or []),
        allergies=list(allergies or []),
        gender_hint=infer_gender_hint(
            relationship or name
        ),
    )