"""Shared durable-memory rules.

This module contains utilities that must be importable by both the memory
agent and structured memory layer without creating circular imports.
"""

from __future__ import annotations

import re


TEMPORARY_BUDGET_PATTERNS = (
    r"\b(?:lkr|rs|rs\.|rupees|₨|\$|usd|eur|gbp)\s*"
    r"\d[\d,]*(?:\.\d+)?\b",

    r"\b\d[\d,]*(?:\.\d+)?\s*"
    r"(?:lkr|rs|rs\.|rupees|₨|usd|eur|gbp)\b",

    r"\b(?:my\s+)?budget\b",

    r"\bmaximum\s+(?:budget|amount|price|spend|spending)\b",

    r"\bmax(?:imum)?\s+(?:budget|price|amount|spend|spending)\b",

    r"\bminimum\s+(?:budget|amount|price|spend|spending)\b",

    r"\bmin(?:imum)?\s+(?:budget|price|amount|spend|spending)\b",

    r"\b(?:i\s+can|i\s+could|i\s+am\s+willing\s+to)\s+spend\b",

    r"\b(?:i\s+want|i\s+need)\s+to\s+spend\b",

    r"\b(?:can|could)\s+spend\b",

    r"\bspending\s+limit\b",

    r"\bprice\s+limit\b",

    r"\bfinancial\s+limit\b",

    r"\bfinancial\s+status\b",

    r"\bwithin\s+(?:my|the)\s+(?:budget|price|spending)\b",

    r"\bunder\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bbelow\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bup\s+to\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bat\s+most\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bno\s+more\s+than\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bless\s+than\s+(?:rs\.?|lkr|₨|\$)?\s*\d",

    r"\bbetween\s+(?:rs\.?|lkr|₨|\$)?\s*\d"
    r"\s+and\s+(?:rs\.?|lkr|₨|\$)?\s*\d",
)


def normalize_memory_text(
    value: str,
) -> str:
    """Normalize whitespace and casing for memory-rule checks."""

    return " ".join(
        str(value)
        .strip()
        .lower()
        .split()
    )


def is_temporary_budget_value(
    value: str,
) -> bool:
    """
    Return True when a piece of text contains a temporary financial or
    budget constraint.

    This must be checked before writing durable memory.
    """

    normalized = normalize_memory_text(
        value
    )

    if not normalized:
        return False

    return any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in TEMPORARY_BUDGET_PATTERNS
    )


def sanitize_memory_value(
    value: str,
) -> str | None:
    """
    Clean a prospective durable-memory value.

    Temporary financial information and obvious conversational fragments
    are rejected.
    """

    clean = " ".join(
        str(value)
        .strip()
        .split()
    )

    if not clean:
        return None

    if is_temporary_budget_value(
        clean
    ):
        return None

    cutoff_patterns = (
        r"\s*,?\s+\bfor\s+(?:him|her|them)\b",

        r"\s*,?\s+\bwhat\s+are\s+the\b",

        r"\s*,?\s+\bwhat\s+is\s+the\b",

        r"\s*,?\s+\bwhich\s+are\s+the\b",

        r"\s*,?\s+\bshow\s+me\b",

        r"\s*,?\s+\bfind\s+me\b",

        r"\s*,?\s+\bcan\s+you\s+(?:show|find|recommend)\b",

        r"\s*,?\s+\bplease\s+(?:show|find|recommend)\b",
    )

    for pattern in cutoff_patterns:
        match = re.search(
            pattern,
            clean,
            re.IGNORECASE,
        )

        if match:
            clean = clean[
                :match.start()
            ].strip(
                " ,.;:!?-"
            )

    if not clean:
        return None

    fragment_patterns = (
        r"\bbut\s+(?:he|him|his|she|her|hers|they|them|their|theirs)\b",

        r"\bhowever\s+(?:he|she|they)\b",

        r"\b(?:he|him|his|she|her|hers|they|them|their|theirs)\s+"
        r"(?:love|loves|like|likes|prefer|prefers|hate|hates|"
        r"dislike|dislikes)\b",
    )

    if any(
        re.search(
            pattern,
            clean,
            re.IGNORECASE,
        )
        for pattern in fragment_patterns
    ):
        return None

    return clean