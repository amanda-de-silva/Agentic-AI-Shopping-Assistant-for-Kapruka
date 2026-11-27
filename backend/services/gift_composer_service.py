"""Read-only gift-basket planning built on the existing recommendation stack.

This service deliberately does not call cart mutation or checkout APIs. It uses
Kapruka catalog search, enrichment, product normalization, hard eligibility,
ranking, recipient memory and delivery checks, then returns a preview only.
"""
from __future__ import annotations

import asyncio
import itertools
import re
from typing import Any

from backend.agents import catalog_agent
from backend.core.logging import get_logger
from backend.db.repositories.profile_repo import find_profile_by_name, list_profiles
from backend.memory.recipient_resolution import (
    canonical_relationship,
    normalize_reference,
    resolve_recipient_reference,
)
from backend.recommendation.constraint_compiler import (
    build_recipient_context,
    compile_spec,
)
from backend.recommendation.eligibility import evaluate_all
from backend.recommendation.models import (
    AttributeConstraint,
    BudgetConstraint,
    ProductFacts,
    RecipientContext,
    ShoppingSpec,
)
from backend.recommendation.product_normalizer import normalize_products
from backend.recommendation.ranker import rank_products
from backend.schemas.gift_composer import (
    GiftBundlePreview,
    GiftComposerRequest,
    GiftComposerResponse,
    GiftProductPreview,
)
from backend.services import delivery_service

logger = get_logger(__name__)

# This is an intentionally small bounded search: the chat recommendation path
# remains authoritative for normal chat results and is not changed by this page.
_SEARCH_LANES: tuple[tuple[str, str], ...] = (
    ("cake", "cake"),
    ("flowers", "flowers bouquet"),
    ("hamper", "gift hamper"),
    ("personalized", "personalized gift"),
)
# The Gift Composer has a dedicated, bounded carousel candidate pool. This does
# not change the normal chat recommendation path. Up to 12 candidates per product
# lane allows a bundle slot to offer as many as 12 eligible alternatives.
_SEARCH_LIMIT_PER_LANE = 12
_MAX_CANDIDATES_TO_ENRICH = 36
_ENRICHMENT_CONCURRENCY = 3

# Bundle selection must consider a broad enough pool to find different product
# types. The general recommendation ranker may place many products of one type
# (for example, sarees) before other valid types (cakes/flowers). We therefore
# build a category-balanced pool for combination generation rather than only
# examining the first 12 globally ranked products.
_MAX_COMBINATION_CANDIDATES = 36
_MAX_COMBINATION_PRODUCTS_PER_GROUP = 10
# Cap category-specific searches to keep the planner inside a safe MCP budget.
# For the normal four entered categories plus saved likes, all categories are searched.
_MAX_PREFERENCE_SEARCH_LANES = 10
_MAX_BUNDLE_SELECTION_STATES = 2_000


def _split_terms(value: str | None) -> list[str]:
    if not value:
        return []
    terms = re.split(r"[,;\n]+", value)
    return list(dict.fromkeys(
        term.strip().lower()
        for term in terms
        if term.strip()
    ))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _restore_search_ids_on_details(
    candidates: list[Any],
    details: list[Any],
    enrichment_failures: list[str],
) -> list[Any]:
    """Keep the catalog ID used for search when MCP detail IDs are missing or drift.

    The lane-to-product mapping is created from search result IDs. Some parsed
    Kapruka detail responses can omit their ID (the adapter may expose this as
    the literal string "None") or return a differently formatted ID. Since
    each successful detail is fetched for a known candidate ID and enrichment
    preserves input order, the original search ID is authoritative for this
    Gift Composer preview and for the existing cart action.

    Failed IDs are supplied by enrich_products_with_failures(), which keeps
    successful detail objects in the same relative order as candidates.
    """
    failed_ids = {str(value).strip() for value in (enrichment_failures or [])}
    detail_iter = iter(details)
    restored: list[Any] = []

    for candidate in candidates:
        requested_id = str(
            getattr(candidate, "product_id", "") or ""
        ).strip()
        if not requested_id or requested_id.lower() in {"none", "null", "unknown"}:
            continue
        if requested_id in failed_ids:
            continue

        detail = next(detail_iter, None)
        if detail is None:
            logger.error(
                "Gift Composer enrichment alignment incomplete: "
                "candidate_id=%s details_exhausted=true",
                requested_id,
            )
            break

        returned_id = str(
            getattr(detail, "product_id", "") or ""
        ).strip()
        if returned_id != requested_id:
            logger.warning(
                "Gift Composer restoring catalog ID after detail-ID mismatch: "
                "requested_id=%s returned_id=%r product=%r",
                requested_id,
                returned_id,
                getattr(detail, "name", ""),
            )
            try:
                detail = detail.model_copy(
                    update={"product_id": requested_id}
                )
            except AttributeError:
                # Compatibility for model-like test doubles or older models.
                setattr(detail, "product_id", requested_id)

        restored.append(detail)

    leftovers = list(detail_iter)
    if leftovers:
        logger.warning(
            "Gift Composer enrichment returned %d unmatched detail record(s); "
            "retaining them with their returned IDs",
            len(leftovers),
        )
        restored.extend(leftovers)

    return restored


def _preference_term_priority(term: str) -> tuple[int, int, int]:
    """Prefer explicit purchasable item types over broad descriptive phrases."""
    value = _norm(term)
    product_terms = (
        "saree", "sarees", "sari", "saris", "flower", "flowers",
        "bouquet", "cake", "cakes", "watch", "watches", "phone",
        "phones", "chocolate", "hamper", "perfume", "mug", "card",
        "jewellery", "jewelry", "necklace", "bracelet", "dress",
        "shirt", "wallet", "bag", "toy", "book", "plant",
    )
    explicit_type = int(any(re.search(rf"\b{re.escape(word)}\b", value) for word in product_terms))
    return (explicit_type, len(value.split()), len(value))


def _search_lanes(
    occasion: str,
    recipient: str,
    preferences: list[str],
    excluded_terms: list[str],
    priority_preferences: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Build one dedicated search lane per requested product category.

    When categories are supplied, do not let generic gift searches crowd them
    out. Each entered category and each enabled saved recipient like receives
    its own lane. Generic lanes are used only when there are no category terms.
    """
    del recipient  # Kept in the private function signature for call-site stability.
    del priority_preferences  # Preference priority is handled by ranking, not query truncation.

    ordered_preferences: list[str] = []
    seen_preferences: set[str] = set()
    for term in preferences:
        normalized = _norm(term)
        if normalized and normalized not in seen_preferences:
            ordered_preferences.append(term.strip())
            seen_preferences.add(normalized)

    if ordered_preferences:
        bounded = ordered_preferences[:_MAX_PREFERENCE_SEARCH_LANES]
        lanes: list[tuple[str, str]] = []
        for index, term in enumerate(bounded, start=1):
            query = term.strip()
            # Keep a cake category tied to the occasion. A bare `cake` query
            # can return broad/noisy results that later fail the exact category
            # check. For birthdays this produces `Birthday cake`; the search
            # lane below also has bounded fallback queries if needed.
            occasion_text = occasion.strip()
            if _preference_carousel_key(term) == "cake" and occasion_text:
                if _norm(occasion_text) not in _norm(query):
                    query = f"{occasion_text} {query}".strip()
            lanes.append((f"preference_{index}", query))
        return lanes

    occasion_text = occasion.strip()
    exclusions = " ".join(excluded_terms)
    chocolate_excluded = bool(
        re.search(r"\b(?:chocolate|cocoa|cacao)\b", exclusions)
    )
    lanes = [
        (key, f"{occasion_text} {query}".strip())
        for key, query in _SEARCH_LANES
    ]
    if not chocolate_excluded:
        lanes.append(("sweets", f"{occasion_text} chocolates gift".strip()))

    deduped: list[tuple[str, str]] = []
    seen_queries: set[str] = set()
    for key, query in lanes:
        normalized_query = _norm(query)
        if not normalized_query or normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        deduped.append((key, query))
        if len(deduped) >= 6:
            break
    return deduped

def _singularize_last_word(value: str) -> str:
    words = value.split()
    if not words:
        return value
    last = words[-1]
    if last.endswith("ies") and len(last) > 3:
        words[-1] = last[:-3] + "y"
    elif last.endswith("s") and not last.endswith("ss") and len(last) > 2:
        words[-1] = last[:-1]
    return " ".join(words)


def _term_matches_text(term: str, text: str) -> bool:
    """Match a preference phrase conservatively, allowing a simple plural form."""
    normalized_term = _norm(term)
    normalized_text = _norm(text)
    if not normalized_term or not normalized_text:
        return False
    variants = {normalized_term, _singularize_last_word(normalized_term)}
    for variant in variants:
        if variant and re.search(
            rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])",
            normalized_text,
        ):
            return True
    return False


def _matched_preferences(
    fact: ProductFacts,
    preferences: list[str],
) -> list[str]:
    searchable = fact.searchable_text()
    return [
        term for term in preferences
        if _term_matches_text(term, searchable)
    ]


def _preference_score(
    fact: ProductFacts,
    preferences: list[str],
    priority_preferences: list[str],
) -> int:
    matched = _matched_preferences(fact, preferences)
    priority = {_norm(term) for term in priority_preferences}
    # A saved recipient preference is a strong soft signal. It never overrides
    # hard eligibility and does not make unmatched products ineligible.
    score = sum(12 if _norm(term) in priority else 4 for term in matched)
    return score


def _classify_group(fact: ProductFacts, source_lane: str) -> str:
    text = _norm(" ".join([
        fact.name,
        fact.category or "",
        fact.product_family or "",
        fact.canonical_product_type or "",
        fact.description[:500],
        fact.safety_text[:1500],
    ]))

    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("cake", ("cake", "cupcake", "gateau", "gateaux")),
        ("flowers", ("flower", "bouquet", "roses", "rose", "orchid")),
        ("hamper", ("hamper", "gift basket", "combo gift pack", "gift set")),
        ("greeting_card", ("greeting card", "birthday card", "message card")),
        ("snacks", ("snack", "chips", "crackers", "biscuits", "cookies")),
        ("chocolates", ("chocolate", "cocoa", "cacao", "candy", "confectionery")),
        ("fragrance", ("perfume", "fragrance", "eau de", "cologne")),
        ("fashion", ("saree", "sari", "jewellery", "jewelry", "necklace", "bracelet", "dress")),
        ("personalized", ("personalized", "personalised", "custom", "photo mug", "engraved")),
    )

    for group, words in patterns:
        if any(word in text for word in words):
            return group

    return source_lane if source_lane in {
        "cake", "flowers", "hamper", "greeting_card", "snacks", "chocolates", "fragrance", "fashion", "personalized"
    } else "other_gift"


def _carousel_group_key(fact: ProductFacts, classified_group: str) -> str:
    """Return a tighter product-type key for swappable carousel options.

    Bundle diversity still uses the existing broad groups (e.g. fashion), but
    a saree slot must show sarees rather than unrelated fashion accessories.
    The key is based on product identity/category, not arbitrary description
    mentions, to reduce cross-type alternatives.
    """
    identity_text = _norm(" ".join([
        fact.name,
        fact.category or "",
        fact.product_family or "",
        fact.canonical_product_type or "",
    ]))

    specific_types: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("saree", ("saree", "sarees", "sari", "saris")),
        ("watch", ("watch", "watches")),
        ("mobile_phone", ("mobile phone", "smartphone", "mobile phones", "smart phones")),
        ("mug", ("mug", "mugs")),
        ("necklace", ("necklace", "necklaces")),
        ("bracelet", ("bracelet", "bracelets")),
        ("handbag", ("handbag", "handbags", "hand bag", "hand bags")),
        ("dress", ("dress", "dresses")),
        ("shirt", ("shirt", "shirts")),
        ("wallet", ("wallet", "wallets")),
        ("plant", ("plant", "plants")),
        ("book", ("book", "books")),
        ("toy", ("toy", "toys")),
        ("perfume", ("perfume", "perfumes", "fragrance")),
        ("greeting_card", ("greeting card", "birthday card", "message card")),
        ("snacks", ("snack", "snacks", "chips", "crackers", "biscuits", "cookies")),
        ("cake", ("cake", "cakes", "gateau", "gateaux")),
        ("flowers", ("flower", "flowers", "bouquet", "roses", "orchid")),
        ("hamper", ("hamper", "gift basket", "combo gift pack")),
        ("chocolates", ("chocolate", "chocolates", "cocoa", "cacao", "candy")),
    )

    # Longer phrases are checked naturally through token boundaries.
    for key, words in specific_types:
        for word in words:
            if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", identity_text):
                return key

    return classified_group


def _preference_carousel_key(term: str) -> str | None:
    """Map common user-entered category terms to a precise product slot type."""
    value = _norm(term)
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("saree", ("saree", "sarees", "sari", "saris")),
        ("flowers", ("flower", "flowers", "bouquet", "roses", "orchid")),
        ("cake", ("cake", "cakes", "cupcake", "gateau", "gateaux")),
        ("snacks", ("snack", "snacks", "chips", "crackers", "biscuits", "cookies")),
        ("chocolates", ("chocolate", "chocolates", "cocoa", "cacao", "candy")),
        ("greeting_card", ("greeting card", "birthday card", "message card")),
        ("hamper", ("hamper", "gift basket", "combo gift pack", "gift set")),
        ("perfume", ("perfume", "fragrance", "eau de", "cologne")),
        ("watch", ("watch", "watches")),
        ("mobile_phone", ("mobile phone", "smartphone", "mobile phones", "smart phones", "phones")),
        ("mug", ("mug", "mugs")),
        ("necklace", ("necklace", "necklaces")),
        ("bracelet", ("bracelet", "bracelets")),
        ("handbag", ("handbag", "handbags", "hand bag", "hand bags")),
        ("dress", ("dress", "dresses")),
        ("shirt", ("shirt", "shirts")),
        ("wallet", ("wallet", "wallets")),
        ("plant", ("plant", "plants")),
        ("book", ("book", "books")),
        ("toy", ("toy", "toys")),
        ("personalized", ("personalized", "personalised", "custom", "engraved")),
    )
    for key, words in patterns:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", value) for word in words):
            return key
    return None


def _occasion_default_preferences(
    occasion: str,
    excluded_terms: list[str],
) -> list[str]:
    """Return sensible product categories when the user leaves preferences blank.

    These are fallback category slots, not hard requirements. The normal catalog
    search, eligibility, ranking, budget, and delivery logic still decides which
    products can actually be shown. Saved profile likes remain soft ranking signals;
    only saved likes that identify a product category become additional slots.
    """
    text = _norm(occasion)

    def has_any(*needles: str) -> bool:
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(_norm(needle))}(?![a-z0-9])", text)
            for needle in needles
        )

    if has_any("anniversary", "anniversaries"):
        defaults = ["flowers", "cake", "personalized gift", "chocolates", "perfume"]
    elif has_any("valentine", "valentines", "valentine's day"):
        defaults = ["flowers", "chocolates", "perfume", "personalized gift", "cake"]
    elif has_any("wedding", "engagement"):
        defaults = ["flowers", "cake", "gift hamper", "personalized gift", "chocolates"]
    elif has_any("mother's day", "mothers day", "mother day", "mum's day", "mums day"):
        defaults = ["flowers", "cotton sarees", "cake", "personalized gift", "gift hamper"]
    elif has_any("father's day", "fathers day", "father day", "dad's day", "dads day"):
        defaults = ["watch", "wallet", "perfume", "gift hamper", "personalized gift"]
    elif has_any("graduation", "convocation"):
        defaults = ["flowers", "watch", "personalized gift", "gift hamper", "chocolates"]
    elif has_any("thank you", "thank-you", "appreciation"):
        defaults = ["flowers", "gift hamper", "chocolates", "personalized gift"]
    elif has_any("get well", "get-well", "recovery"):
        defaults = ["flowers", "gift hamper", "personalized gift", "snacks"]
    elif has_any("christmas", "diwali", "deepavali", "sinhala new year", "avurudu"):
        defaults = ["gift hamper", "cake", "chocolates", "flowers", "personalized gift"]
    elif has_any("new baby", "baby shower", "newborn"):
        defaults = ["gift hamper", "toy", "flowers", "personalized gift"]
    elif has_any("housewarming", "house warming", "new home"):
        defaults = ["plant", "gift hamper", "personalized gift", "flowers"]
    elif has_any("birthday", "birth day", "bday"):
        defaults = ["cake", "flowers", "personalized gift", "gift hamper", "chocolates"]
    else:
        # Sensible broad fallback for occasions with no dedicated template.
        defaults = ["flowers", "gift hamper", "personalized gift", "cake"]

    excluded_keys = {
        key
        for term in excluded_terms
        if (key := _preference_carousel_key(term)) is not None
    }
    if any(re.search(r"\b(?:chocolate|cocoa|cacao)\b", _norm(term)) for term in excluded_terms):
        excluded_keys.add("chocolates")

    result: list[str] = []
    seen: set[str] = set()
    for term in defaults:
        key = _preference_carousel_key(term)
        dedupe_key = key or _norm(term)
        if dedupe_key in seen:
            continue
        if key and key in excluded_keys:
            continue
        seen.add(dedupe_key)
        result.append(term)
    return result


def _cake_search_queries(term: str, occasion: str) -> list[str]:
    """Build a small, deduplicated query set for cake-category retrieval.

    The first query is contextual (e.g. ``Birthday cake``). Only if that query
    doesn't find enough clearly identifiable cakes do we try the generic and
    plural forms. The fallback is limited to three queries to control MCP load.
    """
    normalized_term = _norm(term)
    if not normalized_term:
        normalized_term = "cake"

    normalized_occasion = _norm(occasion)
    contextual = (
        normalized_term
        if normalized_occasion and normalized_occasion in normalized_term
        else f"{occasion.strip()} {term.strip()}".strip()
    )

    if any(word in normalized_term for word in ("cupcake", "cupcakes")):
        variants = ["cupcake", "cupcakes"]
    elif any(word in normalized_term for word in ("gateau", "gateaux")):
        variants = ["gateau", "gateaux"]
    else:
        variants = ["cake", "cakes"]

    queries: list[str] = []
    seen: set[str] = set()
    for candidate in (contextual, term.strip(), *variants):
        candidate = candidate.strip()
        key = _norm(candidate)
        if candidate and key not in seen:
            seen.add(key)
            queries.append(candidate)
        if len(queries) >= 3:
            break
    return queries


def _summary_matches_category(product: Any, target_key: str) -> bool:
    """Check whether a search summary explicitly identifies a requested type.

    Search summaries from the live MCP server are often much thinner than the
    eventual product-detail response. Read all known identity fields when they
    exist, but do not use a failed summary check to discard a candidate from a
    category-specific query: detail enrichment is the authoritative validation
    stage for cards such as cakes whose title/category metadata is incomplete.
    """
    if target_key != "cake":
        return True

    parts: list[str] = []
    for key in (
        "name", "title", "category", "product_family", "product_type",
        "canonical_product_type", "safety_text", "description",
    ):
        value = getattr(product, key, None)
        if value:
            parts.append(str(value))

    if isinstance(product, dict):
        for key in (
            "name", "title", "category", "product_family", "product_type",
            "canonical_product_type", "safety_text", "description",
        ):
            value = product.get(key)
            if value:
                parts.append(str(value))

    text = _norm(" ".join(parts))
    # Cake accessories often contain the word "cake" but must not satisfy the
    # cake slot or stop fallback queries before actual cakes are found.
    if re.search(
        r"\b(?:cake\s+(?:topper|candle|candles|knife|stand|decoration|decorations|box|holder|mould|mold|pan|board|server|accessor(?:y|ies)))\b",
        text,
    ):
        return False
    cake_terms = ("cake", "cakes", "cupcake", "cupcakes", "gateau", "gateaux", "cheesecake", "cheesecakes")
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
        for term in cake_terms
    )


def _is_cake_fact(fact: ProductFacts) -> bool:
    """Verify cake identity using enriched details, including normalized type.

    `canonical_product_type` matters because some Kapruka product names omit a
    generic noun even though the detailed listing/category or normalizer identifies
    the item as a cake. Accessories such as toppers/candles must not fill the slot.
    """
    if fact.item_role in {"accessory", "component", "replacement", "service"}:
        return False

    name_identity = _norm(fact.name)
    if re.search(
        r"\b(?:cake\s+(?:topper|candle|candles|knife|stand|decoration|decorations|box|holder|mould|mold|pan|board|server|accessor(?:y|ies)))\b",
        name_identity,
    ):
        return False

    # The current shared catalog adapter puts the raw product-detail Markdown
    # into safety_text, while its normalized ProductSummary can omit `category`.
    # Recover only the explicit Category metadata from that Markdown; do not
    # infer a cake from an incidental mention in descriptive marketing copy.
    raw_detail_category = re.search(
        r"(?im)^\*\*Category\*\*:\s*([^\r\n]+)",
        fact.safety_text or "",
    )
    if raw_detail_category:
        detail_category = _norm(raw_detail_category.group(1))
        if re.search(
            r"(?<![a-z0-9])(?:cakes?|cupcakes?|gateaux?)(?![a-z0-9])|\bcheesecakes?\b",
            detail_category,
        ):
            return True

    canonical = _norm(fact.canonical_product_type or "")
    if canonical:
        # Product type must describe a cake itself, not an accessory whose parent
        # product is a cake (e.g. a topper or cake knife).
        if any(word in canonical for word in (
            "topper", "candle", "knife", "stand", "decoration", "decoration set",
        )):
            return False
        if canonical in {
            "cake", "cakes", "cupcake", "cupcakes", "gateau", "gateaux",
            "cheesecake", "cheesecakes", "birthday cake", "sponge cake",
            "chocolate cake", "fruit cake",
        } or re.search(r"\b(?:cup)?cakes?\b|\bgateaux?\b|\bcheesecakes?\b", canonical):
            return True

    identity_text = _norm(" ".join((
        fact.name,
        fact.category or "",
        fact.product_family or "",
    )))
    if any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", identity_text)
        for term in ("cake", "cakes", "cupcake", "cupcakes", "gateau", "gateaux", "cheesecake", "cheesecakes")
    ):
        # Avoid letting cake accessories through when normalization could not
        # confidently assign item_role.
        if re.search(r"\b(?:cake topper|cake candle|cake knife|cake stand)\b", identity_text):
            return False
        return True

    return False

def _price_cents(value: Any) -> int:
    """Convert a product price to non-negative integer cents for safe budget math."""
    try:
        return max(0, int(round(float(value or 0.0) * 100)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _budget_safe_carousel_candidates(
    slot_specs: list[dict[str, Any]],
    selected_slots: list[tuple[str, str, ProductFacts]],
    budget_lkr: float | None,
) -> tuple[dict[str, list[ProductFacts]], dict[str, int]]:
    """Limit alternatives so every combination of carousel choices fits the budget.

    The selected product in each slot defines a guaranteed-valid baseline. Any
    remaining budget is divided across the selected slots in cents. Each slot's
    alternative prices are capped at baseline price plus its share of the
    headroom. Thus the sum of the maximum price available in every displayed
    slot cannot exceed the entered budget, regardless of which arrows are clicked.
    """
    candidates_by_lane: dict[str, list[ProductFacts]] = {}
    caps_by_lane: dict[str, int] = {}

    if not selected_slots:
        return candidates_by_lane, caps_by_lane

    if budget_lkr is None:
        for slot in slot_specs:
            candidates_by_lane[slot["lane"]] = list(slot["candidates"])
        return candidates_by_lane, caps_by_lane

    budget_cents = _price_cents(budget_lkr)
    selected_prices = [
        _price_cents(fact.price)
        for _, _, fact in selected_slots
    ]
    selected_total_cents = sum(selected_prices)

    # _select_category_products already enforces this invariant. Retain a hard
    # guard here so future selection changes cannot accidentally produce a bundle
    # over budget or an invalid carousel allowance.
    if selected_total_cents > budget_cents:
        logger.error(
            "Gift Composer selected baseline exceeds budget; carousel alternatives disabled",
            extra={
                "selected_total_lkr": round(selected_total_cents / 100, 2),
                "budget_lkr": round(budget_cents / 100, 2),
                "selected_slots": len(selected_slots),
            },
        )
        # Return only the selected facts for each slot. The caller also validates
        # this invariant before returning a public preview.
        for lane, _, fact in selected_slots:
            candidates_by_lane[lane] = [fact]
            caps_by_lane[lane] = _price_cents(fact.price)
        return candidates_by_lane, caps_by_lane

    headroom_cents = budget_cents - selected_total_cents
    slot_count = len(selected_slots)
    base_share, remainder = divmod(headroom_cents, slot_count)

    slot_map = {slot["lane"]: slot for slot in slot_specs}
    caps_for_log: dict[str, float] = {}

    for index, (lane, _label, selected_fact) in enumerate(selected_slots):
        # Distribute remainder cents deterministically so total caps add to no
        # more than the exact budget, avoiding floating-point rounding drift.
        allowance = base_share + (1 if index < remainder else 0)
        cap_cents = _price_cents(selected_fact.price) + allowance
        caps_by_lane[lane] = cap_cents
        slot = slot_map.get(lane, {})

        # Deduplicate by ID and keep the selected baseline fact as authoritative.
        by_id: dict[str, ProductFacts] = {}
        for candidate in slot.get("candidates", []):
            candidate_id = str(candidate.product_id or "").strip()
            if not candidate_id or _price_cents(candidate.price) > cap_cents:
                continue
            by_id.setdefault(candidate_id, candidate)

        selected_id = str(selected_fact.product_id or "").strip()
        by_id[selected_id] = selected_fact
        candidates_by_lane[lane] = list(by_id.values())
        caps_for_log[lane] = round(cap_cents / 100, 2)

    logger.info(
        "Gift Composer budget-safe carousel caps",
        extra={
            "budget_lkr": round(budget_cents / 100, 2),
            "selected_total_lkr": round(selected_total_cents / 100, 2),
            "worst_case_cap_total_lkr": round(sum(caps_by_lane.values()) / 100, 2),
            "category_caps_lkr": caps_for_log,
        },
    )
    return candidates_by_lane, caps_by_lane


def _select_category_products(
    slot_specs: list[dict[str, Any]],
    *,
    budget_lkr: float | None,
    ranked: list[ProductFacts],
    preferences: list[str],
    saved_preferences: list[str],
) -> list[tuple[str, str, ProductFacts]]:
    """Choose one distinct item per available category, maximizing slot count under budget.

    A bounded beam search prefers including more requested categories first,
    then uses preference/ranking scores to choose among affordable combinations.
    """
    if not slot_specs:
        return []

    rank_index = {str(f.product_id): i for i, f in enumerate(ranked)}
    # state: total, utility score, selected [(lane, label, fact), ...]
    states: list[tuple[float, float, list[tuple[str, str, ProductFacts]]]] = [(0.0, 0.0, [])]

    for slot in slot_specs:
        lane = slot["lane"]
        label = slot["label"]
        candidates: list[ProductFacts] = slot["candidates"][:12]
        expanded: list[tuple[float, float, list[tuple[str, str, ProductFacts]]]] = list(states)  # allow category skip if budget requires it

        for total, score, chosen in states:
            chosen_ids = {str(f.product_id) for _, _, f in chosen}
            for fact in candidates:
                fact_id = str(fact.product_id)
                if fact_id in chosen_ids:
                    continue
                price = max(0.0, float(fact.price or 0.0))
                new_total = round(total + price, 2)
                if budget_lkr is not None and new_total > budget_lkr:
                    continue
                preference_score = _preference_score(fact, preferences, saved_preferences)
                rank_bonus = max(0, len(ranked) - rank_index.get(fact_id, len(ranked)))
                # The 10,000 point slot bonus means number of represented
                # categories outranks product-level scores; scores rank ties.
                item_utility = 10_000 + preference_score * 10 + rank_bonus
                expanded.append((
                    new_total,
                    score + item_utility,
                    [*chosen, (lane, label, fact)],
                ))

        # Preserve distinct states. The beam prevents combinatorial growth for
        # requests containing many categories while retaining high-quality fits.
        expanded.sort(
            key=lambda state: (len(state[2]), state[1], -state[0]),
            reverse=True,
        )
        deduped: list[tuple[float, float, list[tuple[str, str, ProductFacts]]]] = []
        seen: set[tuple[int, tuple[str, ...]]] = set()
        for state in expanded:
            key = (int(round(state[0] * 100)), tuple(str(f.product_id) for _, _, f in state[2]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(state)
            if len(deduped) >= _MAX_BUNDLE_SELECTION_STATES:
                break
        states = deduped

    if not states:
        return []
    best = max(states, key=lambda state: (len(state[2]), state[1], -state[0]))
    return best[2]


def _group_label(group: str) -> str:
    return {
        "cake": "Cake",
        "flowers": "Flowers",
        "hamper": "Gift hamper",
        "greeting_card": "Greeting card",
        "chocolates": "Chocolates",
        "snacks": "Snacks",
        "fragrance": "Fragrance",
        "fashion": "Fashion/accessory",
        "personalized": "Personalized gift",
        "other_gift": "Gift",
    }.get(group, group.replace("_", " ").title())


def _make_product_preview(
    fact: ProductFacts,
    group: str,
    preferences: list[str],
    occasion: str,
) -> dict[str, Any]:
    text = _norm(fact.searchable_text())
    reasons: list[str] = []

    matched_preferences = _matched_preferences(
        fact,
        preferences,
    )
    if matched_preferences:
        reasons.append("Matches preference: " + ", ".join(matched_preferences[:3]))
    if occasion and _norm(occasion) in text:
        reasons.append(f"Relevant to {occasion.lower()}")
    if fact.in_stock is True:
        reasons.append("Kapruka lists this item as in stock")
    elif fact.in_stock is None:
        reasons.append("Stock status was not confirmed")

    raw = fact.raw if isinstance(fact.raw, dict) else {}
    raw_images = raw.get("images") or []
    images = [str(url) for url in raw_images if url] if isinstance(raw_images, list) else []
    if fact.image_url and not images:
        images = [fact.image_url]

    return {
        "product_id": str(fact.product_id),
        "name": fact.name,
        "price": float(fact.price),
        "currency": fact.currency or "LKR",
        "category": fact.category,
        "group": _group_label(group),
        "image_url": fact.image_url or (images[0] if images else None),
        "images": images,
        "url": fact.url,
        "description": fact.description or "",
        "in_stock": fact.in_stock,
        "reasons": reasons,
        "delivery_status": "not_checked",
        "delivery_message": None,
    }


def _combo_candidates(
    ranked: list[ProductFacts],
    group_by_id: dict[str, str],
    budget_lkr: float | None,
    preferences: list[str],
    priority_preferences: list[str],
) -> list[dict[str, Any]]:
    """
    Build 2- or 3-item combinations across distinct product groups.

    Important: do not generate combinations from only ``ranked[:12]``. The
    first dozen globally ranked products can be dominated by a single product
    type, even when the retrieval pool contains eligible cakes, flowers, and
    other gift types. Build a bounded category-balanced pool first, preserving
    ranking order within each group, then enumerate combinations from it.
    """
    if len(ranked) < 2:
        return []

    candidates: list[ProductFacts] = []
    count_by_group: dict[str, int] = {}

    # `ranked` is already ordered by the ranking model and the composer’s
    # preference layer. Keep the best products within each group, but prevent
    # a single group from consuming the whole combination pool.
    for fact in ranked:
        group = group_by_id.get(
            str(fact.product_id),
            "other_gift",
        )
        group_count = count_by_group.get(group, 0)

        if group_count >= _MAX_COMBINATION_PRODUCTS_PER_GROUP:
            continue

        candidates.append(fact)
        count_by_group[group] = group_count + 1

        if len(candidates) >= _MAX_COMBINATION_CANDIDATES:
            break

    if len(candidates) < 2:
        return []

    logger.info(
        "Gift Composer combination pool prepared",
        extra={
            "ranked_products": len(ranked),
            "combination_pool_size": len(candidates),
            "product_groups": dict(count_by_group),
        },
    )

    rank_index = {
        str(item.product_id): index
        for index, item in enumerate(candidates)
    }
    rank_strength = {
        str(item.product_id): len(candidates) - index
        for index, item in enumerate(candidates)
    }

    combinations: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()

    # Try three items first, then retain the existing two-item fallback.
    for size in (3, 2):
        for combo in itertools.combinations(candidates, size):
            groups = [
                group_by_id.get(
                    str(item.product_id),
                    "other_gift",
                )
                for item in combo
            ]

            # A gift basket should contain distinct broad product types.
            if len(set(groups)) != len(groups):
                continue

            total = round(
                sum(float(item.price) for item in combo),
                2,
            )

            if budget_lkr is not None and total > budget_lkr:
                continue

            ids = tuple(
                sorted(str(item.product_id) for item in combo)
            )

            if ids in seen_keys:
                continue

            seen_keys.add(ids)

            matched_preference_score = sum(
                _preference_score(
                    item,
                    preferences,
                    priority_preferences,
                )
                for item in combo
            )

            score = sum(
                rank_strength.get(str(item.product_id), 0)
                for item in combo
            )
            score += (len(set(groups)) - 1) * 2.0
            score += matched_preference_score * 4.0

            combinations.append({
                "ids": ids,
                "facts": list(combo),
                "groups": groups,
                "total": total,
                "score": float(score),
                "preference_score": matched_preference_score,
                "size": len(combo),
                "rank_sum": sum(
                    rank_index.get(str(item.product_id), 99)
                    for item in combo
                ),
            })

    logger.info(
        "Gift Composer combinations generated",
        extra={
            "eligible_pool_size": len(candidates),
            "three_item_combinations": sum(
                1 for combo in combinations if combo["size"] == 3
            ),
            "two_item_combinations": sum(
                1 for combo in combinations if combo["size"] == 2
            ),
            "budget_lkr": budget_lkr,
        },
    )

    return combinations


def _choose_bundle_options(combinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not combinations:
        return []

    selected: list[tuple[str, str, dict[str, Any]]] = []
    used: set[tuple[str, ...]] = set()

    def add(title: str, description: str, ordered: list[dict[str, Any]]) -> None:
        for combo in ordered:
            if combo["ids"] in used:
                continue
            used.add(combo["ids"])
            selected.append((title, description, combo))
            return

    add(
        "Best match",
        "The strongest overall combination based on recipient fit, stated preferences and product diversity.",
        # Prefer a complete three-product basket whenever a valid combination
        # of that size exists within the budget and distinct-group constraints.
        # Recipient-fit and ranking scores remain tie-breakers among bundles
        # of the same size, so this only changes the Best match selection order.
        sorted(
            combinations,
            key=lambda c: (
                c["size"],
                c["preference_score"],
                c["score"],
                -c["rank_sum"],
            ),
            reverse=True,
        ),
    )
    add(
        "More complete gift",
        "A fuller combination with more distinct product types, within the total budget when one was supplied.",
        sorted(
            combinations,
            key=lambda c: (
                c["size"],
                c["preference_score"],
                c["score"],
                -c["total"],
            ),
            reverse=True,
        ),
    )
    add(
        "Budget-friendly",
        "A lower-cost combination that still includes different product types.",
        sorted(combinations, key=lambda c: (-c["preference_score"], c["total"], -c["score"], c["size"])),
    )

    # If two labels would otherwise select the same basket, offer the next
    # strongest distinct combination with a neutral label.
    if len(selected) < min(3, len(combinations)):
        for combo in sorted(combinations, key=lambda c: (c["score"], c["size"]), reverse=True):
            if combo["ids"] in used:
                continue
            selected.append((
                "Alternative combination",
                "Another eligible combination of distinct product types.",
                combo,
            ))
            used.add(combo["ids"])
            if len(selected) >= 3:
                break

    return [
        {
            "id": "gift-bundle-" + str(index + 1),
            "title": title,
            "description": description,
            "total": combo["total"],
            "currency": combo["facts"][0].currency or "LKR",
            "facts": combo["facts"],
        }
        for index, (title, description, combo) in enumerate(selected[:3])
    ]


async def _check_delivery_for_options(
    *,
    bundles: list[dict[str, Any]],
    standalone: list[dict[str, Any]],
    city: str | None,
    requested_date: Any,
) -> tuple[bool, str | None, str | None]:
    """Validate chosen preview items once each; this function never creates an order."""
    if not bundles and not standalone:
        return False, None, None

    if not city or not requested_date:
        message = "Delivery has not been checked. Enter both a delivery city and date to verify these preview options."
        for bundle in bundles:
            bundle["delivery_status"] = "not_checked"
            bundle["delivery_message"] = message
        for product in standalone:
            product["delivery_status"] = "not_checked"
            product["delivery_message"] = message
        return False, None, message

    try:
        canonical_city = await delivery_service.resolve_canonical_city(city)
        general = await delivery_service._check_single_delivery(
            city=canonical_city,
            delivery_date=requested_date,
            product_id=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gift Composer delivery precheck could not be completed: %s", exc)
        message = "Kapruka delivery availability could not be confirmed right now. These bundles remain previews only."
        for bundle in bundles:
            bundle["delivery_status"] = "unknown"
            bundle["delivery_message"] = message
        for product in standalone:
            product["delivery_status"] = "unknown"
            product["delivery_message"] = message
        return False, None, message

    if general.get("deliverable") is not True:
        available = general.get("deliverable") is False
        status = "unavailable" if available else "unknown"
        message = (
            general.get("notes")
            or general.get("message")
            or f"Kapruka did not confirm general delivery to {canonical_city} on {requested_date.isoformat()}."
        )
        for bundle in bundles:
            bundle["delivery_status"] = status
            bundle["delivery_message"] = message
        for product in standalone:
            product["delivery_status"] = status
            product["delivery_message"] = message
        return True, canonical_city, message

    product_ids: set[str] = set()
    for bundle in bundles:
        product_ids.update(str(fact.product_id) for fact in bundle["facts"])
    product_ids.update(str(product["product_id"]) for product in standalone)

    product_checks: dict[str, bool | None] = {}
    # Use bounded sequential checks. The same product can appear in multiple
    # options, so it is queried only once per composer preview.
    for product_id in sorted(product_ids):
        try:
            check = await delivery_service._check_single_delivery(
                city=canonical_city,
                delivery_date=requested_date,
                product_id=product_id,
            )
            raw_status = check.get("deliverable")
            product_checks[product_id] = raw_status if isinstance(raw_status, bool) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gift Composer product delivery check failed for %s: %s", product_id, exc)
            product_checks[product_id] = None

    def apply(items: list[dict[str, Any]], facts: list[ProductFacts]) -> tuple[str, str | None]:
        statuses = [product_checks.get(str(fact.product_id)) for fact in facts]
        if any(value is False for value in statuses):
            return "unavailable", f"At least one item was not confirmed deliverable to {canonical_city} on {requested_date.isoformat()}."
        if any(value is None for value in statuses):
            return "unknown", "Kapruka could not confirm delivery for every item in this preview."
        return "confirmed", f"Kapruka confirmed delivery checks for {canonical_city} on {requested_date.isoformat()}."

    for bundle in bundles:
        status, message = apply(bundle.get("products", []), bundle["facts"])
        bundle["delivery_status"] = status
        bundle["delivery_message"] = message
        for product in bundle.get("products", []):
            product_status = product_checks.get(str(product["product_id"]))
            product["delivery_status"] = (
                "confirmed" if product_status is True
                else "unavailable" if product_status is False
                else "unknown"
            )
            product["delivery_message"] = message

    for product in standalone:
        product_status = product_checks.get(str(product["product_id"]))
        product["delivery_status"] = (
            "confirmed" if product_status is True
            else "unavailable" if product_status is False
            else "unknown"
        )
        product["delivery_message"] = (
            f"Delivery to {canonical_city} on {requested_date.isoformat()} was confirmed."
            if product_status is True
            else "Delivery was not confirmed for this item."
        )

    return True, canonical_city, None


async def compose_gift_preview(
    *,
    user_id: str,
    session_id: str,
    request: GiftComposerRequest,
) -> GiftComposerResponse:
    recipient_query = request.recipient.strip()
    occasion = request.occasion.strip()
    preferences = _split_terms(request.preferences)
    avoid_terms = _split_terms(request.avoid)
    allergy_terms = _split_terms(request.allergies)

    resolved_recipient = await resolve_recipient_reference(
        user_id=user_id,
        session_id=session_id,
        raw_name=recipient_query,
    )
    profile = None
    if resolved_recipient:
        profile = await find_profile_by_name(
            user_id=user_id,
            name=resolved_recipient,
        )

    # Recipient lookup also supports profiles saved under a person's name
    # while their relationship is stored separately (e.g. name="Amila",
    # relationship="mother"). The standard name resolver alone may return
    # the canonical relationship token rather than that profile's name.
    if profile is None:
        try:
            profiles = await list_profiles(user_id)
            query_norm = normalize_reference(recipient_query)
            query_canonical = canonical_relationship(recipient_query)
            for candidate in profiles:
                name_norm = normalize_reference(candidate.name)
                relationship_norm = normalize_reference(candidate.relationship or "")
                relationship_canonical = canonical_relationship(candidate.relationship or "")
                if (
                    query_norm == name_norm
                    or (relationship_norm and query_norm == relationship_norm)
                    or (query_canonical and relationship_canonical == query_canonical)
                ):
                    profile = candidate
                    resolved_recipient = candidate.name
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gift Composer recipient-profile fallback lookup failed: %s", exc)

    # Saved likes influence the preview by default, but users can disable that
    # personalization for this request. Profile dislikes and allergies remain
    # active hard exclusions regardless of this preference toggle.
    saved_profile_preferences = _split_terms(
        ",".join(profile.likes or [])
        if profile is not None and not request.ignore_saved_preferences
        else ""
    )
    spec = ShoppingSpec(
        product_family=None,
        canonical_product_type=None,
        item_role="unknown",
        recipient_name=resolved_recipient or recipient_query,
        occasion=occasion,
        budget=(
            BudgetConstraint(max_price=request.budget_lkr, currency="LKR")
            if request.budget_lkr is not None
            else None
        ),
        # The planner's user-entered likes/preferences remain soft ranking signals.
        preferred_attributes=list(preferences),
        in_stock_only=True,
        search_queries=[],
    )

    for term in avoid_terms:
        spec.excluded_attributes.append(
            AttributeConstraint(
                term=term,
                polarity="exclude",
                source="current_message",
                safety_critical=False,
            )
        )

    for term in allergy_terms:
        spec.excluded_attributes.append(
            AttributeConstraint(
                term=term,
                polarity="exclude",
                source="current_message",
                safety_critical=True,
            )
        )

    recipient_context: RecipientContext | None = None
    if profile is not None:
        recipient_context = build_recipient_context(
            name=profile.name,
            relationship=profile.relationship or recipient_query,
            likes=(profile.likes if not request.ignore_saved_preferences else []),
            dislikes=profile.dislikes,
            allergies=profile.allergies,
        )
        spec = compile_spec(spec, recipient_context)
    else:
        recipient_context = build_recipient_context(
            name=resolved_recipient or recipient_query,
            relationship=recipient_query,
            likes=[],
            dislikes=[],
            allergies=[],
        )

    # Reassert request-specific budget after merging the profile; recipient memory
    # must never supply or modify a financial limit.
    spec.budget = (
        BudgetConstraint(max_price=request.budget_lkr, currency="LKR")
        if request.budget_lkr is not None
        else None
    )

    excluded_terms = [item.term for item in spec.excluded_attributes]
    effective_preferences = list(dict.fromkeys([*preferences, *saved_profile_preferences]))

    # Preserve the established behavior when the user enters categories: search
    # those categories plus saved recipient likes exactly as before. When the
    # category field is blank, do not let a non-category taste such as "vanilla"
    # become the only search lane. Use occasion-led category defaults instead,
    # augmented with saved likes that actually identify product categories.
    if preferences:
        category_source_terms = [*preferences, *saved_profile_preferences]
    else:
        saved_category_preferences = [
            term for term in saved_profile_preferences
            if _preference_carousel_key(term) is not None
        ]
        occasion_categories = _occasion_default_preferences(
            occasion=occasion,
            excluded_terms=excluded_terms,
        )
        category_source_terms = [*saved_category_preferences, *occasion_categories]

    category_preferences: list[str] = []
    seen_category_preferences: set[str] = set()
    for term in category_source_terms:
        normalized = _norm(term)
        category_type = _preference_carousel_key(term)
        # In the occasion-driven fallback, avoid creating duplicate slots for
        # aliases of the same item type (e.g. "cotton sarees" and "sarees").
        dedupe_key = (
            f"type:{category_type}"
            if not preferences and category_type
            else f"term:{normalized}"
        )
        if not normalized or dedupe_key in seen_category_preferences:
            continue
        category_preferences.append(term)
        seen_category_preferences.add(dedupe_key)

    omitted_categories = category_preferences[_MAX_PREFERENCE_SEARCH_LANES:]
    category_preferences = category_preferences[:_MAX_PREFERENCE_SEARCH_LANES]
    if not preferences:
        logger.info(
            "Gift Composer occasion defaults occasion=%r recipient=%r "
            "saved_category_likes=%s selected_categories=%s ignored_saved_likes=%s",
            occasion,
            resolved_recipient or recipient_query,
            saved_category_preferences,
            category_preferences,
            [term for term in saved_profile_preferences if _preference_carousel_key(term) is None],
        )

    lanes = _search_lanes(
        occasion=occasion,
        recipient=recipient_query,
        preferences=category_preferences,
        excluded_terms=excluded_terms,
    )
    preference_lane_labels = {
        f"preference_{index}": term
        for index, term in enumerate(category_preferences, start=1)
    }

    semaphore = asyncio.Semaphore(2)

    async def search_lane(lane_key: str, query: str):
        async with semaphore:
            label = preference_lane_labels.get(lane_key, query)
            target_key = _preference_carousel_key(label)

            # The cake lane uses the occasion-aware category queries first. Do
            # not discard every candidate simply because the brief search summary
            # omits the word "cake": Kapruka's detail response can contain a more
            # complete category/name and the normalizer can supply canonical type.
            # Keep obvious cake summaries first, then a bounded fallback pool from
            # those same cake-targeted queries; the enriched detail check below
            # prevents unrelated products from occupying the cake slot.
            if target_key == "cake":
                matched_products: list[Any] = []
                fallback_products: list[Any] = []
                seen_product_ids: set[str] = set()
                successful_queries = 0
                last_error: Exception | None = None

                for search_query in _cake_search_queries(label, occasion):
                    try:
                        products = await catalog_agent.search_products(
                            query=search_query,
                            max_price=request.budget_lkr,
                            in_stock_only=True,
                            limit=_SEARCH_LIMIT_PER_LANE,
                        )
                        successful_queries += 1
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        logger.warning(
                            "Gift Composer cake query failed lane=%s query=%r error=%s",
                            lane_key,
                            search_query,
                            exc,
                        )
                        continue

                    identity_matches = 0
                    valid_ids = 0
                    for product in products:
                        product_id = str(
                            getattr(product, "product_id", "") or ""
                        ).strip()
                        if not product_id or product_id.lower() in {"none", "null", "unknown"}:
                            continue
                        if product_id in seen_product_ids:
                            continue
                        seen_product_ids.add(product_id)
                        valid_ids += 1

                        if _summary_matches_category(product, "cake"):
                            matched_products.append(product)
                            identity_matches += 1
                        else:
                            fallback_products.append(product)

                    logger.info(
                        "Gift Composer cake search lane=%s query=%r results=%d valid_ids=%d identity_matches=%d accumulated_matches=%d fallback_candidates=%d",
                        lane_key,
                        search_query,
                        len(products),
                        valid_ids,
                        identity_matches,
                        len(matched_products),
                        len(fallback_products),
                    )

                    if len(matched_products) >= _SEARCH_LIMIT_PER_LANE:
                        break

                if successful_queries == 0 and last_error is not None:
                    return lane_key, [], str(last_error)

                # Use identity-matched summaries first. If their metadata is
                # incomplete, retain category-query fallback results so details
                # can confirm whether they are actually cakes. Hard cap remains
                # 12 candidate products per category lane.
                combined = matched_products + fallback_products
                selected = combined[:_SEARCH_LIMIT_PER_LANE]
                logger.info(
                    "Gift Composer cake lane candidates lane=%s matched=%d fallback=%d selected=%d queries_succeeded=%d",
                    lane_key,
                    len(matched_products),
                    len(fallback_products),
                    len(selected),
                    successful_queries,
                )
                return lane_key, selected, None

            try:
                products = await catalog_agent.search_products(
                    query=query,
                    max_price=request.budget_lkr,
                    in_stock_only=True,
                    limit=_SEARCH_LIMIT_PER_LANE,
                )
                return lane_key, products, None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gift Composer search lane '%s' failed: %s", lane_key, exc)
                return lane_key, [], str(exc)

    search_results = await asyncio.gather(
        *(search_lane(lane, query) for lane, query in lanes)
    )

    summaries_by_id: dict[str, Any] = {}
    source_lane_by_id: dict[str, str] = {}
    lane_product_ids: dict[str, list[str]] = {}
    lanes_by_product_id: dict[str, list[str]] = {}
    failed_searches = 0

    for lane, products, error in search_results:
        if error:
            failed_searches += 1
            continue
        lane_product_ids.setdefault(lane, [])
        for product in products:
            product_id = str(getattr(product, "product_id", "") or "").strip()
            if not product_id or product_id.lower() in {"none", "null", "unknown"}:
                continue
            summaries_by_id.setdefault(product_id, product)
            source_lane_by_id.setdefault(product_id, lane)
            if product_id not in lane_product_ids[lane]:
                lane_product_ids[lane].append(product_id)
            membership = lanes_by_product_id.setdefault(product_id, [])
            if lane not in membership:
                membership.append(lane)

    # Enrich candidates round-robin across category search lanes. The prior
    # lane-by-lane approach filled the 36-product budget from early lanes and
    # could leave later requested categories with no detailed product facts.
    candidates: list[Any] = []
    seen_ids: set[str] = set()
    max_lane_results = max((len(ids) for ids in lane_product_ids.values()), default=0)
    for result_index in range(max_lane_results):
        for lane, _ in lanes:
            ids = lane_product_ids.get(lane, [])
            if result_index >= len(ids):
                continue
            product_id = ids[result_index]
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            product = summaries_by_id.get(product_id)
            if product is None:
                continue
            candidates.append(product)
            if len(candidates) >= _MAX_CANDIDATES_TO_ENRICH:
                break
        if len(candidates) >= _MAX_CANDIDATES_TO_ENRICH:
            break

    details, enrichment_failures = await catalog_agent.enrich_products_with_failures(
        candidates,
        max_concurrency=_ENRICHMENT_CONCURRENCY,
        max_attempts=1,
    )

    # Re-associate each successful detail with the exact ID that was searched.
    # This is important for lane membership: otherwise a missing/mismatched
    # ID from a detail parser can make a successfully found category (such as
    # cakes) disappear later when facts are joined back to their search lane.
    details = _restore_search_ids_on_details(
        candidates,
        details,
        enrichment_failures,
    )
    logger.info(
        "Gift Composer enrichment summary searched=%d details=%d failures=%d",
        len(candidates),
        len(details),
        len(enrichment_failures),
    )

    if not details:
        message = (
            "Kapruka search could not provide usable product details just now. "
            "Please try again shortly."
            if failed_searches or candidates
            else "No products were returned for these gift searches. Try different preferences or a larger budget."
        )
        return GiftComposerResponse(
            status="no_eligible_products",
            recipient=resolved_recipient or recipient_query,
            occasion=occasion,
            budget_lkr=request.budget_lkr,
            delivery_city=request.delivery_city,
            delivery_date=request.delivery_date,
            message=message,
            bundles=[],
            standalone_options=[],
            delivery_checked=False,
        )

    batch_size = 8
    facts = await normalize_products(
        details,
        excluded_terms=excluded_terms,
        batch_size=batch_size,
    )

    eligibility = evaluate_all(facts, spec)
    eligible_ids = {
        str(result.product_id)
        for result in eligibility
        if result.eligible
    }

    # Diagnose category-specific loss after enrichment/normalization. Search
    # may succeed while a lane later disappears due to missing ID joins,
    # hard eligibility constraints, or an accessory/component classification.
    eligibility_by_id = {
        str(result.product_id): result
        for result in eligibility
    }
    normalized_by_id = {
        str(fact.product_id): fact
        for fact in facts
    }
    for lane, lane_ids in lane_product_ids.items():
        lane_facts_for_diagnostics = [
            normalized_by_id[product_id]
            for product_id in lane_ids
            if product_id in normalized_by_id
        ]
        failure_counts: dict[str, int] = {}
        role_rejected = 0
        eligible_for_lane = 0
        for fact in lane_facts_for_diagnostics:
            product_id = str(fact.product_id)
            result = eligibility_by_id.get(product_id)
            if result is None:
                failure_counts["eligibility_result_missing"] = (
                    failure_counts.get("eligibility_result_missing", 0) + 1
                )
                continue
            if not result.eligible:
                for reason in result.hard_failures:
                    failure_counts[reason] = failure_counts.get(reason, 0) + 1
                continue
            if fact.item_role in {"accessory", "component", "replacement", "service"}:
                role_rejected += 1
            else:
                eligible_for_lane += 1

        logger.info(
            "Gift Composer lane eligibility lane=%s search_ids=%d normalized_facts=%d "
            "eligible_primary_facts=%d hard_failure_counts=%s role_filtered=%d",
            lane,
            len(lane_ids),
            len(lane_facts_for_diagnostics),
            eligible_for_lane,
            failure_counts,
            role_rejected,
        )

    # For a multi-category gift basket, accessories/components are not treated
    # as standalone gift items unless the normalizer explicitly identifies them
    # as a bundle. The normal recommendation path itself remains untouched.
    by_id = {str(fact.product_id): fact for fact in facts}
    eligible_facts = [
        fact for fact in facts
        if str(fact.product_id) in eligible_ids
        and fact.item_role not in {"accessory", "component", "replacement", "service"}
    ]

    if not eligible_facts:
        message = (
            "No candidates passed the existing hard eligibility checks. "
            "Try broadening preferences or adjusting avoid/allergy terms."
        )
        if enrichment_failures:
            message += f" {len(enrichment_failures)} product-detail lookup(s) also failed."
        return GiftComposerResponse(
            status="no_eligible_products",
            recipient=resolved_recipient or recipient_query,
            occasion=occasion,
            budget_lkr=request.budget_lkr,
            delivery_city=request.delivery_city,
            delivery_date=request.delivery_date,
            message=message,
            bundles=[],
            standalone_options=[],
            delivery_checked=False,
        )

    ranked = await rank_products(
        products=eligible_facts,
        spec=spec,
        recipient=recipient_context,
        user_message=(
            f"Create a personalized gift preview for {recipient_query} for {occasion}. "
            f"Budget: {request.budget_lkr if request.budget_lkr is not None else 'not specified'} LKR. "
            f"Explicit preferences: {', '.join(preferences) or 'none specified'}. "
            f"Saved recipient preferences: {', '.join(saved_profile_preferences) or 'none'}. "
            "Prefer products that directly match these preferences when available, while preserving all hard exclusions."
        ),
        final_limit=len(eligible_facts),
    )

    # Preserve the ranking model's order within each preference tier. This is a
    # Gift Composer-only personalization layer; the main chat ranker is untouched.
    original_rank = {
        str(fact.product_id): index
        for index, fact in enumerate(ranked)
    }
    ranked.sort(
        key=lambda fact: (
            _preference_score(
                fact,
                effective_preferences,
                saved_profile_preferences,
            ),
            -original_rank.get(str(fact.product_id), 10**6),
        ),
        reverse=True,
    )

    details_by_id = {str(detail.product_id): detail for detail in details}
    group_by_id: dict[str, str] = {}
    preview_by_id: dict[str, dict[str, Any]] = {}

    for fact in ranked:
        lane = source_lane_by_id.get(str(fact.product_id), "other_gift")
        group = _classify_group(fact, lane)
        group_by_id[str(fact.product_id)] = group
        preview_by_id[str(fact.product_id)] = _make_product_preview(
            fact,
            group,
            effective_preferences,
            occasion,
        )

    # Map each eligible normalized product back to every search lane in which it
    # appeared. A single item can be returned by multiple category searches.
    facts_by_id = {str(fact.product_id): fact for fact in ranked}
    facts_by_lane: dict[str, list[ProductFacts]] = {lane: [] for lane, _ in lanes}
    for fact in ranked:
        fact_id = str(fact.product_id)
        for lane in lanes_by_product_id.get(fact_id, [source_lane_by_id.get(fact_id, "other_gift")]):
            facts_by_lane.setdefault(lane, []).append(fact)

    rank_index = {str(fact.product_id): index for index, fact in enumerate(ranked)}
    for lane in facts_by_lane:
        facts_by_lane[lane] = sorted(
            {str(f.product_id): f for f in facts_by_lane[lane]}.values(),
            key=lambda fact: rank_index.get(str(fact.product_id), 10**6),
        )

    # Each entered category and enabled saved recipient like becomes one card
    # slot. If no preferences exist, use the default product-search lanes to
    # create one card for each available category returned by those searches.
    slot_specs: list[dict[str, Any]] = []
    for lane, query in lanes:
        label = preference_lane_labels.get(lane)
        if label is None:
            label = {
                "cake": "Cake",
                "flowers": "Flowers",
                "hamper": "Gift hamper",
                "personalized": "Personalized gift",
                "sweets": "Chocolates",
            }.get(lane, query.strip().title())

        lane_facts = [
            fact for fact in facts_by_lane.get(lane, [])
            if request.budget_lkr is None or float(fact.price) <= request.budget_lkr
        ]
        if not lane_facts:
            logger.warning(
                "Gift Composer category slot omitted lane=%s label=%r "
                "target=%s search_ids=%d mapped_eligible_facts=0",
                lane,
                label,
                _preference_carousel_key(label),
                len(lane_product_ids.get(lane, [])),
            )
            continue

        target_key = _preference_carousel_key(label)
        slot_facts = lane_facts
        if target_key == "cake":
            slot_facts = [fact for fact in lane_facts if _is_cake_fact(fact)]
        elif target_key:
            typed = [
                fact for fact in lane_facts
                if _carousel_group_key(
                    fact,
                    group_by_id.get(str(fact.product_id), "other_gift"),
                ) == target_key
            ]
            # The exact term search is still a useful source when the category
            # name is descriptive (for example "chocolate birthday cake") and
            # the catalog's type labels differ. Prefer exact type matches first.
            if typed:
                slot_facts = typed
            else:
                text_matches = [
                    fact for fact in lane_facts
                    if _term_matches_text(label, fact.searchable_text())
                ]
                if text_matches:
                    slot_facts = text_matches
                elif lane.startswith("preference_") and target_key != "cake":
                    # Non-cake lanes still require their normalized product text
                    # to match the requested type. Cake summaries have already
                    # been category-verified during search, so retain those
                    # source-lane facts if the normalizer omits that word.
                    slot_facts = []

        logger.info(
            "Gift Composer category slot lane=%s label=%r target=%s lane_facts=%d accepted=%d",
            lane,
            label,
            target_key,
            len(lane_facts),
            len(slot_facts),
        )
        if slot_facts:
            slot_specs.append({
                "lane": lane,
                "label": label,
                "candidates": slot_facts,
                "target_key": target_key,
            })

    selected_slots = _select_category_products(
        slot_specs,
        budget_lkr=request.budget_lkr,
        ranked=ranked,
        preferences=effective_preferences,
        saved_preferences=saved_profile_preferences,
    )

    selected_ids = {str(fact.product_id) for _, _, fact in selected_slots}
    selected_by_lane = {lane: fact for lane, _, fact in selected_slots}
    slot_spec_by_lane = {slot["lane"]: slot for slot in slot_specs}
    carousel_candidates_by_lane, carousel_caps_by_lane = _budget_safe_carousel_candidates(
        slot_specs,
        selected_slots,
        request.budget_lkr,
    )

    # Defensive server-side invariant: neither the initial products nor their
    # carousel alternatives may expose a combination above the requested budget.
    if request.budget_lkr is not None and selected_slots:
        selected_total = sum(_price_cents(fact.price) for _, _, fact in selected_slots)
        worst_case_total = sum(carousel_caps_by_lane.get(lane, _price_cents(fact.price)) for lane, _, fact in selected_slots)
        budget_cents = _price_cents(request.budget_lkr)
        if selected_total > budget_cents or worst_case_total > budget_cents:
            logger.error(
                "Gift Composer budget invariant failed; suppressing unsafe bundle",
                extra={
                    "selected_total_lkr": round(selected_total / 100, 2),
                    "worst_case_total_lkr": round(worst_case_total / 100, 2),
                    "budget_lkr": round(budget_cents / 100, 2),
                },
            )
            selected_slots = []
            selected_ids = set()
            selected_by_lane = {}
    bundles: list[dict[str, Any]] = []
    standalone: list[dict[str, Any]] = []
    status = "bundles_found"

    if selected_slots:
        selected_previews: list[dict[str, Any]] = []
        for lane, label, selected_fact in selected_slots:
            selected_id = str(selected_fact.product_id)
            slot_spec = slot_spec_by_lane[lane]
            # Each slot's alternatives are capped so the sum of the most expensive
            # option in every slot stays within the user's total budget.
            alternatives = carousel_candidates_by_lane.get(
                lane, [selected_fact]
            )
            other_selected_ids = selected_ids - {selected_id}
            ordered_options = [selected_fact] + [
                fact for fact in alternatives
                if str(fact.product_id) != selected_id
                and str(fact.product_id) not in other_selected_ids
            ]

            option_payloads: list[dict[str, Any]] = []
            for candidate in ordered_options[:12]:
                candidate_id = str(candidate.product_id)
                payload = dict(preview_by_id[candidate_id])
                payload["group"] = label.title()
                payload.pop("options", None)
                option_payloads.append(payload)

            selected_payload = dict(preview_by_id[selected_id])
            selected_payload["group"] = label.title()
            selected_payload["options"] = option_payloads
            selected_previews.append(selected_payload)

        total = round(sum(float(fact.price or 0.0) for _, _, fact in selected_slots), 2)
        bundle_message = None
        bundles.append({
            "id": "gift-bundle-1",
            "title": "Best match",
            "description": "One selected item for each available requested category, personalized using the enabled recipient preferences.",
            "total": total,
            "currency": selected_slots[0][2].currency or "LKR",
            "products": selected_previews,
            "facts": [fact for _, _, fact in selected_slots],
            "delivery_status": "not_checked",
            "delivery_message": bundle_message,
        })

        missing_categories = [
            preference_lane_labels.get(lane, label)
            for lane, label in preference_lane_labels.items()
            if lane not in slot_spec_by_lane
        ]
        budget_omitted_categories = [
            slot["label"] for slot in slot_specs
            if slot["lane"] not in selected_by_lane
        ]
        message = (
            "This preview contains one selected product for each category that could be matched "
            "and passed the existing eligibility checks. Product totals exclude delivery charges; "
            "products are not reserved or ordered."
        )
        if missing_categories:
            message += " No eligible matching item was found for: " + ", ".join(missing_categories) + "."
        if budget_omitted_categories:
            message += (
                " The total budget could not accommodate every matched category, so these were left out: "
                + ", ".join(budget_omitted_categories) + "."
            )
        if omitted_categories:
            message += (
                f" To keep the preview within catalog request limits, only the first "
                f"{_MAX_PREFERENCE_SEARCH_LANES} categories were searched. Not searched: "
                + ", ".join(omitted_categories) + "."
            )
        if failed_searches:
            message += " Some category searches failed; the preview may not cover every requested category."
        if enrichment_failures:
            message += " Some product details could not be loaded and those products were excluded."
    else:
        status = "no_eligible_products"
        message = (
            "No requested category produced an eligible, in-budget product. "
            "Try a different category, a larger budget, or review the avoid/allergy terms."
        )
        if omitted_categories:
            message += (
                f" Only the first {_MAX_PREFERENCE_SEARCH_LANES} categories were searched; "
                "not searched: " + ", ".join(omitted_categories) + "."
            )
        if failed_searches:
            message += " One or more catalog searches failed; this does not prove that products are unavailable."
        if enrichment_failures:
            message += f" {len(enrichment_failures)} product-detail lookup(s) failed."

    delivery_checked, canonical_city, delivery_note = await _check_delivery_for_options(
        bundles=bundles,
        standalone=standalone,
        city=(request.delivery_city.strip() if request.delivery_city else None),
        requested_date=request.delivery_date,
    )

    # Convert internal facts used for delivery assessment away from the public
    # response schema; no internal model/debug fields leak to the frontend.
    public_bundles = []
    for bundle in bundles:
        public_bundles.append({
            key: bundle[key]
            for key in (
                "id", "title", "description", "total", "currency", "products",
                "delivery_status", "delivery_message"
            )
        })

    final_city = canonical_city or request.delivery_city
    if delivery_note and "read-only" not in message.lower():
        message = f"{message} {delivery_note}"

    return GiftComposerResponse(
        status=status,
        recipient=resolved_recipient or recipient_query,
        occasion=occasion,
        budget_lkr=request.budget_lkr,
        delivery_city=final_city,
        delivery_date=request.delivery_date,
        message=message,
        bundles=public_bundles,
        standalone_options=standalone,
        delivery_checked=delivery_checked,
    )
