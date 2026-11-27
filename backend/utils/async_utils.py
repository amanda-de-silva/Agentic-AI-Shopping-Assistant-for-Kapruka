"""
asyncio.gather-with-timeout and fan-out/fan-in helpers used by the orchestrator.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

T = TypeVar("T")


async def gather_with_timeout(
    *coros: Awaitable[Any], timeout_seconds: float, return_exceptions: bool = True
) -> list[Any]:
    """Runs coroutines concurrently, capping total wall time. Any coroutine still
    pending at the deadline is cancelled and represented as a TimeoutError in the
    returned list (when return_exceptions=True) so one slow agent never hangs the
    whole turn."""
    try:
        return await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=return_exceptions), timeout=timeout_seconds
        )
    except TimeoutError:
        return [TimeoutError(f"exceeded {timeout_seconds}s") for _ in coros]


async def run_named(tasks: dict[str, Awaitable[Any]]) -> dict[str, Any]:
    """Runs a dict of named coroutines concurrently, returning a dict of results
    (or exceptions) keyed by the same names -- used by the orchestrator to fan out to
    multiple specialist agents and know which output came from which."""
    keys = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return dict(zip(keys, results))
