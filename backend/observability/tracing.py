"""Lightweight tracing decorator with console performance measurement."""
from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from typing import Any

from backend.core.logging import get_logger
from backend.observability.langfuse_client import get_langfuse
from config.settings import get_params_config

logger = get_logger(__name__)


def _print_performance(
    name: str,
    duration_ms: float,
    *,
    session_id: str | None = None,
    intent: str | None = None,
) -> None:
    """Print performance timing to the backend console only.

    This is diagnostic instrumentation for Stage 1 latency measurement.
    It does not affect the returned value or control flow.
    """
    session_text = session_id or "-"
    intent_text = intent or "-"

    print(
        f"[PERF] {name:<42} "
        f"{duration_ms:>10.1f} ms "
        f"({duration_ms / 1000.0:>7.3f} s) "
        f"| session={session_text} "
        f"| intent={intent_text}",
        flush=True,
    )


def traced(name: str):
    """Trace an async call and always print its execution duration.

    Langfuse tracing remains optional according to the existing configuration,
    while console timing is always enabled for latency diagnosis.
    """

    def decorator(fn: Callable[..., Any]):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            session_id = kwargs.get("session_id")
            intent = kwargs.get("intent")

            sample_rate = float(
                get_params_config()
                .get("observability", {})
                .get("sample_rate", 1.0)
            )

            trace_enabled = (
                sample_rate >= 1.0
                or random.random() < max(sample_rate, 0.0)
            )

            langfuse = (
                get_langfuse()
                if trace_enabled
                else None
            )

            span = None

            if langfuse is not None:
                try:
                    span = langfuse.start_span(
                        name=name,
                        metadata={
                            "session_id": session_id,
                            "intent": intent,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Langfuse span unavailable: %s",
                        exc,
                    )

            start = time.perf_counter()

            try:
                return await fn(*args, **kwargs)

            finally:
                duration_ms = (
                    time.perf_counter() - start
                ) * 1000.0

                if span is not None:
                    try:
                        span.end()
                    except Exception:  # noqa: BLE001
                        pass

                # Existing structured logging.
                logger.info(
                    "%s completed",
                    name,
                    extra={
                        "session_id": session_id,
                        "intent": intent,
                        "duration_ms": round(
                            duration_ms,
                            1,
                        ),
                    },
                )

                # Stage-1 console measurement.
                _print_performance(
                    name,
                    duration_ms,
                    session_id=session_id,
                    intent=intent,
                )

        return wrapper

    return decorator