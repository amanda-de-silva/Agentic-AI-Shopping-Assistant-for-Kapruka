"""
Small text helpers (slug/normalize) used for FAQ trigger matching, etc.
"""
from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def contains_any(text: str, triggers: list[str]) -> bool:
    normalized = normalize(text)
    return any(normalize(t) in normalized for t in triggers)


def slugify(text: str) -> str:
    text = normalize(text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")
