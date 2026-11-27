"""
Langfuse client singleton, initialized from config/params.yaml: observability.
"""
from __future__ import annotations

from langfuse import Langfuse

from config.settings import get_params_config, get_settings

_client: Langfuse | None = None
_enabled: bool = False


def get_langfuse() -> Langfuse | None:
    global _client, _enabled
    if _client is None:
        settings = get_settings()
        params = get_params_config()["observability"]
        _enabled = settings.langfuse_enabled and params.get("langfuse_enabled", True)
        if not _enabled or not settings.langfuse_public_key:
            return None
        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _client if _enabled else None


def is_enabled() -> bool:
    get_langfuse()  # ensure initialized
    return _enabled
