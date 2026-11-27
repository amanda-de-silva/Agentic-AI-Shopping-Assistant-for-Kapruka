"""
Small in-memory TTL cache for low-churn data (categories, delivery cities).

Deliberately not Redis: this data (Kapruka categories, delivery city list) changes
rarely and per-process memory is enough for a single backend instance. If you scale
to multiple backend replicas, swap this for a shared cache without touching callers —
they only depend on the `get`/`set` interface below.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, _Entry[Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._store[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_seconds)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Process-wide singleton — imported by services that need low-churn caching.
cache = TTLCache()
