"""
Durable user memory extraction and retrieval.

Important design rules:

1. Recipient facts are durable and belong to users.
2. Temporary shopping constraints such as budgets are never stored.
3. Recommendation/request text must never accidentally become a
   durable preference.
4. When the memory LLM fails, deterministic extraction must still
   correctly extract facts from natural multi-intent sentences.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.core.logging import get_logger
from backend.llm.openrouter_client import get_openrouter_client
from backend.llm.prompt_loader import render_prompt
from backend.memory import (
    semantic_memory,
    short_term,
    structured_memory,
)
from backend.memory.memory_rules import (
    is_temporary_budget_value,
    sanitize_memory_value,
)
from backend.memory.recipient_resolution import (
    resolve_recipient_reference,
    valid_profiles_only,
)
from backend.observability.tracing import traced


logger = get_logger(__name__)


# ======================================================================
# MODELS
# ======================================================================

class MemoryUpdate(BaseModel):
    recipient_name: str = Field(
        min_length=1,
        max_length=100,
    )

    field: str = Field(
        min_length=1,
        max_length=50,
    )

    value: str = Field(
        min_length=1,
        max_length=500,
    )


class MemoryExtraction(BaseModel):
    updates: list[MemoryUpdate] = Field(
        default_factory=list
    )


# ======================================================================
# NORMALIZATION
# ======================================================================

INVALID_RECIPIENT_REFERENCES = {
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
    "someone",
    "somebody",
    "person",
    "user",
    "customer",
    "recipient",
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


FIELD_ALIASES = {
    "allergic": "allergies",
    "allergy": "allergies",
    "allergies": "allergies",

    "love": "likes",
    "loves": "likes",
    "like": "likes",
    "likes": "likes",
    "prefer": "likes",
    "prefers": "likes",
    "preference": "likes",
    "preferences": "likes",

    "dislike": "dislikes",
    "dislikes": "dislikes",
    "hate": "dislikes",
    "hates": "dislikes",
}


VALID_FIELDS = {
    "allergies",
    "likes",
    "dislikes",
    "notes",
}


def _normalize_text(
    value: str,
) -> str:
    return " ".join(
        str(value)
        .strip()
        .lower()
        .split()
    )


def _clean_whitespace(
    value: str,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value).strip(),
    )


def _canonical_recipient(
    value: str,
) -> str | None:

    normalized = _normalize_text(
        value
    )

    if not normalized:
        return None

    if normalized in INVALID_RECIPIENT_REFERENCES:
        return None

    return RELATIONSHIP_ALIASES.get(
        normalized,
        normalized,
    )


def _normalize_field(
    value: str,
) -> str | None:

    normalized = _normalize_text(
        value
    )

    return FIELD_ALIASES.get(
        normalized
    )


# ======================================================================
# TEMPORARY / NON-DURABLE CONTENT
# ======================================================================

def _looks_like_profile_recall_request(
    value: str,
) -> bool:
    """Return True for explicit requests to recall stored recipient memory.

    These requests do not contain a durable fact to extract, so they should
    bypass the memory-extraction LLM completely. Profile recall itself is
    handled by the recall_profile specialist after routing.
    """
    normalized = _normalize_text(value)

    if not normalized:
        return False

    patterns = (
        r"\bwhat\s+do\s+you\s+know\s+about\b",
        r"\bwhat\s+do\s+you\s+remember\s+about\b",
        r"\bwhat\s+information\s+do\s+you\s+have\s+about\b",
        r"\bwhat\s+do\s+you\s+have\s+on\b",
        r"\btell\s+me\s+about\s+my\b",
        r"\bdo\s+you\s+remember\s+my\b",
        r"\bwhat\s+can\s+you\s+tell\s+me\s+about\b",
    )

    if not any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in patterns
    ):
        return False

    # Require a recipient/object after the recall phrase so generic
    # questions are not accidentally classified as profile recall.
    recipient_patterns = (
        r"\babout\s+(?:my\s+)?[a-z][a-z\s'\-]{1,60}\b",
        r"\bon\s+(?:my\s+)?[a-z][a-z\s'\-]{1,60}\b",
        r"\bmy\s+(?:wife|husband|mother|mom|mum|father|dad|brother|sister|son|daughter|friend|girlfriend|boyfriend)\b",
    )

    return any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in recipient_patterns
    )


def _looks_like_request_text(
    value: str,
) -> bool:

    normalized = _normalize_text(
        value
    )

    if not normalized:
        return False

    request_patterns = (
        r"\bwhat\s+are\s+the\b",
        r"\bwhat\s+are\b",
        r"\bwhat\s+is\b",
        r"\bwhich\s+are\b",
        r"\bwhich\s+is\b",
        r"\bshow\s+me\b",
        r"\bfind\s+me\b",
        r"\brecommend\b",
        r"\brecommendations?\b",
        r"\bsuggest\b",
        r"\bsuggestions?\b",
        r"\boptions?\b",
        r"\bcan\s+you\b",
        r"\bcould\s+you\b",
        r"\bplease\b",
        r"\bi\s+want\s+to\b",
        r"\bi\s+need\s+to\b",
        r"\bi\s+would\s+like\s+to\b",
        r"\bi'?d\s+like\s+to\b",
        r"\bi\s+want\b",
        r"\bi\s+need\b",
        r"\bi\s+would\s+like\b",
        r"\blooking\s+for\b",
        r"\bplanning\s+to\b",
        r"\bplan\s+to\b",
        r"\bbuy\b",
        r"\bget\b",
        r"\bpurchase\b",
    )

    return any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in request_patterns
    )


def _looks_like_conversational_fragment(
    value: str,
) -> bool:

    normalized = _normalize_text(
        value
    )

    if not normalized:
        return False

    patterns = (
        r"\bbut\s+(?:he|him|his|she|her|hers|"
        r"they|them|their|theirs)\b",

        r"\bhowever\s+(?:he|she|they)\b",

        r"\b(?:he|him|his|she|her|hers|"
        r"they|them|their|theirs)\s+"
        r"(?:love|loves|like|likes|prefer|prefers|"
        r"hate|hates|dislike|dislikes)\b",
    )

    return any(
        re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )
        for pattern in patterns
    )


# ======================================================================
# FACT-VALUE CLEANING
# ======================================================================

# These phrases normally indicate that the user has moved from
# "telling us a fact" to "asking us to do something".
#
# Example:
#
# My mother loves cotton sarees. I want to buy a saree for her.
#
# We must keep:
#
# cotton sarees
#
# and discard:
#
# I want to buy a saree for her
#
# from durable memory.
REQUEST_BOUNDARY_PATTERNS = (
    r"\bwhat\s+are\s+the\b",
    r"\bwhat\s+are\b",
    r"\bwhat\s+is\b",
    r"\bwhich\s+are\b",
    r"\bwhich\s+is\b",
    r"\bshow\s+me\b",
    r"\bfind\s+me\b",
    r"\brecommend\b",
    r"\bsuggest\b",
    r"\bcan\s+you\b",
    r"\bcould\s+you\b",
    r"\bplease\b",
    r"\bi\s+want\s+to\b",
    r"\bi\s+need\s+to\b",
    r"\bi\s+would\s+like\s+to\b",
    r"\bi'?d\s+like\s+to\b",
    r"\bi\s+want\b",
    r"\bi\s+need\b",
    r"\bi\s+would\s+like\b",
    r"\bi'?m\s+looking\s+for\b",
    r"\blooking\s+for\b",
    r"\bplanning\s+to\b",
    r"\bplan\s+to\b",
)


def _truncate_at_request_boundary(
    value: str,
) -> str:

    clean = _clean_whitespace(
        value
    )

    if not clean:
        return ""

    earliest_index: int | None = None

    for pattern in REQUEST_BOUNDARY_PATTERNS:

        match = re.search(
            pattern,
            clean,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        if (
            earliest_index is None
            or match.start()
            < earliest_index
        ):
            earliest_index = (
                match.start()
            )

    if earliest_index is not None:
        clean = clean[
            :earliest_index
        ].strip()

    return clean.strip(
        " \t\r\n,.;:!?-"
    )


def _sanitize_fact_value(
    value: str,
) -> str | None:

    clean = _clean_whitespace(
        value
    )

    if not clean:
        return None

    # Remove sentence endings.
    clean = clean.strip(
        " \t\r\n,.;:!?\"'"
    )

    # Stop before an unrelated shopping/request clause.
    clean = _truncate_at_request_boundary(
        clean
    )

    if not clean:
        return None

    # Budget is never durable.
    if is_temporary_budget_value(
        clean
    ):
        return None

    if _looks_like_request_text(
        clean
    ):
        return None

    if _looks_like_conversational_fragment(
        clean
    ):
        return None

    return clean


def _split_fact_values(
    value: str,
) -> list[str]:

    clean = _sanitize_fact_value(
        value
    )

    if not clean:
        return []

    # Handle values such as:
    #
    # cotton sarees and batik sarees
    #
    # chocolate and vanilla
    #
    parts = re.split(
        r"\s*,\s*"
        r"|\s+\band\b\s+",
        clean,
        flags=re.IGNORECASE,
    )

    result: list[
        str
    ] = []

    for part in parts:

        part = _sanitize_fact_value(
            part
        )

        if not part:
            continue

        result.append(
            part
        )

    return list(
        dict.fromkeys(
            result
        )
    )


# ======================================================================
# SPLITTING A MULTI-INTENT MESSAGE
# ======================================================================

def _split_sentences(
    text: str,
) -> list[str]:

    """
    Split natural user input into independent sentence-like pieces.

    This is intentionally simple and robust rather than depending on an
    NLP package.

    Example:

        My mother loves cotton sarees.
        I want to buy a saree for her.
        What are the best options?

    becomes:

        My mother loves cotton sarees
        I want to buy a saree for her
        What are the best options
    """

    normalized = _clean_whitespace(
        text
    )

    if not normalized:
        return []

    pieces = re.split(
        r"(?<=[.!?])\s+",
        normalized,
    )

    return [
        piece.strip()
        for piece in pieces
        if piece.strip()
    ]


def _split_contrast_clauses(
    text: str,
) -> list[str]:

    clauses = re.split(
        r"\s*(?:,\s*)?\bbut\b\s+"
        r"|\s*(?:,\s*)?\bhowever\b\s+"
        r"|\s*(?:,\s*)?\bwhile\b\s+",
        text,
        flags=re.IGNORECASE,
    )

    return [
        clause.strip(
            " \t\r\n,.;:!?-"
        )
        for clause in clauses
        if clause.strip()
    ]


# ======================================================================
# INDIVIDUAL FACT PARSING
# ======================================================================

def _make_update(
    *,
    recipient: str,
    field: str,
    value: str,
) -> MemoryUpdate | None:

    canonical_name = (
        _canonical_recipient(
            recipient
        )
    )

    if not canonical_name:
        return None

    normalized_field = (
        _normalize_field(
            field
        )
    )

    if normalized_field not in VALID_FIELDS:
        return None

    clean_value = _sanitize_fact_value(
        value
    )

    if not clean_value:
        return None

    return MemoryUpdate(
        recipient_name=canonical_name,
        field=normalized_field,
        value=clean_value,
    )


def _extract_direct_fact(
    clause: str,
) -> list[MemoryUpdate]:

    updates: list[
        MemoryUpdate
    ] = []

    clean_clause = clause.strip(
        " \t\r\n,.;:!?-"
    )

    if not clean_clause:
        return updates

    # ----------------------------------------------------------
    # ALLERGY
    #
    # "My mother is allergic to chocolate"
    # ----------------------------------------------------------
    allergy_match = re.match(
        r"^(?:my\s+)?"
        r"(?P<recipient>.+?)\s+"
        r"(?:is|are)\s+"
        r"allergic\s+to\s+"
        r"(?P<value>.+?)$",
        clean_clause,
        flags=re.IGNORECASE,
    )

    if allergy_match:

        recipient = (
            allergy_match.group(
                "recipient"
            ).strip()
        )

        value = allergy_match.group(
            "value"
        ).strip()

        values = _split_fact_values(
            value
        )

        for fact_value in values:

            update = _make_update(
                recipient=recipient,
                field="allergies",
                value=fact_value,
            )

            if update:
                updates.append(
                    update
                )

        if updates:
            return updates

    # ----------------------------------------------------------
    # LIKES / LOVES / PREFERS
    #
    # "My mother loves cotton sarees"
    # ----------------------------------------------------------
    like_match = re.match(
        r"^(?:my\s+)?"
        r"(?P<recipient>.+?)\s+"
        r"(?:love|loves|like|likes|"
        r"prefer|prefers)\s+"
        r"(?P<value>.+?)$",
        clean_clause,
        flags=re.IGNORECASE,
    )

    if like_match:

        recipient = (
            like_match.group(
                "recipient"
            ).strip()
        )

        value = like_match.group(
            "value"
        ).strip()

        values = _split_fact_values(
            value
        )

        for fact_value in values:

            update = _make_update(
                recipient=recipient,
                field="likes",
                value=fact_value,
            )

            if update:
                updates.append(
                    update
                )

        if updates:
            return updates

    # ----------------------------------------------------------
    # DISLIKES / HATES
    # ----------------------------------------------------------
    dislike_match = re.match(
        r"^(?:my\s+)?"
        r"(?P<recipient>.+?)\s+"
        r"(?:dislike|dislikes|"
        r"hate|hates)\s+"
        r"(?P<value>.+?)$",
        clean_clause,
        flags=re.IGNORECASE,
    )

    if dislike_match:

        recipient = (
            dislike_match.group(
                "recipient"
            ).strip()
        )

        value = dislike_match.group(
            "value"
        ).strip()

        values = _split_fact_values(
            value
        )

        for fact_value in values:

            update = _make_update(
                recipient=recipient,
                field="dislikes",
                value=fact_value,
            )

            if update:
                updates.append(
                    update
                )

        if updates:
            return updates

    return updates


# ======================================================================
# PRONOUN-BASED FACT PARSING
# ======================================================================

async def _infer_recent_recipient(
    *,
    user_id: str,
    session_id: str,
    explicit_recipient: str | None,
    current_text: str,
) -> str | None:

    if explicit_recipient:

        canonical = _canonical_recipient(
            explicit_recipient
        )

        if canonical:
            return canonical

    try:

        profiles = await (
            structured_memory.list_all_profiles(
                user_id
            )
        )

        profiles = valid_profiles_only(
            profiles
        )

        if len(profiles) == 1:

            return profiles[0].name

        context = (
            current_text
            + "\n"
            + await short_term.get_context(
                session_id
            )
        )

        normalized_context = (
            _normalize_text(
                context
            )
        )

        best_profile = None
        best_position = -1

        for profile in profiles:

            aliases = {
                _normalize_text(
                    profile.name
                )
            }

            if profile.relationship:

                aliases.add(
                    _normalize_text(
                        profile.relationship
                    )
                )

            for alias in aliases:

                if not alias:
                    continue

                position = (
                    normalized_context.rfind(
                        alias
                    )
                )

                if position > best_position:

                    best_position = position
                    best_profile = profile

        if best_profile is not None:
            return best_profile.name

    except Exception as exc:

        logger.debug(
            "Recent recipient inference failed: %s",
            exc,
        )

    return None


def _extract_pronoun_fact(
    *,
    clause: str,
    recipient: str | None,
) -> MemoryUpdate | None:

    if not recipient:
        return None

    clean_clause = clause.strip(
        " \t\r\n,.;:!?-"
    )

    # he/she/they + likes/loves/prefers
    like_match = re.match(
        r"^(?:he|him|his|she|her|hers|"
        r"they|them|their|theirs)\s+"
        r"(?:love|loves|like|likes|"
        r"prefer|prefers)\s+"
        r"(?P<value>.+?)$",
        clean_clause,
        flags=re.IGNORECASE,
    )

    if like_match:

        value = like_match.group(
            "value"
        )

        values = _split_fact_values(
            value
        )

        if values:

            return _make_update(
                recipient=recipient,
                field="likes",
                value=values[0],
            )

    # he/she/they + dislikes/hates
    dislike_match = re.match(
        r"^(?:he|him|his|she|her|hers|"
        r"they|them|their|theirs)\s+"
        r"(?:dislike|dislikes|"
        r"hate|hates)\s+"
        r"(?P<value>.+?)$",
        clean_clause,
        flags=re.IGNORECASE,
    )

    if dislike_match:

        value = dislike_match.group(
            "value"
        )

        values = _split_fact_values(
            value
        )

        if values:

            return _make_update(
                recipient=recipient,
                field="dislikes",
                value=values[0],
            )

    return None


# ======================================================================
# DETERMINISTIC EXTRACTION
# ======================================================================

async def _deterministic_extract(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> list[MemoryUpdate]:

    text = _clean_whitespace(
        user_message
    )

    if not text:
        return []

    updates: list[
        MemoryUpdate
    ] = []

    # Track the most recently explicit recipient.
    active_recipient: str | None = None

    # ----------------------------------------------------------
    # FIRST split into sentence-like units.
    # ----------------------------------------------------------
    sentences = _split_sentences(
        text
    )

    for sentence in sentences:

        if not sentence:
            continue

        # ------------------------------------------------------
        # Then split contrast clauses.
        #
        # This preserves:
        #
        # My wife is allergic to chocolate, but she loves vanilla.
        #
        # as two separate facts.
        # ------------------------------------------------------
        clauses = _split_contrast_clauses(
            sentence
        )

        for clause in clauses:

            if not clause:
                continue

            # --------------------------------------------------
            # Skip obvious request-only clauses.
            # --------------------------------------------------
            if (
                _looks_like_request_text(
                    clause
                )
                and not re.search(
                    r"\b(?:is|are)\s+"
                    r"allergic\s+to\b",
                    clause,
                    re.IGNORECASE,
                )
                and not re.search(
                    r"\b(?:love|loves|like|likes|"
                    r"prefer|prefers|dislike|dislikes|"
                    r"hate|hates)\b",
                    clause,
                    re.IGNORECASE,
                )
            ):
                continue

            # --------------------------------------------------
            # DIRECT named recipient fact.
            # --------------------------------------------------
            direct_updates = (
                _extract_direct_fact(
                    clause
                )
            )

            if direct_updates:

                updates.extend(
                    direct_updates
                )

                # Update active recipient from the successful
                # direct facts.
                for update in direct_updates:

                    active_recipient = (
                        update.recipient_name
                    )

                continue

            # --------------------------------------------------
            # Pronoun-based fact:
            #
            # "... but she loves vanilla"
            # --------------------------------------------------
            pronoun_update = (
                _extract_pronoun_fact(
                    clause=clause,
                    recipient=active_recipient,
                )
            )

            if pronoun_update:

                updates.append(
                    pronoun_update
                )

    # ----------------------------------------------------------
    # Deduplicate.
    # ----------------------------------------------------------
    unique: list[
        MemoryUpdate
    ] = []

    seen: set[
        tuple[str, str, str]
    ] = set()

    for update in updates:

        recipient = _normalize_text(
            update.recipient_name
        )

        field = _normalize_text(
            update.field
        )

        value = _normalize_text(
            update.value
        )

        key = (
            recipient,
            field,
            value,
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        unique.append(
            update
        )

    return unique


# ======================================================================
# LLM OUTPUT NORMALIZATION
# ======================================================================

def _normalize_llm_update(
    raw: Any,
) -> dict[str, str] | None:

    if not isinstance(
        raw,
        dict,
    ):
        return None

    recipient = (
        raw.get(
            "recipient_name"
        )
        or raw.get(
            "recipient"
        )
        or raw.get(
            "name"
        )
    )

    field = raw.get(
        "field"
    )

    value = raw.get(
        "value"
    )

    if (
        recipient is None
        or field is None
        or value is None
    ):
        return None

    canonical_recipient = (
        _canonical_recipient(
            str(recipient)
        )
    )

    if not canonical_recipient:
        return None

    normalized_field = (
        _normalize_field(
            str(field)
        )
    )

    if normalized_field not in VALID_FIELDS:
        return None

    clean_value = _sanitize_fact_value(
        str(value)
    )

    if not clean_value:
        return None

    return {
        "recipient_name": canonical_recipient,
        "field": normalized_field,
        "value": clean_value,
    }


def _parse_llm_response(
    text: str,
) -> MemoryExtraction:

    try:

        parsed = json.loads(
            text
        )

    except (
        json.JSONDecodeError,
        TypeError,
    ):

        return MemoryExtraction()

    if isinstance(
        parsed,
        dict,
    ):

        raw_updates = parsed.get(
            "updates",
            [],
        )

    elif isinstance(
        parsed,
        list,
    ):

        raw_updates = parsed

    else:

        raw_updates = []

    if not isinstance(
        raw_updates,
        list,
    ):
        return MemoryExtraction()

    updates: list[
        MemoryUpdate
    ] = []

    for raw_update in raw_updates:

        normalized = (
            _normalize_llm_update(
                raw_update
            )
        )

        if normalized is None:
            continue

        try:

            updates.append(
                MemoryUpdate.model_validate(
                    normalized
                )
            )

        except Exception:
            continue

    return MemoryExtraction(
        updates=updates
    )


# ======================================================================
# STORAGE
# ======================================================================

async def _store_updates(
    *,
    user_id: str,
    session_id: str,
    updates: list[MemoryUpdate],
) -> list[dict[str, Any]]:

    applied: list[
        dict[str, Any]
    ] = []

    for update in updates:

        # Safety gate: temporary budget must never become memory.
        if is_temporary_budget_value(
            update.value
        ):
            continue

        resolved_name = (
            await resolve_recipient_reference(
                user_id=user_id,
                session_id=session_id,
                raw_name=update.recipient_name,
            )
        )

        if not resolved_name:
            logger.warning(
                "Could not resolve recipient '%s'.",
                update.recipient_name,
            )
            continue

        try:

            profile = (
                await structured_memory.apply_update(
                    user_id=user_id,
                    session_id=session_id,
                    recipient_name=resolved_name,
                    field=update.field,
                    value=update.value,
                )
            )

            applied.append(
                {
                    "recipient_name": profile.name,
                    "field": update.field,
                    "value": update.value,
                }
            )

            logger.info(
                "Durable recipient memory stored: "
                "user=%s recipient=%s field=%s",
                user_id,
                profile.name,
                update.field,
            )

        except Exception as exc:

            logger.exception(
                "Failed to persist recipient memory: %s",
                exc,
            )

    return applied


# ======================================================================
# PUBLIC EXTRACTION API
# ======================================================================

@traced(
    "agent.memory.extract_and_store"
)
async def extract_and_store(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> list[dict[str, Any]]:

    text = _clean_whitespace(
        user_message
    )

    if not text:
        return []

    # ----------------------------------------------------------
    # Profile recall is not a memory-save operation.
    #
    # For requests such as:
    #   "What do you know about my wife?"
    #   "What do you remember about my brother?"
    #
    # the router/profile-recall path is authoritative. Running the memory
    # extraction LLM here only adds latency and can never produce a useful
    # durable update.
    # ----------------------------------------------------------
    if _looks_like_profile_recall_request(text):
        logger.debug(
            "Skipping memory extraction for profile recall request."
        )
        return []

    extraction = MemoryExtraction()

    # ----------------------------------------------------------
    # Try LLM extraction first.
    # ----------------------------------------------------------
    try:

        client = get_openrouter_client()

        prompt = render_prompt(
            "memory_agent_system.jinja",
            user_message=text,
        )

        response = await client.chat_completion(
            task="memory_agent",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            response_format={
                "type": "json_object"
            },
        )

        extraction = _parse_llm_response(
            client.extract_text(
                response
            )
        )

    except Exception as exc:

        logger.warning(
            "Memory extraction LLM failed; "
            "using deterministic fallback: %s",
            exc,
        )

    # ----------------------------------------------------------
    # If LLM returned nothing, or the LLM failed,
    # deterministic extraction takes over.
    #
    # This is especially important because the current logs show
    # OpenRouter memory extraction frequently failing.
    # ----------------------------------------------------------
    if not extraction.updates:

        try:

            deterministic_updates = (
                await _deterministic_extract(
                    user_id=user_id,
                    session_id=session_id,
                    user_message=text,
                )
            )

            extraction = MemoryExtraction(
                updates=deterministic_updates
            )

            if deterministic_updates:

                logger.info(
                    "Deterministic memory fallback produced "
                    "%d update(s).",
                    len(
                        deterministic_updates
                    ),
                )

        except Exception as exc:

            logger.exception(
                "Deterministic memory extraction failed: %s",
                exc,
            )

            return []

    if not extraction.updates:
        return []

    return await _store_updates(
        user_id=user_id,
        session_id=session_id,
        updates=extraction.updates,
    )


# ======================================================================
# MEMORY PREFETCH
# ======================================================================

@traced(
    "agent.memory.prefetch_context"
)
async def prefetch_context(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> dict[str, Any]:

    short_term_context, semantic_hits = (
        await asyncio.gather(
            short_term.get_context(
                session_id
            ),
            semantic_memory.recall_relevant_notes(
                session_id,
                user_message,
            ),
        )
    )

    profiles = (
        await structured_memory.list_all_profiles(
            user_id
        )
    )

    profiles = valid_profiles_only(
        profiles
    )

    profile_parts: list[
        str
    ] = []

    for profile in profiles:

        lines = [
            f"Recipient: {profile.name}"
        ]

        if profile.relationship:

            lines.append(
                "Relationship: "
                + profile.relationship
            )

        if profile.allergies:

            lines.append(
                "Allergies: "
                + ", ".join(
                    profile.allergies
                )
            )

        if profile.likes:

            lines.append(
                "Likes: "
                + ", ".join(
                    profile.likes
                )
            )

        if profile.dislikes:

            lines.append(
                "Dislikes: "
                + ", ".join(
                    profile.dislikes
                )
            )

        if profile.notes:

            lines.append(
                "Notes: "
                + profile.notes
            )

        profile_parts.append(
            "\n".join(
                lines
            )
        )

    profile_context = (
        "\n\n".join(
            profile_parts
        )
    )

    filtered_semantic_hits = []

    for item in semantic_hits:

        text = str(
            item.get(
                "text",
                "",
            )
        )

        # Never surface temporary financial memory as durable
        # preference context.
        if is_temporary_budget_value(
            text
        ):
            continue

        filtered_semantic_hits.append(
            item
        )

    semantic_context = "\n".join(
        (
            f"- {item.get('name', '')} "
            f"({item.get('field', '')}): "
            f"{item.get('text', '')}"
        )
        for item in filtered_semantic_hits
    )

    return {
        "short_term_context": (
            short_term_context
        ),
        "memory_context": (
            profile_context
            or semantic_context
            or "(no stored memory)"
        ),
        "semantic_hits": (
            filtered_semantic_hits
        ),
        "profiles": profiles,
    }