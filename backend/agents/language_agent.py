"""Bilingual language processing for English/Sinhala chat input.

The language layer has one responsibility:

    user language
        ->
    semantic English representation for the existing agents

and one output responsibility:

    authoritative English specialist results
        ->
    natural response in the user's input language

It intentionally does NOT modify product facts, recommendation logic,
memory rules, budget rules, recipient rules, or catalog behavior.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from backend.llm.openrouter_client import (
    get_openrouter_client,
)
from backend.llm.prompt_loader import (
    render_prompt,
)
from backend.observability.tracing import traced


SinhalaLanguage = Literal[
    "en",
    "si",
    "mixed",
    "unknown",
]


class LanguageResult(BaseModel):
    source_language: SinhalaLanguage = "unknown"
    response_language: Literal[
        "en",
        "si",
    ] = "en"

    original_text: str = ""

    # This is the message consumed by the existing English-oriented
    # routing/recommendation/memory pipeline.
    normalized_english: str = ""

    translated: bool = False

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )


def contains_sinhala(text: str) -> bool:
    """Return True when Sinhala Unicode characters are present."""

    if not text:
        return False

    return any(
        "\u0d80" <= character <= "\u0dff"
        for character in text
    )


def detect_language(
    text: str,
) -> SinhalaLanguage:
    """Fast deterministic script detection.

    Sinhala script takes priority because a Sinhala sentence can contain
    English product names, brands, numbers, and LKR amounts.
    """

    text = text.strip()

    if not text:
        return "unknown"

    has_sinhala = contains_sinhala(text)

    ascii_letters = sum(
        character.isascii()
        and character.isalpha()
        for character in text
    )

    sinhala_letters = sum(
        "\u0d80"
        <= character
        <= "\u0dff"
        for character in text
    )

    if has_sinhala and ascii_letters > 0:
        return "mixed"

    if sinhala_letters > 0:
        return "si"

    return "en"


def _fallback_result(
    text: str,
    detected: SinhalaLanguage,
) -> LanguageResult:
    response_language = (
        "si"
        if detected in {"si", "mixed"}
        else "en"
    )

    return LanguageResult(
        source_language=detected,
        response_language=response_language,
        original_text=text,
        normalized_english=text,
        translated=False,
        confidence=0.0,
    )


@traced(
    "agent.language.normalize"
)
async def normalize_user_message(
    user_message: str,
) -> LanguageResult:
    """Detect input language and semantically normalize Sinhala to English.

    English messages are returned unchanged.

    Sinhala/mixed messages are passed to the LLM for semantic normalization.
    This is NOT word-by-word translation. The prompt explicitly requires the
    model to reconstruct the user's actual shopping intent.
    """

    text = user_message.strip()

    if not text:
        return LanguageResult(
            source_language="unknown",
            response_language="en",
            original_text="",
            normalized_english="",
            translated=False,
            confidence=1.0,
        )

    detected = detect_language(text)

    if detected == "en":
        return LanguageResult(
            source_language="en",
            response_language="en",
            original_text=text,
            normalized_english=text,
            translated=False,
            confidence=1.0,
        )

    client = get_openrouter_client()

    prompt = render_prompt(
        "language_agent_system.jinja",
        mode="normalize",
        input_text=text,
    )

    try:
        response = await client.chat_completion(
            task="composer",
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
                "type": "json_object",
            },
            temperature_override=0.0,
        )

        raw_text = client.extract_text(
            response
        )

        parsed = json.loads(
            raw_text
        )

        normalized = str(
            parsed.get(
                "normalized_english",
                "",
            )
            or ""
        ).strip()

        language = str(
            parsed.get(
                "source_language",
                detected,
            )
            or detected
        ).lower()

        if language not in {
            "en",
            "si",
            "mixed",
            "unknown",
        }:
            language = detected

        confidence = float(
            parsed.get(
                "confidence",
                0.9,
            )
            or 0.9
        )

        if not normalized:
            raise ValueError(
                "Language normalization returned empty text."
            )

        return LanguageResult(
            source_language=language,  # type: ignore[arg-type]
            response_language=(
                "si"
                if detected
                in {"si", "mixed"}
                else "en"
            ),
            original_text=text,
            normalized_english=normalized,
            translated=True,
            confidence=max(
                0.0,
                min(
                    confidence,
                    1.0,
                ),
            ),
        )

    except Exception:
        # The existing downstream agents may still understand the original
        # message, so do not break the complete chat pipeline merely because
        # the optional normalization call fails.
        return _fallback_result(
            text,
            detected,
        )


@traced(
    "agent.language.localize"
)
async def localize_response(
    response_text: str,
    *,
    language: Literal[
        "en",
        "si",
    ],
) -> str:
    """Translate generated conversational text into the requested language.

    Used mainly for static/configured responses such as FAQ answers.
    """

    if (
        language != "si"
        or not response_text.strip()
    ):
        return response_text

    client = get_openrouter_client()

    prompt = render_prompt(
        "language_agent_system.jinja",
        mode="localize",
        input_text=response_text,
    )

    try:
        response = await client.chat_completion(
            task="composer",
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": response_text,
                },
            ],
            temperature_override=0.0,
        )

        translated = client.extract_text(
            response
        ).strip()

        return translated or response_text

    except Exception:
        return response_text