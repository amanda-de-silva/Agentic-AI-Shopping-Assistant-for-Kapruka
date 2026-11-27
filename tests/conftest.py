"""
Shared pytest fixtures: test client, fake MCP client, in-memory db stand-ins.
"""
from __future__ import annotations

import pytest

from backend.mcp.tool_schemas import MCPToolResult


class FakeMCPClient:
    """Drop-in stand-in for KaprukaMCPClient that returns canned data instead of
    hitting the real Kapruka server, so agent/service tests don't need network."""

    def __init__(self, canned: dict[str, dict] | None = None):
        self.canned = canned or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict, *, is_order: bool = False) -> MCPToolResult:
        self.calls.append((tool_name, arguments))
        data = self.canned.get(tool_name, {})
        return MCPToolResult(tool_name=tool_name, ok=True, data=data, latency_ms=1.0)

    async def search_products(self, params):
        return await self.call_tool("kapruka_search_products", params.model_dump())

    async def get_product(self, params):
        return await self.call_tool("kapruka_get_product", params.model_dump())

    async def track_order(self, params):
        return await self.call_tool("kapruka_track_order", params.model_dump())


@pytest.fixture
def fake_mcp_client():
    return FakeMCPClient()
