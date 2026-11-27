"""
GET /health, /ready -- liveness and readiness probes.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.mcp.client import get_mcp_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    try:
        tools = await get_mcp_client().list_tools()
        return {"status": "ready", "mcp_tools": tools}
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready", "error": str(exc)}
