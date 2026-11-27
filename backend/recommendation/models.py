"""Canonical typed models for the recommendation pipeline.

The recommendation system intentionally separates:
- the current shopping request;
- durable recipient preferences; and
- normalized product facts.

Hard constraints are enforced by code after retrieval. LLMs may interpret or
rank information, but they are never allowed to resurrect an ineligible product.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ConstraintKind = Literal[
    "attribute",
    "brand",
    "model",
    "variant",
    "feature",
    "style",
    "color",
    "material",
    "audience",
    "other",
]

ConstraintPolarity = Literal["include", "exclude"]

QuantityMode = Literal["exact", "at_least", "at_most"]


ItemRole = Literal[
    "primary_product",
    "accessory",
    "component",
    "replacement",
    "bundle",
    "service",
    "unknown",
]


class BudgetConstraint(BaseModel):
    min_price: float | None = None
    max_price: float | None = None
    currency: str = "LKR"
    source: Literal["current_message"] = "current_message"


class QuantityValue(BaseModel):
    value: float
    unit: str


class QuantityConstraint(BaseModel):
    value: float
    unit: str
    mode: QuantityMode = "exact"
    source: Literal["current_message"] = "current_message"


class AttributeConstraint(BaseModel):
    term: str
    kind: ConstraintKind = "attribute"
    polarity: ConstraintPolarity = "include"
    source: Literal["current_message", "durable_profile"] = "current_message"
    # A safety-critical exclusion (for example, a recipient allergy) is
    # fail-closed when product evidence is unknown. Generic exclusions such
    # as color/style dislikes remain text-based hard constraints without
    # turning missing metadata into an automatic rejection.
    safety_critical: bool = False


class RecipientContext(BaseModel):
    name: str | None = None
    relationship: str | None = None
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    gender_hint: Literal["male", "female", "neutral", "unknown"] = "unknown"


class ShoppingSpec(BaseModel):
    """Complete turn-scoped shopping specification."""

    product_family: str | None = None
    canonical_product_type: str | None = None
    item_role: ItemRole = "primary_product"
    parent_product_type: str | None = None
    recipient_name: str | None = None
    occasion: str | None = None

    # Current-turn financial state only.
    budget: BudgetConstraint | None = None

    # Current message hard constraints.
    required_attributes: list[AttributeConstraint] = Field(default_factory=list)
    excluded_attributes: list[AttributeConstraint] = Field(default_factory=list)
    required_components: list[str] = Field(default_factory=list)
    excluded_components: list[str] = Field(default_factory=list)
    quantity: QuantityConstraint | None = None

    in_stock_only: bool = True

    # Durable likes are deliberately soft.
    preferred_attributes: list[str] = Field(default_factory=list)

    # Retrieval hints only; these are never used as proof of eligibility.
    search_queries: list[str] = Field(default_factory=list)

    # Useful for UI/debugging and auditing, but not used as a constraint.
    ambiguities: list[str] = Field(default_factory=list)


class ProductFacts(BaseModel):
    """Normalized facts extracted from a Kapruka product detail response."""

    product_id: str
    name: str
    price: float
    currency: str = "LKR"
    in_stock: bool | None = None
    category: str | None = None
    vendor: str | None = None
    url: str | None = None
    image_url: str | None = None

    description: str = ""
    safety_text: str = ""

    # LLM-assisted semantic facts. Every field is grounded in the supplied
    # product text; deterministic verification still checks the raw text.
    product_family: str | None = None
    canonical_product_type: str | None = None
    item_role: ItemRole = "unknown"
    parent_product_type: str | None = None
    audience: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)

    package_quantity: QuantityValue | None = None

    # excluded_term -> present / not_found / unknown
    safety_flags: dict[str, Literal["present", "not_found", "unknown"]] = Field(
        default_factory=dict
    )
    # Short deterministic evidence snippets/reasons used for debugging and
    # auditability. The recommendation UI does not need to expose this.
    safety_evidence: dict[str, str] = Field(default_factory=dict)

    raw: dict[str, Any] = Field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [
            self.name,
            self.category or "",
            self.vendor or "",
            self.product_family or "",
            self.description,
            self.safety_text,
            *self.attributes,
            *self.components,
            *self.audience,
        ]
        return " ".join(part for part in parts if part)


class EligibilityResult(BaseModel):
    product_id: str
    eligible: bool
    hard_failures: list[str] = Field(default_factory=list)
    matched_constraints: list[str] = Field(default_factory=list)
    soft_matches: list[str] = Field(default_factory=list)


class RankedProduct(BaseModel):
    product_id: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    status: Literal["recommendations_found", "no_exact_match"]
    spec: ShoppingSpec
    recipient: RecipientContext | None = None
    products: list[dict[str, Any]] = Field(default_factory=list)
    ranked_product_ids: list[str] = Field(default_factory=list)
    eligibility: list[EligibilityResult] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    shown_count: int = 0
    requested_limit: int = 0