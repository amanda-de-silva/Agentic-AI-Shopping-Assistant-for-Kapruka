"""
Thin async client over OpenRouter's OpenAI-compatible chat completions API.

Each task can use its own model from config/models.yaml.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.core.logging import get_logger
from backend.observability.tracing import traced
from config.settings import get_models_config, get_settings

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# TASK -> MODEL CONFIGURATION KEY
# ---------------------------------------------------------------------------

_TASK_MODEL_KEY = {
    # Language normalization / bilingual processing.
    "language": "language_model",
    "language_agent": "language_model",
    "language_normalizer": "language_model",

    # Fast routing.
    "router": "router_model",

    # Fast memory extraction.
    "memory_agent": "memory_model",

    # Recommendation pipeline.
    "recommendation_parser": "requirement_parser_model",
    "product_normalizer": "product_normalizer_model",
    "recommendation_ranker": "ranker_model",

    # Existing generic catalog/logistics/cart tasks.
    "catalog_agent": "chat_model",
    "logistics_agent": "chat_model",
    "cart_agent": "chat_model",

    # Reflection and final response.
    "reflection": "reflection_model",
    "composer": "composer_model",
}


# ---------------------------------------------------------------------------
# TASK -> TEMPERATURE CONFIGURATION KEY
# ---------------------------------------------------------------------------

_TASK_TEMPERATURE_KEY = {
    "language": "language",
    "language_agent": "language",
    "language_normalizer": "language",

    "router": "router",

    "catalog_agent": "catalog_agent",
    "logistics_agent": "logistics_agent",
    "cart_agent": "cart_agent",

    "memory_agent": "memory_agent",

    "recommendation_parser": "recommendation_parser",
    "product_normalizer": "product_normalizer",
    "recommendation_ranker": "recommendation_ranker",

    "reflection": "reflection",
    "composer": "composer",
}


class OpenRouterClient:
    def __init__(self) -> None:
        settings = get_settings()

        self._base_url = (
            settings.openrouter_base_url.rstrip("/")
        )

        self._api_key = settings.openrouter_api_key

        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.openrouter_app_url,
            "X-Title": settings.openrouter_app_name,
        }

        models_cfg = get_models_config()["llm"]

        self._models_cfg = models_cfg

        self._timeout = models_cfg.get(
            "timeout_seconds",
            30,
        )

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -----------------------------------------------------------------------
    # MODEL / TEMPERATURE / TOKEN RESOLUTION
    # -----------------------------------------------------------------------

    def _resolve(
        self,
        task: str,
    ) -> tuple[str, float, int]:
        model_key = _TASK_MODEL_KEY.get(
            task,
            "chat_model",
        )

        temperature_key = _TASK_TEMPERATURE_KEY.get(
            task,
            "composer",
        )

        model = self._models_cfg.get(
            model_key,
            self._models_cfg["chat_model"],
        )

        temperature = self._models_cfg.get(
            "temperature",
            {},
        ).get(
            temperature_key,
            0.3,
        )

        # Keep the existing token policy:
        # router -> router limit
        # reflection -> reflection limit
        # everything else -> agent limit
        if task == "reflection":
            max_tokens_key = "reflection"
        elif task == "router":
            max_tokens_key = "router"
        elif task == "composer":
            max_tokens_key = "composer"
        else:
            max_tokens_key = "agent"

        max_tokens = self._models_cfg.get(
            "max_tokens",
            {},
        ).get(
            max_tokens_key,
            800,
        )

        return (
            model,
            temperature,
            max_tokens,
        )

    # -----------------------------------------------------------------------
    # NORMAL CHAT COMPLETION
    # -----------------------------------------------------------------------

    @traced(
        "llm.openrouter.chat_completion"
    )
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(
            multiplier=1,
            max=10,
        ),
        retry=retry_if_exception_type(
            (
                httpx.TransportError,
                httpx.HTTPStatusError,
            )
        ),
    )
    async def chat_completion(
        self,
        *,
        task: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        temperature_override: float | None = None,
    ) -> dict[str, Any]:
        model, temperature, max_tokens = (
            self._resolve(task)
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": (
                temperature_override
                if temperature_override is not None
                else temperature
            ),
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools

        if tool_choice:
            payload["tool_choice"] = tool_choice

        if response_format:
            payload["response_format"] = response_format

        resp = await self._client.post(
            "/chat/completions",
            headers=self._headers,
            json=payload,
        )

        resp.raise_for_status()

        return resp.json()

    # -----------------------------------------------------------------------
    # STREAMING CHAT COMPLETION
    # -----------------------------------------------------------------------

    async def chat_completion_stream(
        self,
        *,
        task: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        model, temperature, max_tokens = (
            self._resolve(task)
        )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with self._client.stream(
            "POST",
            "/chat/completions",
            headers=self._headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if (
                    not line
                    or not line.startswith("data:")
                ):
                    continue

                data = line[
                    len("data:"):
                ].strip()

                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = (
                    chunk
                    .get("choices", [{}])[0]
                    .get("delta", {})
                )

                token = delta.get(
                    "content"
                )

                if token:
                    yield token

    # -----------------------------------------------------------------------
    # RESPONSE HELPERS
    # -----------------------------------------------------------------------

    @staticmethod
    def extract_text(
        response: dict[str, Any],
    ) -> str:
        return (
            response["choices"][0]["message"]
            .get("content", "")
            or ""
        )

    @staticmethod
    def extract_tool_calls(
        response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return (
            response["choices"][0]["message"]
            .get("tool_calls", [])
            or []
        )


# ---------------------------------------------------------------------------
# CLIENT SINGLETON
# ---------------------------------------------------------------------------

_client: OpenRouterClient | None = None


def get_openrouter_client() -> OpenRouterClient:
    global _client

    if _client is None:
        _client = OpenRouterClient()

    return _client