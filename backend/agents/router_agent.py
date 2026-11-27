"""
Classifies each user turn into one or more intents and extracts slots
(product_search, add_to_cart, checkout, track_order, save_preference, etc.).
"""
from __future__ import annotations

import json
from typing import Any

from backend.llm.openrouter_client import get_openrouter_client
from backend.llm.prompt_loader import render_prompt
from backend.observability.tracing import traced


@traced("agent.router.classify")
async def classify(*, user_message: str, short_term_context: str, memory_context: str) -> dict[str, Any]:
    client = get_openrouter_client()
    system_prompt = render_prompt(
        "router_system.jinja",
        short_term_context=short_term_context,
        memory_context=memory_context,
        user_message=user_message,
    )
    response = await client.chat_completion(
        task="router",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
    )
    text = client.extract_text(response)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"intents": ["general_chat"], "slots": {}, "requires_reflection": False}

    parsed.setdefault("intents", ["general_chat"])
    parsed.setdefault("slots", {})
    parsed.setdefault("requires_reflection", False)
    return parsed
