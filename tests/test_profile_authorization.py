from __future__ import annotations

import pytest

from backend.db.repositories import profile_repo


@pytest.mark.asyncio
async def test_profile_lookup_contains_session_predicate(monkeypatch):
    calls = []

    class Query:
        def select(self, *_): return self
        def eq(self, key, value):
            calls.append((key, value))
            return self
        def limit(self, *_): return self
        def execute(self): return type("R", (), {"data": []})()

    class Client:
        def table(self, *_): return Query()

    monkeypatch.setattr(profile_repo, "get_supabase", lambda: Client())
    assert await profile_repo.get_profile("session-a", "profile-b") is None
    assert ("session_id", "session-a") in calls
    assert ("id", "profile-b") in calls