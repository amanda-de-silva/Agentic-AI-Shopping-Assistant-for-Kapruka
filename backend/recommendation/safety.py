"""Deterministic text-based exclusion/safety evidence analysis.

The recommendation pipeline uses this module for two different kinds of
exclusions:

1. Generic hard exclusions (for example, a normal product dislike). These use
   the richer evidence policy implemented by :func:`analyze_exclusion`.
2. Safety-critical allergy exclusions. For the current experimental policy,
   an allergy is considered *safe to recommend* when none of its known
   allergen terms are mentioned in the product name or description. A direct
   positive mention still rejects the product, while explicit negation such as
   ``nut-free`` is treated as safe.

The allergy-specific rule intentionally does NOT inspect category, vendor, or
other metadata. This keeps this experiment isolated from the working
non-allergy recommendation path.
"""
from __future__ import annotations

import re
from typing import Iterable


SAFETY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "peanut": (
        "peanuts",
        "groundnut",
        "groundnuts",
    ),
    "milk": (
        "dairy",
        "casein",
        "whey",
        "lactose",
    ),
    "egg": (
        "eggs",
        "albumin",
        "mayonnaise",
    ),
    "tree nut": (
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "pistachio",
        "hazelnut",
        "macadamia",
        "tree nuts",
    ),
    "nut": (
        "nuts",
        "tree nut",
        "tree nuts",
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "pistachio",
        "hazelnut",
        "macadamia",
    ),
    "chocolate": (
        "chocolates",
        "cocoa",
        "cocoa powder",
        "cacao",
        "cacao powder",
    ),
    "fish": (
        "anchovy",
        "anchovies",
        "tuna",
        "salmon",
        "cod",
    ),
    "shellfish": (
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "mussel",
        "clam",
        "oyster",
    ),
    "soy": (
        "soya",
        "soybean",
    ),
    "wheat": (
        "gluten",
        "barley",
        "rye",
    ),
}


_CANONICAL_TERM_ALIASES: dict[str, str] = {
    "peanuts": "peanut",
    "groundnut": "peanut",
    "groundnuts": "peanut",
    "dairy": "milk",
    "casein": "milk",
    "whey": "milk",
    "lactose": "milk",
    "eggs": "egg",
    "almond": "tree nut",
    "almonds": "tree nut",
    "cashew": "tree nut",
    "cashews": "tree nut",
    "walnut": "tree nut",
    "walnuts": "tree nut",
    "pecan": "tree nut",
    "pecans": "tree nut",
    "pistachio": "tree nut",
    "pistachios": "tree nut",
    "hazelnut": "tree nut",
    "hazelnuts": "tree nut",
    "macadamia": "tree nut",
    "macadamias": "tree nut",
    "nuts": "nut",
    "tree nuts": "tree nut",
    "chocolates": "chocolate",
    "cocoa": "chocolate",
    "cocoa powder": "chocolate",
    "cacao": "chocolate",
    "cacao powder": "chocolate",
    "soya": "soy",
    "soybean": "soy",
    "gluten": "wheat",
    "barley": "wheat",
    "rye": "wheat",
}


# Common morphology forms that encode explicit absence without using a
# separate negation word. These are treated as negative evidence.
_LESS_FORMS: dict[str, tuple[str, ...]] = {
    "egg": ("eggless",),
    "milk": ("milkless", "dairyless"),
    "dairy": ("dairyless",),
    "sugar": ("sugarless",),
}


_PREFIX_NEGATION_RE = re.compile(
    r"(?:"
    r"\bno\b|"
    r"\bwithout\b|"
    r"\bfree\s+from\b|"
    r"\bfree\s+of\b|"
    r"\bcontains?\s+no\b|"
    r"\bdoes\s+not\s+contain\b|"
    r"\bdoesn['’]t\s+contain\b|"
    r"\bdoes\s+not\s+include\b|"
    r"\bdoesn['’]t\s+include\b|"
    r"\bmade\s+without\b|"
    r"\bno\s+added\b"
    r")\s+(?:[a-z0-9'’&/+-]+\s+){0,3}$",
    re.IGNORECASE,
)


_STRUCTURED_EVIDENCE_RE = re.compile(
    r"\b(?:ingredients?|allergens?|allergy\s+information|"
    r"ingredient\s+list|contains)\s*[:\-]",
    re.IGNORECASE,
)


def normalize_term(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text)


def canonicalize_term(value: str) -> str:
    """Map common surface forms onto the canonical safety term."""
    normalized = normalize_term(value)
    return _CANONICAL_TERM_ALIASES.get(
        normalized,
        normalized,
    )


def expanded_terms(term: str) -> list[str]:
    normalized = canonicalize_term(term)
    if not normalized:
        return []

    values = [normalized]
    values.extend(SAFETY_SYNONYMS.get(normalized, ()))

    # A canonical two-word term can also appear with a hyphen.
    if " " in normalized:
        values.append(normalized.replace(" ", "-"))

    # Singular/plural variants are handled conservatively in both directions.
    # This is important for allergies such as ``nut`` where the product may
    # mention ``almonds`` rather than the canonical term ``nut``.
    for value in list(values):
        if value.endswith("ies") and len(value) > 3:
            values.append(value[:-3] + "y")
        elif value.endswith("s") and not value.endswith("ss") and len(value) > 1:
            values.append(value[:-1])
        elif value.endswith("y") and len(value) > 2:
            values.append(value[:-1] + "ies")
        elif value.endswith(("x", "ch", "sh")):
            values.append(value + "es")
        elif not value.endswith("s"):
            values.append(value + "s")

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = normalize_term(value)
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)

    return deduped


def _term_pattern(term: str) -> re.Pattern[str]:
    tokens = normalize_term(term).split()
    if not tokens:
        return re.compile(r"(?!x)x")

    if len(tokens) == 1:
        body = re.escape(tokens[0])
    else:
        body = r"\s+".join(re.escape(token) for token in tokens)

    return re.compile(
        rf"(?<![a-z0-9]){body}(?![a-z0-9])",
        re.IGNORECASE,
    )


def _compound_negated(
    text: str,
    start: int,
    end: int,
    term: str,
) -> bool:
    before = text[max(0, start - 3):start].rstrip()
    after = text[end:min(len(text), end + 12)]

    # chocolate-free / chocolate free / tree-nut-free
    if re.match(
        r"^-?\s*(?:free|free-from|free-of)\b",
        after,
        re.IGNORECASE,
    ):
        return True

    if re.search(
        r"(?:^|-)free$",
        before,
        re.IGNORECASE,
    ):
        return True

    normalized = normalize_term(term)
    compact = normalized.replace(" ", "[- ]")

    if re.search(
        rf"(?<![a-z0-9]){compact}\s*-?free\b",
        text[max(0, start - 2):min(len(text), end + 14)],
        re.IGNORECASE,
    ):
        return True

    if normalized and re.search(
        rf"(?<![a-z0-9]){re.escape(normalized)}less\b",
        text[max(0, start - 2):min(len(text), end + 12)],
        re.IGNORECASE,
    ):
        return True

    return False


def _prefix_negated(
    text: str,
    start: int,
) -> bool:
    window_start = max(0, start - 72)
    prefix = text[window_start:start]
    prefix = re.sub(r"\s+", " ", prefix)

    return bool(
        _PREFIX_NEGATION_RE.search(prefix)
    )


def is_negated_occurrence(
    text: str,
    start: int,
    end: int,
    term: str,
) -> bool:
    return (
        _prefix_negated(
            text,
            start,
        )
        or _compound_negated(
            text,
            start,
            end,
            term,
        )
    )


def _sentence(
    text: str,
    start: int,
    end: int,
) -> str:
    left = max(
        text.rfind(".", 0, start),
        text.rfind("\n", 0, start),
        text.rfind(";", 0, start),
    )

    right_candidates = [
        index
        for index in (
            text.find(".", end),
            text.find("\n", end),
            text.find(";", end),
        )
        if index >= 0
    ]

    right = (
        min(right_candidates)
        if right_candidates
        else len(text)
    )

    return text[left + 1:right].strip()


def _analyze_terms(
    term: str,
    texts: Iterable[str],
) -> tuple[str, str | None]:
    """Analyze one exclusion against supplied source texts."""
    aliases = expanded_terms(term)

    if not aliases:
        return "unknown", None

    saw_occurrence = False
    saw_negated = False
    positive_evidence: str | None = None
    negated_evidence: str | None = None
    structured_evidence = False

    for raw_text in texts:
        text = str(raw_text or "")

        if not text:
            continue

        normalized = text.replace(
            "’",
            "'",
        )

        if _STRUCTURED_EVIDENCE_RE.search(
            normalized
        ):
            structured_evidence = True

        for alias in aliases:
            for negative_form in _LESS_FORMS.get(
                alias,
                (),
            ):
                negative_pattern = _term_pattern(
                    negative_form
                )

                for match in negative_pattern.finditer(
                    normalized
                ):
                    saw_occurrence = True
                    saw_negated = True

                    if negated_evidence is None:
                        negated_evidence = _sentence(
                            normalized,
                            match.start(),
                            match.end(),
                        )[:240]

            pattern = _term_pattern(alias)

            for match in pattern.finditer(
                normalized
            ):
                saw_occurrence = True

                snippet = _sentence(
                    normalized,
                    match.start(),
                    match.end(),
                )[:240]

                if is_negated_occurrence(
                    normalized,
                    match.start(),
                    match.end(),
                    alias,
                ):
                    saw_negated = True

                    if negated_evidence is None:
                        negated_evidence = snippet
                else:
                    positive_evidence = snippet
                    break

            if positive_evidence is not None:
                break

        if positive_evidence is not None:
            break

    if positive_evidence is not None:
        return (
            "present",
            positive_evidence,
        )

    if saw_occurrence and saw_negated:
        return (
            "not_found",
            negated_evidence,
        )

    if structured_evidence:
        return (
            "not_found",
            "structured ingredient/allergen information",
        )

    return (
        "unknown",
        None,
    )


def analyze_exclusion(
    term: str,
    texts: Iterable[str],
) -> tuple[str, str | None]:
    """Return ``(status, evidence)`` for one generic excluded term.

    Status is one of ``present``, ``not_found`` or ``unknown``.

    Generic exclusions retain the richer evidence policy: if the supplied
    text does not contain enough evidence, the result can remain ``unknown``.
    """
    return _analyze_terms(
        term,
        texts,
    )


def analyze_allergy_exclusion(
    term: str,
    *,
    product_name: str,
    product_description: str,
) -> tuple[str, str | None]:
    """Analyze a safety-critical allergy using the experimental policy.

    Policy for this experiment:

    - If the allergy material (or one of its known synonyms) is mentioned in
      the product name/description as a positive occurrence: ``present``.
    - If it appears only in an explicit negative expression such as
      ``chocolate-free`` or ``without nuts``: ``not_found``.
    - If it is not mentioned at all in the product name or description:
      ``not_found`` as requested, meaning the product is allowed.

    Category, vendor, structured metadata, and LLM-generated attributes are
    intentionally ignored here so the experiment affects only the allergy
    decision and does not change normal recommendation behavior.
    """
    status, evidence = _analyze_terms(
        term,
        [
            product_name,
            product_description,
        ],
    )

    # Experimental policy: silence in both the name and description counts as
    # safe/allowed. ``present`` still rejects the product, while an explicit
    # negated occurrence is also safe.
    if status == "unknown":
        return (
            "not_found",
            "allergy material not mentioned in name/description",
        )

    return (
        status,
        evidence,
    )