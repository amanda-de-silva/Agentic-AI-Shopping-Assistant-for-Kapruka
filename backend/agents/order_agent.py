"""Order tracking agent backed by the authorized order cache."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.db.repositories.order_repo import get_cached_order_status, upsert_order_status_cache
from backend.mcp.client import get_mcp_client
from backend.mcp.markdown_parsers import extract_markdown_result, parse_track_order_markdown
from backend.mcp.tool_schemas import TrackOrderInput
from backend.observability.tracing import traced
from backend.schemas.order import OrderStatus
from config.settings import get_params_config


@traced("agent.order.track_order")
async def track_order(order_number: str, *, force_refresh: bool = False) -> OrderStatus:
    ttl_minutes = int(get_params_config()["cache"]["order_status_ttl_minutes"])
    if not force_refresh:
        cached = await get_cached_order_status(order_number)
        if cached and cached.last_synced_at:
            age_seconds = (datetime.now(timezone.utc) - cached.last_synced_at).total_seconds()
            if age_seconds < ttl_minutes * 60 and cached.raw_payload:
                return _to_order_status(order_number, cached.raw_payload)

    result = await get_mcp_client().track_order(TrackOrderInput(order_number=order_number))
    data = result.data or {}
    markdown = extract_markdown_result(data)
    parsed = parse_track_order_markdown(markdown) if markdown is not None else data
    cache_status = parsed.get("status") or parsed.get("error_code") or "unparsed"
    await upsert_order_status_cache(order_number, cache_status, parsed)
    return _to_order_status(order_number, parsed)


def _to_order_status(order_number: str, data: dict) -> OrderStatus:
    if data.get("error_code"):
        return OrderStatus(
            order_number=order_number,
            status=str(data["error_code"]),
            message=data.get("message"),
        )
    status = data.get("status") or "unparsed"
    message = data.get("message")
    if status == "unparsed" and data.get("raw_markdown"):
        message = message or "Kapruka returned a tracking response that could not be fully parsed."
    return OrderStatus(
        order_number=order_number,
        status=str(status),
        message=message,
        recipient=data.get("recipient"),
        items=data.get("items") or [],
        timeline=data.get("timeline") or data.get("progress") or [],
    )