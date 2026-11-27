"""Universal recipient/reference resolution for durable memory."""

from __future__ import annotations

import re
from typing import Any

from backend.db.repositories.profile_repo import (
    find_profile_by_name,
    list_profiles,
)
from backend.memory import short_term


PRONOUNS = {
    "he",
    "him",
    "his",
    "she",
    "her",
    "hers",
    "they",
    "them",
    "their",
    "theirs",
}

INVALID_RECIPIENT_NAMES = {
    *PRONOUNS,
    "someone",
    "somebody",
    "person",
    "user",
    "customer",
    "recipient",
    "the person",
    "the user",
    "the recipient",
}

RELATIONSHIP_ALIASES = {
    "dad": "father",
    "my dad": "father",
    "father": "father",
    "my father": "father",

    "mom": "mother",
    "mum": "mother",
    "my mom": "mother",
    "my mum": "mother",
    "mother": "mother",
    "my mother": "mother",

    "wife": "wife",
    "my wife": "wife",

    "husband": "husband",
    "my husband": "husband",

    "brother": "brother",
    "my brother": "brother",

    "sister": "sister",
    "my sister": "sister",

    "son": "son",
    "my son": "son",

    "daughter": "daughter",
    "my daughter": "daughter",

    "friend": "friend",
    "my friend": "friend",

    "girlfriend": "girlfriend",
    "my girlfriend": "girlfriend",

    "boyfriend": "boyfriend",
    "my boyfriend": "boyfriend",

    "grandfather": "grandfather",
    "grandpa": "grandfather",
    "my grandfather": "grandfather",
    "my grandpa": "grandfather",

    "grandmother": "grandmother",
    "grandma": "grandmother",
    "my grandmother": "grandmother",
    "my grandma": "grandmother",

    "uncle": "uncle",
    "my uncle": "uncle",

    "aunt": "aunt",
    "my aunt": "aunt",

    "cousin": "cousin",
    "my cousin": "cousin",
}


def normalize_reference(
    value: str,
) -> str:
    return " ".join(
        value.strip().lower().split()
    )


def canonical_relationship(
    value: str,
) -> str | None:
    normalized = normalize_reference(
        value
    )

    return RELATIONSHIP_ALIASES.get(
        normalized
    )


def is_invalid_recipient_name(
    value: str,
) -> bool:
    """
    Reject pronouns and conversational fragments.

    Examples:
        him
        her
        but she
        but he
        for him
        for her
        what are the best watches
    """

    normalized = normalize_reference(
        value
    )

    if not normalized:
        return True

    if normalized in INVALID_RECIPIENT_NAMES:
        return True

    # Conversational fragments must never become profile names.
    fragment_patterns = (
        r"^(?:but|however|while)\s+"
        r"(?:he|him|his|she|her|hers|they|them|their|theirs)\b",
        r"^(?:for|about|with)\s+"
        r"(?:him|her|them)\b",
        r"^(?:what|which|where|when|how|why)\b",
        r"\bwhat\s+are\s+the\b",
        r"\bwhat\s+is\s+the\b",
        r"\bshow\s+me\b",
        r"\bfind\s+me\b",
    )

    return any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in fragment_patterns
    )


def _profile_aliases(
    profile: Any,
) -> set[str]:
    aliases = {
        normalize_reference(
            profile.name
        )
    }

    if profile.relationship:
        aliases.add(
            normalize_reference(
                profile.relationship
            )
        )

    canonical = canonical_relationship(
        profile.name
    )

    if canonical:
        aliases.add(
            canonical
        )

    if profile.relationship:
        canonical_relationship_name = (
            canonical_relationship(
                profile.relationship
            )
        )

        if canonical_relationship_name:
            aliases.add(
                canonical_relationship_name
            )

    return {
        alias
        for alias in aliases
        if alias
    }


async def resolve_recipient_reference(
    *,
    user_id: str,
    session_id: str,
    raw_name: str | None,
) -> str | None:
    """
    Resolve:

        father
        my father
        dad
        him
        her
        them

    against durable user-owned profiles and conversation context.
    """

    if not raw_name:
        return None

    normalized = normalize_reference(
        raw_name
    )

    if is_invalid_recipient_name(
        normalized
    ):
        context = ""

        try:
            context = (
                await short_term.get_context(
                    session_id
                )
            )
        except Exception:
            pass

        profiles = await list_profiles(
            user_id
        )

        valid_profiles = [
            profile
            for profile in profiles
            if not is_invalid_recipient_name(
                profile.name
            )
        ]

        # Safest case: exactly one known recipient.
        if (
            normalized in PRONOUNS
            and len(valid_profiles) == 1
        ):
            return valid_profiles[0].name

        context_normalized = normalize_reference(
            context
        )

        best_profile = None
        best_position = -1

        for profile in valid_profiles:
            for alias in _profile_aliases(
                profile
            ):
                position = (
                    context_normalized.rfind(
                        alias
                    )
                )

                if position > best_position:
                    best_position = position
                    best_profile = profile

        if best_profile is not None:
            return best_profile.name

        return None

    # Exact durable profile match.
    existing = await find_profile_by_name(
        user_id=user_id,
        name=normalized,
    )

    if existing is not None:
        return existing.name

    # Relationship alias.
    canonical = canonical_relationship(
        normalized
    )

    if canonical:
        existing = (
            await find_profile_by_name(
                user_id=user_id,
                name=canonical,
            )
        )

        if existing is not None:
            return existing.name

        return canonical

    return normalized


def valid_profiles_only(
    profiles: list[Any],
) -> list[Any]:
    """Remove invalid placeholder/conversational profiles."""

    return [
        profile
        for profile in profiles
        if not is_invalid_recipient_name(
            profile.name
        )
    ]