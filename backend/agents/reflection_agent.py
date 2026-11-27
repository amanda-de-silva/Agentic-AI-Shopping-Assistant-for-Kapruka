"""Compatibility reflection stage.

The recommendation service now performs the complete deterministic eligibility
and final verification pipeline before products reach the orchestrator. This
module remains as a compatibility boundary for the existing orchestrator but
never broadens, replaces, or re-ranks the already verified candidate list.
"""

from __future__ import annotations

from backend.schemas.product import ProductSummary


async def run(
    *,
    user_id: str,
    session_id: str,
    recipient_name: str,
    draft_products: list[ProductSummary],
    original_query: str,
) -> dict:
    del (
        user_id,
        session_id,
        recipient_name,
        original_query,
    )

    return {
        "safe_products": list(
            draft_products
        ),
        "violations": [],
        "revised": False,
    }