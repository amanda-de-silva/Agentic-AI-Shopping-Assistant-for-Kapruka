from backend.recommendation.eligibility import (
    evaluate_product,
)
from backend.recommendation.models import (
    AttributeConstraint,
    BudgetConstraint,
    ProductFacts,
    QuantityConstraint,
    QuantityValue,
    ShoppingSpec,
)


def test_exact_budget_and_allergy_exclusion():
    spec = ShoppingSpec(
        product_family="cake",
        budget=BudgetConstraint(
            max_price=7000
        ),
        excluded_attributes=[
            AttributeConstraint(
                term="chocolate",
                polarity="exclude",
                source="durable_profile",
            )
        ],
    )

    product = ProductFacts(
        product_id="cake-1",
        name="Vanilla Anniversary Cake",
        price=6500,
        category="Cakes",
        in_stock=True,
        description="Vanilla cake with cream.",
        safety_text=(
            "## Vanilla Anniversary Cake\n"
            "Vanilla cake"
        ),
    )

    result = evaluate_product(
        product,
        spec,
    )

    assert result.eligible


def test_budget_is_hard():
    spec = ShoppingSpec(
        product_family="watch",
        budget=BudgetConstraint(
            max_price=10000
        ),
    )

    product = ProductFacts(
        product_id="watch-1",
        name="Classic Watch",
        price=12000,
        category="Watches",
        in_stock=True,
    )

    result = evaluate_product(
        product,
        spec,
    )

    assert not result.eligible
    assert (
        "over_maximum_budget"
        in result.hard_failures
    )


def test_exact_package_size_is_hard():
    spec = ShoppingSpec(
        product_family="chicken sausages",
        quantity=QuantityConstraint(
            value=1,
            unit="kg",
            mode="exact",
        ),
    )

    good = ProductFacts(
        product_id="sausage-1",
        name="Chicken Sausages 1KG",
        price=2400,
        category="Frozen",
        in_stock=True,
        package_quantity=QuantityValue(
            value=1,
            unit="kg",
        ),
    )

    bad = ProductFacts(
        product_id="sausage-2",
        name="Chicken Sausages 500g",
        price=1260,
        category="Frozen",
        in_stock=True,
        package_quantity=QuantityValue(
            value=500,
            unit="g",
        ),
    )

    assert evaluate_product(
        good,
        spec,
    ).eligible

    assert not evaluate_product(
        bad,
        spec,
    ).eligible


def test_required_bundle_components_must_be_in_same_product():
    spec = ShoppingSpec(
        product_family="gift combo",
        required_components=[
            "cake",
            "flowers",
        ],
    )

    valid = ProductFacts(
        product_id="combo-1",
        name="Anniversary Combo Gift",
        price=7000,
        category="Gift Packs",
        in_stock=True,
        description=(
            "Includes a vanilla cake and "
            "a fresh flower bouquet."
        ),
        safety_text=(
            "Includes cake and flowers."
        ),
        components=[
            "cake",
            "flowers",
        ],
    )

    invalid = ProductFacts(
        product_id="combo-2",
        name="Gents Gift Pack",
        price=5000,
        category="Gift Packs",
        in_stock=True,
        description=(
            "Bath towel and cotton sarong."
        ),
        safety_text=(
            "Bath towel and cotton sarong."
        ),
        components=[
            "bath towel",
            "cotton sarong",
        ],
    )

    assert evaluate_product(
        valid,
        spec,
    ).eligible

    assert not evaluate_product(
        invalid,
        spec,
    ).eligible