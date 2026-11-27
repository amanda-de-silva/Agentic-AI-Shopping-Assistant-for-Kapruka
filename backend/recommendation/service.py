"""End-to-end constraint-first recommendation service."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.agents import catalog_agent
from backend.core.logging import get_logger
from backend.db.repositories.profile_repo import find_profile_by_name
from backend.db.repositories.recommendation_state_repo import (
    get_recommendation_state,
    save_recommendation_state,
)
from backend.memory.recipient_resolution import (
    resolve_recipient_reference,
)
from backend.memory.structured_memory import (
    get_profile_for_prompt,
)
from backend.observability.tracing import traced
from backend.recommendation.constraint_compiler import (
    build_recipient_context,
    compile_spec,
)
from backend.recommendation.eligibility import (
    evaluate_all,
)
from backend.recommendation.models import (
    ProductFacts,
    ShoppingSpec,
)
from backend.recommendation.product_normalizer import (
    normalize_products,
)
from backend.recommendation.query_planner import (
    CandidateRetrieval,
    retrieve_candidates,
)
from backend.recommendation.ranker import (
    rank_products,
)
from backend.recommendation.requirement_parser import (
    parse_requirements,
)
from config.settings import (
    get_params_config,
)

logger = get_logger(__name__)


@dataclass
class _RecommendationSessionState:
    spec: ShoppingSpec
    shown_product_ids: set[str] = field(
        default_factory=set
    )
    search_cursors: dict[str, str] = field(
        default_factory=dict
    )
    retrieval_category: str | None = None


@dataclass
class _RetrievalOutcome:
    eligible_facts: list[ProductFacts] = field(default_factory=list)
    details: list[Any] = field(default_factory=list)
    next_cursors: dict[str, str] = field(default_factory=dict)
    retrieval_category: str | None = None
    rejected: list[dict[str, Any]] = field(default_factory=list)
    seen_candidate_ids: set[str] = field(default_factory=set)
    stats: dict[str, Any] = field(default_factory=dict)


_RECOMMENDATION_SESSION_STATE: dict[
    str,
    _RecommendationSessionState,
] = {}

_MAX_RECOMMENDATION_SESSIONS = 512


_CONTINUATION_PATTERNS = (
    re.compile(
        r"^\s*show\s+more(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*more(?:\s+options?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*more\s+options?(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*show\s+me\s+more(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*give\s+me\s+more(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*anything\s+else(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*other\s+options?(?:\s+.+?)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*more\s+"
        r"(?:cakes?|gifts?|flowers?|watches?|"
        r"phones?|chocolates?|hampers?|"
        r"sarees?|perfumes?)"
        r"\s*\??\s*$",
        re.IGNORECASE,
    ),
)


def _is_recommendation_continuation(
    user_message: str,
) -> bool:
    text = " ".join(
        str(user_message)
        .strip()
        .split()
    )

    if not text:
        return False

    return any(
        pattern.match(text)
        for pattern in _CONTINUATION_PATTERNS
    )


def _clone_continuation_spec(
    previous: ShoppingSpec,
) -> ShoppingSpec:
    data = previous.model_dump()

    # Budget is current-turn-only and is intentionally not inherited.
    data["budget"] = None

    return ShoppingSpec.model_validate(
        data
    )


def _copy_state(
    state: _RecommendationSessionState,
) -> _RecommendationSessionState:
    return _RecommendationSessionState(
        spec=ShoppingSpec.model_validate(
            state.spec.model_dump()
        ),
        shown_product_ids=set(
            state.shown_product_ids
        ),
        search_cursors=dict(
            state.search_cursors
        ),
        retrieval_category=(
            state.retrieval_category
        ),
    )


def _remember_recommendation_state(
    session_id: str,
    spec: ShoppingSpec,
    shown_product_ids: set[str],
    *,
    search_cursors: dict[str, str] | None = None,
    retrieval_category: str | None = None,
) -> None:
    state = _RECOMMENDATION_SESSION_STATE.get(
        session_id
    )

    if state is None:
        _RECOMMENDATION_SESSION_STATE[
            session_id
        ] = _RecommendationSessionState(
            spec=ShoppingSpec.model_validate(
                spec.model_dump()
            ),
            shown_product_ids=set(
                shown_product_ids
            ),
            search_cursors=dict(
                search_cursors or {}
            ),
            retrieval_category=(
                retrieval_category
            ),
        )
    else:
        state.spec = ShoppingSpec.model_validate(
            spec.model_dump()
        )

        state.shown_product_ids.update(
            shown_product_ids
        )

        if search_cursors is not None:
            state.search_cursors = dict(
                search_cursors
            )

        if retrieval_category is not None:
            state.retrieval_category = (
                retrieval_category
            )

    if (
        len(
            _RECOMMENDATION_SESSION_STATE
        )
        > _MAX_RECOMMENDATION_SESSIONS
    ):
        oldest_session_id = next(
            iter(
                _RECOMMENDATION_SESSION_STATE
            )
        )

        if oldest_session_id != session_id:
            _RECOMMENDATION_SESSION_STATE.pop(
                oldest_session_id,
                None,
            )


def _get_continuation_state(
    session_id: str,
) -> _RecommendationSessionState | None:
    state = _RECOMMENDATION_SESSION_STATE.get(
        session_id
    )

    return (
        _copy_state(state)
        if state is not None
        else None
    )


async def _load_recommendation_state(
    session_id: str,
) -> _RecommendationSessionState | None:
    persistent = await get_recommendation_state(
        session_id
    )

    if isinstance(
        persistent,
        dict,
    ):
        raw_spec = persistent.get(
            "spec"
        )

        raw_ids = persistent.get(
            "shown_product_ids",
            [],
        )

        raw_cursors = persistent.get(
            "search_cursors",
            {},
        )

        raw_category = persistent.get(
            "retrieval_category"
        )

        if isinstance(
            raw_spec,
            dict,
        ):
            try:
                return _RecommendationSessionState(
                    spec=ShoppingSpec.model_validate(
                        raw_spec
                    ),
                    shown_product_ids={
                        str(value)
                        for value
                        in (
                            raw_ids or []
                        )
                    },
                    search_cursors={
                        str(key): str(value)
                        for key, value
                        in (
                            raw_cursors.items()
                            if isinstance(
                                raw_cursors,
                                dict,
                            )
                            else []
                        )
                        if value
                    },
                    retrieval_category=(
                        str(raw_category)
                        if raw_category
                        else None
                    ),
                )

            except Exception as exc:
                logger.warning(
                    "Invalid persisted recommendation state "
                    "for session %s: %s",
                    session_id,
                    exc,
                )

    return _get_continuation_state(
        session_id
    )


async def _save_recommendation_state(
    session_id: str,
    spec: ShoppingSpec,
    shown_product_ids: set[str],
    *,
    search_cursors: dict[str, str] | None = None,
    retrieval_category: str | None = None,
) -> None:
    _remember_recommendation_state(
        session_id=session_id,
        spec=spec,
        shown_product_ids=shown_product_ids,
        search_cursors=search_cursors,
        retrieval_category=retrieval_category,
    )

    payload = {
        "spec": spec.model_dump(
            mode="json"
        ),
        "shown_product_ids": sorted(
            {
                str(value)
                for value
                in shown_product_ids
            }
        ),
        "search_cursors": {
            str(key): str(value)
            for key, value
            in (
                search_cursors or {}
            ).items()
            if value
        },
        "retrieval_category": (
            retrieval_category
        ),
    }

    await save_recommendation_state(
        session_id=session_id,
        state=payload,
    )


async def _retrieve_and_enrich(
    spec: ShoppingSpec,
    *,
    fallback_query: str | None = None,
    excluded_product_ids: set[str] | None = None,
    continuation_cursors: dict[str, str] | None = None,
    preferred_category: str | None = None,
    continuation: bool = False,
) -> tuple[
    list[Any],
    dict[str, str],
    str | None,
    set[str],
    list[str],
]:
    """Retrieve one search round and explicitly account for enrichment loss."""
    cfg = get_params_config().get(
        "recommendation",
        {},
    )

    search_limit = max(
        int(
            cfg.get(
                "search_limit",
                12,
            )
        ),
        1,
    )

    retrieval: CandidateRetrieval = (
        await retrieve_candidates(
            spec,
            search=catalog_agent.search_products,
            search_page=(
                catalog_agent.search_products_page
            ),
            search_limit=search_limit,
            fallback_query=fallback_query,
            continuation_cursors=(
                continuation_cursors
            ),
            preferred_category=(
                preferred_category
            ),
        )
    )

    candidates = list(
        retrieval.products
    )

    excluded_ids = {
        str(value)
        for value
        in (
            excluded_product_ids
            or set()
        )
    }

    if excluded_ids:
        candidates = [
            product
            for product in candidates
            if str(
                getattr(
                    product,
                    "product_id",
                    "",
                )
            )
            not in excluded_ids
        ]

    candidate_ids = {
        str(
            getattr(
                product,
                "product_id",
                "",
            )
        )
        for product in candidates
        if getattr(product, "product_id", None)
    }

    if not candidates:
        return (
            [],
            retrieval.next_cursors,
            retrieval.category,
            set(),
            [],
        )

    max_candidates = max(
        int(
            cfg.get(
                "max_enrichment_candidates",
                30,
            )
        ),
        1,
    )

    # The query planner can produce several query x page-size results in one
    # round. Keep a hard cap for latency, but record every candidate that was
    # considered so the caller can account for candidates that could not be
    # enriched. The next pagination round will continue from the returned
    # cursors.
    candidates = candidates[:max_candidates]

    candidate_ids = {
        str(
            getattr(
                product,
                "product_id",
                "",
            )
        )
        for product in candidates
        if getattr(product, "product_id", None)
    }

    details, failed_ids = (
        await catalog_agent.enrich_products_with_failures(
            candidates,
            max_concurrency=max(
                int(
                    cfg.get(
                        "enrichment_concurrency",
                        8,
                    )
                ),
                1,
            ),
            max_attempts=max(
                int(
                    cfg.get(
                        "enrichment_max_attempts",
                        3,
                    )
                ),
                1,
            ),
        )
    )

    return (
        details,
        retrieval.next_cursors,
        retrieval.category,
        candidate_ids,
        failed_ids,
    )


async def _retrieve_until_target(
    *,
    retrieval_spec: ShoppingSpec,
    eligibility_spec: ShoppingSpec,
    target_count: int,
    fallback_query: str | None,
    excluded_product_ids: set[str] | None,
    continuation_cursors: dict[str, str] | None,
    preferred_category: str | None,
    continuation: bool,
) -> _RetrievalOutcome:
    """Paginate until enough hard-eligible products are available.

    Retrieval and eligibility are deliberately interleaved. We do not stop at
    the first non-empty search response because raw catalog candidates are not
    equivalent to eligible recommendations.
    """
    cfg = get_params_config().get(
        "recommendation",
        {},
    )

    target_count = max(
        int(target_count),
        1,
    )

    max_rounds = max(
        int(
            cfg.get(
                "max_retrieval_rounds",
                4,
            )
        ),
        1,
    )

    max_candidate_budget = max(
        int(
            cfg.get(
                "max_candidate_budget",
                72,
            )
        ),
        target_count,
    )

    normalizer_batch_size = max(
        int(
            cfg.get(
                "normalizer_batch_size",
                8,
            )
        ),
        1,
    )

    eligible_by_id: dict[str, ProductFacts] = {}
    detail_by_id: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []

    seen_candidate_ids = {
        str(value)
        for value
        in (excluded_product_ids or set())
    }

    cursors = dict(
        continuation_cursors or {}
    )
    retrieval_category = preferred_category

    rounds = 0
    raw_candidates = 0
    enriched_candidates = 0
    normalized_candidates = 0
    enrichment_failures = 0
    pagination_stalled = False

    excluded_terms = [
        item.term
        for item in eligibility_spec.excluded_attributes
    ] + list(eligibility_spec.excluded_components)

    while (
        len(eligible_by_id) < target_count
        and rounds < max_rounds
        and len(seen_candidate_ids) < max_candidate_budget
    ):
        old_cursor_signature = tuple(
            sorted(cursors.items())
        )
        old_seen_count = len(seen_candidate_ids)

        (
            details,
            next_cursors,
            round_category,
            candidate_ids,
            failed_ids,
        ) = await _retrieve_and_enrich(
            retrieval_spec,
            fallback_query=fallback_query,
            excluded_product_ids=seen_candidate_ids,
            continuation_cursors=cursors,
            preferred_category=retrieval_category,
            continuation=continuation,
        )

        rounds += 1
        cursors = dict(next_cursors)

        if round_category is not None:
            retrieval_category = round_category

        new_candidate_ids = {
            str(value)
            for value in candidate_ids
            if str(value) not in seen_candidate_ids
        }
        seen_candidate_ids.update(
            candidate_ids
        )
        raw_candidates += len(new_candidate_ids)
        enriched_candidates += len(details)
        enrichment_failures += len(failed_ids)

        for product_id in failed_ids:
            rejected.append(
                {
                    "product_id": str(product_id),
                    "reasons": [
                        "enrichment_failed"
                    ],
                }
            )

        if details:
            facts = await normalize_products(
                details,
                excluded_terms=excluded_terms,
                batch_size=normalizer_batch_size,
            )
            normalized_candidates += len(facts)

            eligible, round_rejected = (
                _finalize_eligibility(
                    facts,
                    eligibility_spec,
                )
            )

            rejected.extend(
                round_rejected
            )

            for fact in eligible:
                eligible_by_id[
                    str(fact.product_id)
                ] = fact

            for detail in details:
                detail_by_id[
                    str(detail.product_id)
                ] = detail

        new_cursor_signature = tuple(
            sorted(cursors.items())
        )

        if len(eligible_by_id) >= target_count:
            break

        # A page can overlap the previous page. If neither candidate IDs nor
        # cursors advance, another loop would only repeat the same work.
        if (
            len(seen_candidate_ids) == old_seen_count
            and new_cursor_signature == old_cursor_signature
        ):
            pagination_stalled = True
            break

        if not cursors:
            break

    stats = {
        "target_recommendations": target_count,
        "retrieval_rounds": rounds,
        "raw_candidates": raw_candidates,
        "enriched_candidates": enriched_candidates,
        "normalized_candidates": normalized_candidates,
        "eligible_candidates": len(eligible_by_id),
        "enrichment_failures": enrichment_failures,
        "remaining_cursors": len(cursors),
        "pagination_stalled": pagination_stalled,
        "search_exhausted": not bool(cursors),
    }

    eligible_facts = list(
        eligible_by_id.values()
    )

    details = [
        detail_by_id[str(fact.product_id)]
        for fact in eligible_facts
        if str(fact.product_id) in detail_by_id
    ]

    logger.info(
        "Recommendation retrieval rounds=%d raw=%d enriched=%d "
        "normalized=%d eligible=%d target=%d remaining_cursors=%d "
        "enrichment_failures=%d stalled=%s",
        rounds,
        raw_candidates,
        enriched_candidates,
        normalized_candidates,
        len(eligible_facts),
        target_count,
        len(cursors),
        enrichment_failures,
        pagination_stalled,
    )

    return _RetrievalOutcome(
        eligible_facts=eligible_facts,
        details=details,
        next_cursors=cursors,
        retrieval_category=retrieval_category,
        rejected=rejected,
        seen_candidate_ids=seen_candidate_ids,
        stats=stats,
    )



def _finalize_eligibility(
    facts: list[ProductFacts],
    spec: ShoppingSpec,
) -> tuple[
    list[ProductFacts],
    list[dict[str, Any]],
]:
    results = evaluate_all(
        facts,
        spec,
    )

    by_id = {
        fact.product_id: fact
        for fact in facts
    }

    eligible: list[
        ProductFacts
    ] = []

    rejected: list[
        dict[str, Any]
    ] = []

    for result in results:
        fact = by_id[
            result.product_id
        ]

        if result.eligible:
            eligible.append(
                fact
            )
        else:
            rejected.append(
                {
                    "product_id": (
                        result.product_id
                    ),
                    "reasons": (
                        result.hard_failures
                    ),
                }
            )

    return (
        eligible,
        rejected,
    )


def _get_final_limit(
    cfg: dict[str, Any],
) -> int:
    configured_limit = int(
        cfg.get(
            "final_limit",
            12,
        )
    )

    return min(
        max(
            configured_limit,
            1,
        ),
        12,
    )


@traced(
    "recommendation.service"
)
async def recommend(
    *,
    user_id: str,
    session_id: str,
    user_message: str,
    memory_context: str,
    router_slots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    router_slots = (
        router_slots or {}
    )

    continuation = (
        _is_recommendation_continuation(
            user_message
        )
    )

    previous_state = (
        await _load_recommendation_state(
            session_id
        )
        if continuation
        else None
    )

    if previous_state is not None:
        spec = _clone_continuation_spec(
            previous_state.spec
        )

        previous_shown_ids = set(
            previous_state.shown_product_ids
        )

        previous_cursors = dict(
            previous_state.search_cursors
        )

        previous_category = (
            previous_state.retrieval_category
        )

    else:
        spec = await parse_requirements(
            user_message=user_message,
            memory_context=memory_context,
            router_slots=router_slots,
        )

        previous_shown_ids = set()
        previous_cursors = {}
        previous_category = None

    resolved_recipient = (
        await resolve_recipient_reference(
            user_id=user_id,
            session_id=session_id,
            raw_name=(
                spec.recipient_name
                or router_slots.get(
                    "recipient_name"
                )
            ),
        )
    )

    spec.recipient_name = (
        resolved_recipient
    )

    profile = None
    recipient_context = None

    if resolved_recipient:
        profile = await find_profile_by_name(
            user_id=user_id,
            name=resolved_recipient,
        )

        if profile:
            recipient_context = (
                build_recipient_context(
                    name=profile.name,
                    relationship=(
                        profile.relationship
                        or profile.name
                    ),
                    likes=profile.likes,
                    dislikes=profile.dislikes,
                    allergies=profile.allergies,
                )
            )

            spec = compile_spec(
                spec,
                recipient_context,
            )

    fallback_query = (
        router_slots.get("query")
        if router_slots.get("query")
        else None
    )

    cfg = get_params_config().get(
        "recommendation",
        {},
    )

    final_limit = _get_final_limit(
        cfg
    )

    target_count = min(
        final_limit,
        max(
            int(
                cfg.get(
                    "target_recommendations",
                    12,
                )
            ),
            1,
        ),
    )

    normalizer_batch_size = max(
        int(
            cfg.get(
                "normalizer_batch_size",
                8,
            )
        ),
        1,
    )

    first_outcome = await _retrieve_until_target(
        retrieval_spec=spec,
        eligibility_spec=spec,
        target_count=target_count,
        fallback_query=fallback_query,
        excluded_product_ids=(
            previous_shown_ids
            if continuation
            else None
        ),
        continuation_cursors=(
            previous_cursors
            if continuation
            else None
        ),
        preferred_category=(
            previous_category
            if continuation
            else None
        ),
        continuation=continuation,
    )

    eligible_facts = list(
        first_outcome.eligible_facts
    )
    details = list(
        first_outcome.details
    )
    rejected = list(
        first_outcome.rejected
    )
    next_cursors = dict(
        first_outcome.next_cursors
    )
    retrieval_category = (
        first_outcome.retrieval_category
    )
    seen_candidate_ids = set(
        first_outcome.seen_candidate_ids
    )
    retrieval_stats = {
        "primary": first_outcome.stats,
    }

    # If the exact query family did not produce enough eligible products after
    # pagination, broaden ONLY the retrieval query. Original hard constraints
    # are still evaluated against every broadened candidate.
    if (
        len(eligible_facts) < target_count
        and spec.product_family
    ):
        broader = ShoppingSpec.model_validate(
            spec.model_dump()
        )

        broader.search_queries = [
            spec.product_family
        ]
        broader.occasion = None
        broader.required_attributes = []
        broader.required_components = []

        fallback_outcome = await _retrieve_until_target(
            retrieval_spec=broader,
            eligibility_spec=spec,
            target_count=target_count - len(eligible_facts),
            fallback_query=spec.product_family,
            excluded_product_ids=seen_candidate_ids,
            continuation_cursors=next_cursors,
            preferred_category=retrieval_category,
            continuation=continuation,
        )

        retrieval_stats[
            "broader"
        ] = fallback_outcome.stats

        rejected.extend(
            fallback_outcome.rejected
        )

        fact_by_id = {
            str(fact.product_id): fact
            for fact in eligible_facts
        }
        detail_by_id = {
            str(detail.product_id): detail
            for detail in details
        }

        for fact in fallback_outcome.eligible_facts:
            fact_by_id[
                str(fact.product_id)
            ] = fact

        for detail in fallback_outcome.details:
            detail_by_id[
                str(detail.product_id)
            ] = detail

        eligible_facts = list(
            fact_by_id.values()
        )
        details = [
            detail_by_id[str(fact.product_id)]
            for fact in eligible_facts
            if str(fact.product_id) in detail_by_id
        ]
        next_cursors = dict(
            fallback_outcome.next_cursors
        )
        retrieval_category = (
            fallback_outcome.retrieval_category
            or retrieval_category
        )
        seen_candidate_ids.update(
            fallback_outcome.seen_candidate_ids
        )

    if not eligible_facts:
        profile_text = None

        if resolved_recipient:
            profile_text = (
                await get_profile_for_prompt(
                    user_id=user_id,
                    name=resolved_recipient,
                )
            )

        await _save_recommendation_state(
            session_id=session_id,
            spec=spec,
            shown_product_ids=(
                previous_shown_ids
                if continuation
                else set()
            ),
            search_cursors=next_cursors,
            retrieval_category=retrieval_category,
        )

        return {
            "status": "no_exact_match",
            "requirements": spec.model_dump(),
            "recipient_profile": profile_text,
            "products": [],
            "_product_models": [],
            "eligibility": [],
            "rejected": rejected,
            "shown_count": 0,
            "requested_limit": final_limit,
            "target_recommendations": target_count,
            "retrieval_stats": retrieval_stats,
        }

    # Final continuation guard.
    if (
        continuation
        and previous_shown_ids
    ):
        eligible_facts = [
            fact
            for fact in eligible_facts
            if str(
                fact.product_id
            )
            not in previous_shown_ids
        ]

    ranked = await rank_products(
        products=eligible_facts,
        spec=spec,
        recipient=recipient_context,
        user_message=user_message,
        final_limit=min(final_limit, target_count),
    )

    if (
        continuation
        and previous_shown_ids
    ):
        ranked = [
            product
            for product in ranked
            if str(
                product.product_id
            )
            not in previous_shown_ids
        ]

    final_ids = {
        fact.product_id
        for fact in ranked
    }

    verified = [
        fact
        for fact in eligible_facts
        if (
            fact.product_id
            in final_ids
            and evaluate_all(
                [fact],
                spec,
            )[0].eligible
        )
    ]

    order = {
        product.product_id: index
        for index, product
        in enumerate(ranked)
    }

    verified.sort(
        key=lambda product: order.get(
            product.product_id,
            9999,
        )
    )

    detail_by_id = {
        str(detail.product_id): detail
        for detail in details
    }

    final_details = [
        detail_by_id[
            fact.product_id
        ]
        for fact in verified
        if fact.product_id
        in detail_by_id
    ]

    seen_final_ids: set[str] = set()
    deduped_final_details: list[Any] = []

    for detail in final_details:
        product_id = str(
            detail.product_id
        )

        if product_id in seen_final_ids:
            continue

        if (
            continuation
            and product_id
            in previous_shown_ids
        ):
            continue

        seen_final_ids.add(
            product_id
        )

        deduped_final_details.append(
            detail
        )

        if (
            len(
                deduped_final_details
            )
            >= final_limit
        ):
            break

    final_details = (
        deduped_final_details
    )

    profile_text = None

    if resolved_recipient:
        profile_text = (
            await get_profile_for_prompt(
                user_id=user_id,
                name=resolved_recipient,
            )
        )

    final_eligibility = evaluate_all(
        [
            fact
            for fact
            in verified
            if fact.product_id
            in {
                str(
                    detail.product_id
                )
                for detail
                in final_details
            }
        ],
        spec,
    )

    final_product_ids = {
        str(product.product_id)
        for product in final_details
    }

    all_shown_product_ids = (
        previous_shown_ids
        | final_product_ids
        if continuation
        else final_product_ids
    )

    await _save_recommendation_state(
        session_id=session_id,
        spec=spec,
        shown_product_ids=(
            all_shown_product_ids
        ),
        search_cursors=(
            next_cursors
        ),
        retrieval_category=(
            retrieval_category
        ),
    )

    return {
        "status": "recommendations_found",
        "requirements": spec.model_dump(),
        "recipient_profile": profile_text,
        "products": [
            product.model_dump()
            for product
            in final_details
        ],
        "_product_models": final_details,
        "eligibility": [
            item.model_dump()
            for item
            in final_eligibility
        ],
        "rejected": rejected,
        "shown_count": len(
            final_details
        ),
        "requested_limit": final_limit,
        "target_recommendations": target_count,
        "retrieval_stats": retrieval_stats,
    }
