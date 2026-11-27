from __future__ import annotations

from backend.agents.catalog_agent import violates_constraints
from backend.schemas.product import ProductDetail, ProductSummary


def test_deterministic_safety_guard_checks_description_and_safety_text():
    product = ProductDetail(
        product_id="P1",
        name="Chocolate Cake",
        price=2500,
        description="Contains peanuts and milk.",
        safety_text="Ingredients: cocoa, wheat, peanuts, milk.",
    )
    assert violates_constraints(product, ["peanuts"])
    assert violates_constraints(product, ["milk"])


def test_word_matching_avoids_common_substring_false_positive():
    product = ProductSummary(product_id="P2", name="Coconut Cake", price=1000, safety_text="coconut")
    assert not violates_constraints(product, ["nut"])