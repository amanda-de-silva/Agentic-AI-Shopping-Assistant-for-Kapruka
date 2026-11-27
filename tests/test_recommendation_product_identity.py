from backend.recommendation.eligibility import evaluate_product
from backend.recommendation.models import (
    AttributeConstraint,
    ProductFacts,
    ShoppingSpec,
)


def test_mobile_phone_does_not_match_phone_case():
    spec = ShoppingSpec(
        product_family="samsung mobile phone",
    )

    phone = ProductFacts(
        product_id="phone-1",
        name="Samsung Galaxy A35 8GB 256GB Smart Mobile Phone",
        price=125000,
        category="Mobile Phones",
        in_stock=True,
    )

    case = ProductFacts(
        product_id="case-1",
        name="S22 Ultra Samsung Galaxy Silicone Transparent Back Cover",
        price=1790,
        category="Mobile Accessories",
        in_stock=True,
        description=(
            "Transparent protective back cover "
            "for Samsung Galaxy S22 Ultra."
        ),
    )

    assert evaluate_product(
        phone,
        spec,
    ).eligible

    result = evaluate_product(
        case,
        spec,
    )

    assert not result.eligible
    assert (
        "product_family_mismatch"
        in result.hard_failures
    )


def test_phone_case_request_still_accepts_phone_case():
    spec = ShoppingSpec(
        product_family="phone case",
    )

    case = ProductFacts(
        product_id="case-2",
        name="Samsung Galaxy Phone Case",
        price=1800,
        category="Mobile Accessories",
        in_stock=True,
    )

    assert evaluate_product(
        case,
        spec,
    ).eligible


def test_watch_box_does_not_match_watch():
    spec = ShoppingSpec(
        product_family="watch",
    )

    watch = ProductFacts(
        product_id="watch-1",
        name="Curren Men's Classic Wrist Watch",
        price=9500,
        category="Watches",
        in_stock=True,
    )

    box = ProductFacts(
        product_id="box-1",
        name="Luxury Watch Display Box",
        price=2500,
        category="Watch Accessories",
        in_stock=True,
    )

    assert evaluate_product(
        watch,
        spec,
    ).eligible

    assert not evaluate_product(
        box,
        spec,
    ).eligible


def test_laptop_sleeve_does_not_match_laptop():
    spec = ShoppingSpec(
        product_family="laptop",
    )

    laptop = ProductFacts(
        product_id="laptop-1",
        name="HP Laptop 15 inch",
        price=150000,
        category="Laptops",
        in_stock=True,
    )

    sleeve = ProductFacts(
        product_id="sleeve-1",
        name="15 Inch Laptop Sleeve",
        price=3500,
        category="Laptop Accessories",
        in_stock=True,
        description="Protective sleeve for laptops.",
    )

    assert evaluate_product(
        laptop,
        spec,
    ).eligible

    assert not evaluate_product(
        sleeve,
        spec,
    ).eligible