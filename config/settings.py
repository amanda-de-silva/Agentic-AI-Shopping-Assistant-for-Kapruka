"""
Central settings loader.

Two layers, on purpose:
  - `.env` -> secrets & environment-specific values (Settings, via pydantic-settings)
  - `*.yaml` -> behavioral/tuning knobs that are safe to commit (loaded once, cached)

Nothing else in the codebase should read `os.environ` or open a yaml file directly --
always go through `get_settings()` / `get_models_config()` / `get_params_config()` /
`get_faqs_config()` / `get_mcp_tools_config()` so there is exactly one source of truth
and it's easy to mock in tests.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(CONFIG_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM / OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_url: str = "http://localhost:8000"
    openrouter_app_name: str = "kapruka-ai-concierge"

    # Tavily web search for general delivery policy/coverage questions.
    tavily_api_key: str = ""

    # Qdrant Cloud
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_semantic_memory: str = "semantic_memory"
    qdrant_collection_convo_summaries: str = "conversation_summaries"

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    supabase_db_url: str = ""

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True

    # Kapruka MCP
    kapruka_mcp_url: str = "https://mcp.kapruka.com/mcp"
    mcp_rate_limit_redis_url: str = ""
    mcp_rate_limit_key_prefix: str = "kapruka:mcp:ratelimit"

    # App
    app_env: str = "development"
    backend_port: int = 8000
    frontend_port: int = 5173
    cors_origins: str = "http://localhost:5173"
    session_secret: str = "change-me-in-production"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml(filename: str) -> dict:
    path = CONFIG_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def get_models_config() -> dict:
    return _load_yaml("models.yaml")


@lru_cache
def get_params_config() -> dict:
    return _load_yaml("params.yaml")


@lru_cache
def get_faqs_config() -> dict:
    return _load_yaml("faqs.yaml")


@lru_cache
def get_mcp_tools_config() -> dict:
    return _load_yaml("mcp_tools.yaml")
