"""Main conversation turn orchestration."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from backend.agents import (
    cart_agent,
    logistics_agent,
    memory_agent,
    order_agent,
    recommendation_agent,
    reflection_agent,
    response_composer,
    router_agent,
)
from backend.core.exceptions import AppError
from backend.core.logging import get_logger
from backend.db.repositories.message_repo import (
    count_user_messages,
)
from backend.memory import (
    semantic_memory,
    short_term,
)
from backend.memory.structured_memory import (
    get_profile_for_prompt,
)
from backend.schemas.chat import ChatResponse
from backend.services import checkout_service
from backend.utils.async_utils import (
    run_named,
)
from config.settings import (
    get_faqs_config,
    get_params_config,
)


logger = get_logger(__name__)


_PROFILE_RECALL_PATTERNS = [
    re.compile(
        r"\bwhat\s+do\s+you\s+know\s+about\s+my\s+(.+?)\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+do\s+you\s+remember\s+about\s+my\s+(.+?)\??$",
        re.IGNORECASE,
    ),
]


_RELATIONSHIP_ALIASES = {
    "dad": "father",
    "my dad": "father",
    "father": "father",
    "my father": "father",
    "mom": "mother",
    "mum": "mother",
    "my mom": "mother",
    "my mum": "mother",
    "mother": "mother",
    "my mother": "mother",
    "wife": "wife",
    "my wife": "wife",
    "husband": "husband",
    "my husband": "husband",
    "brother": "brother",
    "my brother": "brother",
    "sister": "sister",
    "my sister": "sister",
    "son": "son",
    "my son": "son",
    "daughter": "daughter",
    "my daughter": "daughter",
    "friend": "friend",
    "my friend": "friend",
}


def _detect_profile_recall(
    user_message: str,
) -> str | None:

    text = " ".join(
        user_message.strip().split()
    )

    for pattern in _PROFILE_RECALL_PATTERNS:

        match = pattern.search(
            text
        )

        if not match:
            continue

        raw_name = (
            match.group(1)
            .strip()
            .strip("?.!")
            .lower()
        )

        return _RELATIONSHIP_ALIASES.get(
            raw_name,
            raw_name,
        )

    return None


def _looks_like_recommendation_request(
    user_message: str,
) -> bool:

    text = user_message.lower()

    shopping_phrases = (
        "what are the best",
        "what are good",
        "what are suitable",
        "show me",
        "find me",
        "find some",
        "recommend",
        "recommendations",
        "suggest",
        "suggestions",
        "what should i buy",
        "what can i buy",
        "i want to buy",
        "i need to buy",
        "i want",
        "i need",
        "looking for",
        "best options",
        "suitable options",
        "options for",
    )

    return any(
        phrase in text
        for phrase in shopping_phrases
    )


def _apply_profile_recall_override(
    routing: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:

    recipient_name = (
        _detect_profile_recall(
            user_message
        )
    )

    if not recipient_name:
        return routing

    intents = list(
        routing.get(
            "intents",
            ["general_chat"],
        )
    )

    if "recall_profile" not in intents:
        intents.append(
            "recall_profile"
        )

    slots = dict(
        routing.get(
            "slots"
        )
        or {}
    )

    slots["recipient_name"] = (
        recipient_name
    )

    routing["intents"] = intents
    routing["slots"] = slots

    return routing



_GENERAL_DELIVERY_INFORMATION_PATTERNS = (
    re.compile(r"\bwhat\s+(?:are\s+)?(?:the\s+)?(?:delivery\s+)?areas\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+areas\b.{0,80}\b(?:deliver|delivery|one[- ]day|same[- ]day|next[- ]day|express)\b", re.IGNORECASE),
    re.compile(r"\b(?:one[- ]day|same[- ]day|next[- ]day)\s+delivery\s+(?:areas|zones|locations|coverage)\b", re.IGNORECASE),
    re.compile(r"\b(?:one[- ]day|same[- ]day|next[- ]day)\s+delivery\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are|does|do|can)\b.{0,45}\b(?:one[- ]day|same[- ]day|next[- ]day|express)\s+delivery\b.{0,45}\b(?:available|offered|possible|supported)\b", re.IGNORECASE),
    re.compile(r"\bdelivery\s+(?:areas|zones|coverage|policy|charges|fees|times|timeframes|windows|options)\b", re.IGNORECASE),
    re.compile(r"\bshipping\s+(?:areas|policy|charges|fees|times|options)\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+(?:does|do|can)\s+kapruka\s+deliver\b", re.IGNORECASE),
    re.compile(r"\bhow\s+long\b.{0,60}\bdeliver(?:y|ies)?\b", re.IGNORECASE),
    re.compile(r"\bkapruka['’]?s?\s+delivery\s+(?:policy|areas|zones|coverage|charges|fees|timeframes)\b", re.IGNORECASE),
)

_CONCRETE_DELIVERY_TIME_PATTERN = re.compile(
    r"\b(?:today|tomorrow|day after tomorrow|tonight|"
    r"20\d{2}-\d{2}-\d{2}|"
    r"on\s+\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9})\b",
    re.IGNORECASE,
)


def _looks_like_general_delivery_information_request(
    user_message: str,
    slots: dict[str, Any],
) -> bool:
    """Detect broad delivery policy/coverage questions without hijacking
    concrete city/date/product feasibility checks.
    """
    text = " ".join(str(user_message or "").split())
    if not text:
        return False

    # Concrete order checks remain on the existing MCP delivery_check path.
    if slots.get("delivery_date") or slots.get("product_id"):
        return False
    if _CONCRETE_DELIVERY_TIME_PATTERN.search(text):
        return False

    return any(
        pattern.search(text)
        for pattern in _GENERAL_DELIVERY_INFORMATION_PATTERNS
    )


_DELIVERY_POLICY_CONTEXT_PATTERN = re.compile(
    r"\b(?:one[- ]day|same[- ]day|next[- ]day)\b.{0,140}"
    r"\b(?:delivery|areas?|zones?|coverage|available|offered)\b"
    r"|\bdelivery\s+(?:areas?|zones?|coverage|policy|charges?|fees?|"
    r"times?|timeframes?|windows?|options?)\b",
    re.IGNORECASE | re.DOTALL,
)

_DELIVERY_LOCATION_PREFIX = re.compile(
    r"^(?:(?:what|how)\s+about|in|for|to)\s+",
    re.IGNORECASE,
)

_DELIVERY_LOCATION_FOLLOWUP_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 .,'’&()/-]{0,70}[A-Za-z0-9.)]$"
)


def _delivery_followup_query(
    user_message: str,
    short_term_context: str,
    slots: dict[str, Any],
) -> str | None:
    """Turn a city-only follow-up into a policy/coverage search query.

    Example: after asking which areas have one-day delivery, a user may reply
    only "Ahungalla". That is a continuation of the policy question, not a
    date-specific check that should prompt them for a delivery date.
    """
    text = " ".join(str(user_message or "").split()).strip()
    if not text or len(text) > 80:
        return None
    if slots.get("delivery_date") or slots.get("product_id"):
        return None
    if _CONCRETE_DELIVERY_TIME_PATTERN.search(text):
        return None

    context_lines = [
        line.strip()
        for line in str(short_term_context or "").splitlines()
        if line.strip()
    ]

    # short_term_context includes the current user message because the
    # orchestrator records it before routing. Remove that last line so this
    # decision is based on the immediately preceding conversation, not the
    # latest message itself.
    if context_lines and context_lines[-1].casefold().startswith("user:"):
        last_content = context_lines[-1][len("user:"):].strip()
        if last_content.casefold() == text.casefold():
            context_lines.pop()

    # Only inspect the most recent few messages. This avoids reviving an old
    # delivery topic much later in an otherwise unrelated conversation.
    recent_context = "\n".join(context_lines[-4:])
    if not _DELIVERY_POLICY_CONTEXT_PATTERN.search(recent_context):
        return None

    location_from_message = _DELIVERY_LOCATION_PREFIX.sub("", text).strip()
    location_from_message = location_from_message.strip(" ?.!\t\n")
    if not location_from_message:
        return None

    # Avoid treating short confirmations or requests for a date as city names.
    if location_from_message.casefold() in {
        "yes", "no", "yeah", "yep", "nope", "ok", "okay", "please",
        "today", "tomorrow", "date", "delivery date", "why", "how", "thanks",
    }:
        return None
    if len(location_from_message.split()) > 5:
        return None
    if not _DELIVERY_LOCATION_FOLLOWUP_RE.fullmatch(location_from_message):
        return None

    # Use the Router's city slot when it was extracted; otherwise use the
    # short location supplied directly by the user.
    city = str(slots.get("city") or location_from_message).strip()
    lowered_context = recent_context.casefold()
    if re.search(r"\bone[- ]day\b", lowered_context):
        service = "one-day delivery"
    elif re.search(r"\bsame[- ]day\b", lowered_context):
        service = "same-day delivery"
    elif re.search(r"\bnext[- ]day\b", lowered_context):
        service = "next-day delivery"
    elif re.search(r"\bexpress\b", lowered_context):
        service = "express delivery"
    else:
        service = "delivery coverage"

    return (
        f"According to Kapruka's current official delivery information, "
        f"is {service} available in {city}, Sri Lanka? The user is following "
        f"up on a previous question asking which areas support {service}. "
        "Explain what the official sources confirm; do not infer coverage "
        "from the city name alone."
    )


def _apply_delivery_information_override(
    routing: dict[str, Any],
    user_message: str,
    short_term_context: str = "",
) -> dict[str, Any]:
    """Route general delivery policy and its city follow-ups to Tavily search.

    Specific date/product feasibility checks remain on the existing MCP path.
    """
    slots = dict(routing.get("slots") or {})
    is_general_question = _looks_like_general_delivery_information_request(
        user_message,
        slots,
    )

    if is_general_question:
        slots["delivery_information_query"] = user_message
    else:
        followup_query = _delivery_followup_query(
            user_message,
            short_term_context,
            slots,
        )
        if not followup_query:
            return routing
        slots["delivery_information_query"] = followup_query

    intents = list(routing.get("intents") or ["general_chat"])
    intents = [intent for intent in intents if intent != "delivery_check"]
    if "delivery_information" not in intents:
        intents.append("delivery_information")

    routing["intents"] = intents
    routing["slots"] = slots
    return routing

def _ensure_recommendation_intent(
    routing: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:

    if not _looks_like_recommendation_request(
        user_message
    ):
        return routing

    intents = list(
        routing.get(
            "intents",
            ["general_chat"],
        )
    )

    protected_intents = {
        "add_to_cart",
        "remove_from_cart",
        "update_cart_quantity",
        "view_cart",
        "checkout",
        "track_order",
        "delivery_check",
        "delivery_information",
        "recall_profile",
    }

    if any(
        intent in protected_intents
        for intent in intents
    ):
        return routing

    if (
        "product_search"
        not in intents
    ):
        intents.append(
            "product_search"
        )

    routing["intents"] = intents

    return routing


async def prepare_turn(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> tuple[
    dict[str, Any],
    list[str],
    str | None,
]:

    await short_term.record_turn(
        session_id,
        "user",
        user_message,
    )

    # ---------------------------------------------------------
    # FAST PROFILE-RECALL SHORTCUT
    #
    # Explicit questions such as:
    #   "What do you know about my wife?"
    #   "What do you remember about my brother?"
    #   "Tell me what you know about my mother."
    #
    # do not need memory extraction, semantic prefetch, or router
    # classification. The recipient can be identified deterministically
    # and the durable profile can be retrieved directly.
    #
    # This shortcut intentionally applies only when the message is an
    # explicit profile-recall question. All other turns continue through
    # the existing pipeline unchanged.
    # ---------------------------------------------------------
    direct_recall_recipient = _detect_profile_recall(
        user_message
    )

    if direct_recall_recipient:
        intents = ["recall_profile"]
        slots = {
            "recipient_name": direct_recall_recipient,
        }

        profile_recall = await _run_profile_recall(
            user_id=user_id,
            slots=slots,
        )

        specialist_outputs = {
            "intents": intents,
            "memory_updates": [],
            "profile_recall": profile_recall,
        }

        return (
            specialist_outputs,
            intents,
            None,
        )

    # ---------------------------------------------------------
    # MEMORY CONTEXT + ROUTING
    #
    # Memory extraction is intentionally NOT part of the normal
    # recommendation critical path. For product-search/product-detail
    # turns that do not also explicitly save a preference, the durable
    # memory write is unnecessary work and is deferred/skipped.
    #
    # We still prefetch memory before routing because the router and
    # recommendation pipeline use the existing memory context.
    # ---------------------------------------------------------
    memory_updates: list[dict[str, Any]] = []

    memory_ctx = (
        await memory_agent.prefetch_context(
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
        )
    )

    routing = (
        await router_agent.classify(
            user_message=user_message,
            short_term_context=memory_ctx.get(
                "short_term_context",
                "",
            ),
            memory_context=memory_ctx.get(
                "memory_context",
                "",
            ),
        )
    )

    routing = _apply_profile_recall_override(
        routing,
        user_message,
    )

    routing = _apply_delivery_information_override(
        routing,
        user_message,
        short_term_context=memory_ctx.get(
            "short_term_context",
            "",
        ),
    )

    routing = _ensure_recommendation_intent(
        routing,
        user_message,
    )

    intents = list(
        routing.get(
            "intents",
            ["general_chat"],
        )
    )

    slots = dict(
        routing.get(
            "slots"
        )
        or {}
    )

    requires_reflection = bool(
        routing.get(
            "requires_reflection",
            False,
        )
    )

    recommendation_requested = (
        "product_search" in intents
        or "product_detail" in intents
    )

    # ---------------------------------------------------------
    # DURABLE MEMORY WRITE
    #
    # Preserve the existing same-turn behavior for explicit
    # save-preference messages. If the same message also requests
    # products, refresh the memory context after the write so the
    # recommendation engine sees the newly stored preference just as
    # it did before this latency optimization.
    #
    # For ordinary recommendation turns there is no memory write at
    # all, removing the failed/retried memory LLM call from the critical
    # path without changing recommendation logic.
    # ---------------------------------------------------------
    if (
        "save_preference" in intents
        or not recommendation_requested
    ):

        memory_updates = (
            await memory_agent.extract_and_store(
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
            )
        )

        if recommendation_requested:
            memory_ctx = (
                await memory_agent.prefetch_context(
                    user_id=user_id,
                    session_id=session_id,
                    user_message=user_message,
                )
            )

    if intents == ["faq"]:

        answer = _match_faq(
            user_message
        )

        if answer:

            return (
                {
                    "intents": intents,
                    "faq_reply": answer,
                    "memory_updates": (
                        memory_updates
                    ),
                },
                intents,
                answer,
            )

    specialist_outputs: dict[
        str,
        Any,
    ] = {
        "intents": intents,
        "memory_updates": memory_updates,
    }

    tasks: dict[
        str,
        Any,
    ] = {}

    if (
        "product_search" in intents
        or "product_detail" in intents
    ):

        tasks["catalog"] = (
            _run_recommendation(
                user_id=user_id,
                session_id=session_id,
                slots=slots,
                memory_ctx=memory_ctx,
                user_message=user_message,
            )
        )

    if "delivery_check" in intents:

        tasks["logistics"] = (
            _run_logistics(
                slots
            )
        )

    if "delivery_information" in intents:

        tasks["delivery_information"] = (
            _run_delivery_information(
                user_message=user_message,
            )
        )

    if "track_order" in intents:

        tasks["order_status"] = (
            _run_order_tracking(
                session_id,
                slots,
            )
        )

    if "recall_profile" in intents:

        tasks["profile_recall"] = (
            _run_profile_recall(
                user_id=user_id,
                slots=slots,
            )
        )

    if any(
        intent in intents
        for intent in (
            "add_to_cart",
            "remove_from_cart",
            "update_cart_quantity",
            "view_cart",
        )
    ):

        tasks["cart"] = (
            _run_cart(
                session_id,
                intents,
                slots,
            )
        )

    if "checkout" in intents:

        tasks["checkout"] = (
            _run_checkout(
                session_id,
                slots,
                user_message,
            )
        )

    results = await run_named(
        tasks
    )

    specialist_outputs.update(
        results
    )

    catalog_result = results.get(
        "catalog"
    )

    product_models: list[
        Any
    ] = []

    if isinstance(
        catalog_result,
        dict,
    ):

        product_models = list(
            catalog_result.get(
                "_product_models",
                [],
            )
            or []
        )

    # ---------------------------------------------------------
    # Safety reflection.
    # ---------------------------------------------------------

    if (
        product_models
        and slots.get(
            "recipient_name"
        )
    ):

        requirements = (
            catalog_result.get(
                "requirements",
                {},
            )
            if isinstance(
                catalog_result,
                dict,
            )
            else {}
        )

        excluded_terms = (
            requirements.get(
                "excluded_terms",
                [],
            )
        )

        if (
            excluded_terms
            or requires_reflection
        ):

            reflection_result = (
                await reflection_agent.run(
                    user_id=user_id,
                    session_id=session_id,
                    recipient_name=slots[
                        "recipient_name"
                    ],
                    draft_products=product_models,
                    original_query=(
                        slots.get(
                            "query",
                            user_message,
                        )
                    ),
                )
            )

            safe_products = (
                reflection_result[
                    "safe_products"
                ]
            )

            specialist_outputs[
                "reflection"
            ] = {
                "safe_products": [
                    product.model_dump()
                    for product in safe_products
                ],
                "violations": (
                    reflection_result[
                        "violations"
                    ]
                ),
                "revised": False,
            }

            if isinstance(
                specialist_outputs.get(
                    "catalog"
                ),
                dict,
            ):

                final_catalog = (
                    specialist_outputs[
                        "catalog"
                    ]
                )

                final_catalog[
                    "products"
                ] = [
                    product.model_dump()
                    for product
                    in safe_products
                ]

                final_catalog[
                    "_product_models"
                ] = safe_products

                final_catalog[
                    "shown_count"
                ] = len(
                    safe_products
                )

    _synchronize_final_catalog(
        specialist_outputs
    )

    return (
        specialist_outputs,
        intents,
        None,
    )


def _synchronize_final_catalog(
    specialist_outputs: dict[str, Any],
) -> None:

    catalog = specialist_outputs.get(
        "catalog"
    )

    if not isinstance(
        catalog,
        dict,
    ):
        return

    products = catalog.get(
        "products",
        [],
    )

    if (
        not isinstance(
            products,
            list,
        )
        or catalog.get(
            "status"
        ) == "no_exact_match"
    ):
        products = []

    final_products: list[
        dict[str, Any]
    ] = []

    seen_ids: set[str] = set()

    for product in products:

        if not isinstance(
            product,
            dict,
        ):
            continue

        product_id = str(
            product.get(
                "product_id",
                "",
            )
        ).strip()

        if not product_id:
            continue

        if product_id in seen_ids:
            continue

        seen_ids.add(
            product_id
        )

        final_products.append(
            product
        )

    recommendation_cfg = get_params_config().get(
        "recommendation",
        {},
    )
    final_limit = min(
        max(
            int(
                recommendation_cfg.get(
                    "final_limit",
                    12,
                )
            ),
            1,
        ),
        12,
    )
    final_products = final_products[
        :final_limit
    ]

    catalog[
        "products"
    ] = final_products

    catalog[
        "shown_count"
    ] = len(
        final_products
    )

    catalog[
        "_product_models"
    ] = [
        product
        for product in (
            catalog.get(
                "_product_models",
                [],
            )
            or []
        )
        if getattr(
            product,
            "product_id",
            None,
        ) in seen_ids
    ]


async def handle_turn(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> ChatResponse:

    (
        specialist_outputs,
        intents,
        faq_reply,
    ) = await prepare_turn(
        user_id=user_id,
        session_id=session_id,
        user_message=user_message,
    )

    if faq_reply is not None:

        reply = faq_reply
        ui_actions = []

    else:

        (
            reply,
            ui_actions,
        ) = await response_composer.compose(
            specialist_outputs=specialist_outputs,
            user_message=user_message,
        )

    await short_term.record_turn(
        session_id,
        "assistant",
        reply,
        intent=",".join(
            intents
        ),
    )

    asyncio.create_task(
        _maybe_index_summary(
            session_id
        )
    )

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        ui_actions=ui_actions,
    )


async def _run_recommendation(
    *,
    user_id: str,
    session_id: str,
    slots: dict,
    memory_ctx: dict,
    user_message: str,
) -> dict:

    return await recommendation_agent.recommend(
        user_id=user_id,
        session_id=session_id,
        user_message=user_message,
        memory_context=memory_ctx.get(
            "memory_context",
            "",
        ),
        router_slots=slots,
    )


async def _run_profile_recall(
    *,
    user_id: str,
    slots: dict,
) -> dict:

    recipient_name = slots.get(
        "recipient_name"
    )

    if not recipient_name:

        return {
            "error": (
                "No recipient was identified."
            )
        }

    return {
        "recipient_name": recipient_name,
        "profile_text": (
            await get_profile_for_prompt(
                user_id=user_id,
                name=recipient_name,
            )
        ),
    }


async def _run_delivery_information(
    *,
    user_message: str,
) -> dict:
    """Use Tavily-backed logistics search for general delivery-policy questions."""
    return await logistics_agent.search_delivery_information(
        user_question=user_message,
    )


async def _run_logistics(
    slots: dict,
) -> dict:

    city = slots.get(
        "city"
    )

    date = slots.get(
        "delivery_date"
    )

    if not city or not date:

        return {
            "error": (
                "city and delivery_date "
                "are required."
            )
        }

    return await logistics_agent.check_delivery(
        city=city,
        delivery_date=date,
        product_id=slots.get(
            "product_id"
        ),
    )


async def _run_order_tracking(
    session_id: str,
    slots: dict,
) -> dict:

    order_number = slots.get(
        "order_number"
    )

    if not order_number:

        return {
            "error": (
                "No order number was provided."
            )
        }

    from backend.db.repositories.order_repo import (
        get_order_for_session,
    )

    order = (
        await get_order_for_session(
            session_id,
            order_number,
        )
    )

    if order is None:

        return {
            "error": "Order not found."
        }

    return (
        await order_agent.track_order(
            order_number
        )
    ).model_dump()


async def _run_cart(
    session_id: str,
    intents: list[str],
    slots: dict,
) -> dict:

    try:

        if (
            "add_to_cart" in intents
            and slots.get(
                "product_id"
            )
        ):

            view = (
                await cart_agent.add_item(
                    session_id=session_id,
                    product_id=slots[
                        "product_id"
                    ],
                    quantity=int(
                        slots.get(
                            "quantity",
                            1,
                        )
                    ),
                    recipient_name=slots.get(
                        "recipient_name"
                    ),
                )
            )

        elif (
            "remove_from_cart" in intents
            and slots.get(
                "product_id"
            )
        ):

            view = (
                await cart_agent.remove_item(
                    session_id=session_id,
                    product_id=slots[
                        "product_id"
                    ],
                )
            )

        elif (
            "update_cart_quantity" in intents
            and slots.get(
                "product_id"
            )
        ):

            view = (
                await cart_agent.update_quantity(
                    session_id=session_id,
                    product_id=slots[
                        "product_id"
                    ],
                    quantity=int(
                        slots.get(
                            "quantity",
                            1,
                        )
                    ),
                )
            )

        else:

            view = (
                await cart_agent.view_cart(
                    session_id=session_id
                )
            )

        return {
            "cart": view.model_dump()
        }

    except AppError as exc:

        return {
            "error": exc.message
        }


async def _run_checkout(
    session_id: str,
    slots: dict,
    user_message: str,
) -> dict:

    try:

        key = (
            f"chat:{session_id}:"
            f"{abs(hash(user_message.strip().lower()))}"
        )

        return await checkout_service.checkout(
            session_id=session_id,
            slots=slots,
            idempotency_key=key,
        )

    except AppError as exc:

        return {
            "error": exc.message,
            "code": exc.code,
        }


async def _maybe_index_summary(
    session_id: str,
) -> None:
    """
    Create a long-term conversation summary.

    Temporary financial information MUST NOT enter this summary.
    """

    try:

        cfg = get_params_config()[
            "memory"
        ]

        threshold = int(
            cfg.get(
                "summarize_after_turns",
                0,
            )
        )

        if threshold <= 0:
            return

        count = (
            await count_user_messages(
                session_id
            )
        )

        if (
            count == 0
            or count % threshold != 0
        ):
            return

        context = (
            await short_term.get_context(
                session_id
            )
        )

        from backend.llm.openrouter_client import (
            get_openrouter_client,
        )

        client = (
            get_openrouter_client()
        )

        summary_prompt = """
Summarize ONLY durable user/recipient information that should remain useful
across future sessions.

Keep:
- recipient relationships
- recipient allergies
- recipient likes
- recipient dislikes
- stable long-term preferences
- stable recurring shopping preferences

DO NOT store or summarize temporary/current-session information such as:
- budgets
- maximum spending limits
- minimum spending limits
- prices the user can currently afford
- current financial status
- one-time purchase limits
- current shopping quantities
- current delivery dates
- current product choices
- temporary search requests

A budget such as "I can spend 5000 LKR today" is NOT durable memory.

Return only the durable summary.
"""

        response = await client.chat_completion(
            task="memory_agent",
            messages=[
                {
                    "role": "system",
                    "content": summary_prompt,
                },
                {
                    "role": "user",
                    "content": context,
                },
            ],
        )

        summary = (
            client.extract_text(
                response
            ).strip()
        )

        if not summary:
            return

        # Final defensive check before writing to semantic memory.
        budget_terms = (
            "budget",
            "maximum spend",
            "maximum spending",
            "spending limit",
            "price limit",
            "financial limit",
            "financial status",
            "lkr",
            "rs ",
            "rupees",
            "usd",
            "eur",
            "gbp",
        )

        lowered = summary.lower()

        if any(
            term in lowered
            for term in budget_terms
        ):
            logger.warning(
                "Discarding semantic summary because it contains "
                "temporary financial information."
            )
            return

        await semantic_memory.index_conversation_summary(
            session_id,
            summary,
        )

    except Exception as exc:

        logger.warning(
            "Conversation summary indexing failed: %s",
            exc,
        )


def _match_faq(
    user_message: str,
) -> str | None:

    text = user_message.lower()

    for faq in get_faqs_config().get(
        "faqs",
        [],
    ):

        if any(
            str(
                trigger
            ).lower()
            in text
            for trigger in faq.get(
                "triggers",
                [],
            )
        ):
            return faq[
                "answer"
            ].strip()

    return None
