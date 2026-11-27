"""
Supabase client singleton (service-role key, server-side only).

supabase-py exposes a synchronous client by default, so all repository/database
calls must execute through run_sync() to keep blocking HTTP work off the event loop.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

from supabase import Client, ClientOptions, create_client

from config.settings import get_settings

_client: Client | None = None
T = TypeVar("T")


def get_supabase() -> Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set in .env")
        _client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
            options=ClientOptions(postgrest_client_timeout=15),
        )
    return _client


async def run_sync(fn: Callable[[], T]) -> T:
    """Run a synchronous Supabase operation in a worker thread."""
    return await asyncio.to_thread(fn)