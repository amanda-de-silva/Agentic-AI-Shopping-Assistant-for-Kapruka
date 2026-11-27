"""Compatibility wrapper for the new constraint-first recommendation service."""

from __future__ import annotations

from backend.recommendation.service import recommend

__all__ = [
    "recommend",
]