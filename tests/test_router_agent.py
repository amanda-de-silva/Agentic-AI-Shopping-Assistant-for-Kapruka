"""
Tests for intent classification and slot extraction.

These hit the real OpenRouter API (router calls are cheap: openai/gpt-4o-mini, ~300
tokens) -- skipped automatically if OPENROUTER_API_KEY isn't set, so `make test` still
passes in CI without secrets configured.
"""
from __future__ import annotations

import pytest

from backend.agents import router_agent
from config.settings import get_settings

pytestmark = pytest.mark.skipif(
    not get_settings().openrouter_api_key, reason="OPENROUTER_API_KEY not set"
)


async def test_classifies_product_search():
    result = await router_agent.classify(
        user_message="find me a nut-free chocolate cake under 3000 rupees",
        short_term_context="",
        memory_context="",
    )
    assert "product_search" in result["intents"]


async def test_classifies_track_order():
    result = await router_agent.classify(
        user_message="where is my order KAP-12345",
        short_term_context="",
        memory_context="",
    )
    assert "track_order" in result["intents"]
    assert result["slots"].get("order_number")
