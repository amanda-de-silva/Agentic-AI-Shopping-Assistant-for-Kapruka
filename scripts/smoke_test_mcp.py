"""
Pings all 7 Kapruka MCP tools once each and prints latency/success --
run this first when standing the project up (see docs/architecture.md, build order step 1).

Each check is isolated in its own try/except so one failing tool doesn't hide results
for the others, and the connection is always closed in a `finally` block -- an
uncaught exception skipping cleanup is what turns one real bug into two confusing
error messages (the actual failure, plus an unrelated-looking anyio teardown error
from the connection being abandoned mid-task instead of closed properly).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.mcp.client import get_mcp_client  # noqa: E402
from backend.mcp.markdown_parsers import (  # noqa: E402
    extract_markdown_result,
    parse_delivery_cities_markdown,
    parse_search_products_markdown,
)
from backend.mcp.tool_schemas import (  # noqa: E402
    CheckDeliveryInput,
    GetProductInput,
    ListCategoriesInput,
    ListDeliveryCitiesInput,
    SearchProductsInput,
    TrackOrderInput,
)


async def main() -> None:
    client = get_mcp_client()
    checks: list[tuple[str, object]] = []

    try:
        await client.connect()
        tools = await client.list_tools()
        print(f"Connected. Server exposes {len(tools)} tools: {tools}\n")

        known_tools = {
            "kapruka_search_products",
            "kapruka_get_product",
            "kapruka_list_categories",
            "kapruka_list_delivery_cities",
            "kapruka_check_delivery",
            "kapruka_create_order",
            "kapruka_track_order",
        }
        extra_tools = set(tools) - known_tools
        if extra_tools:
            print(
                f"NOTE: server exposes tool(s) not wired up in this app yet: "
                f"{sorted(extra_tools)} -- see config/mcp_tools.yaml\n"
            )

        products: list[dict] = []
        try:
            result = await client.search_products(SearchProductsInput(q="chocolate", limit=3))
            checks.append(("search_products", result))
            products = (result.data or {}).get("products") or (result.data or {}).get("items") or []
            markdown = extract_markdown_result(result.data)
            if markdown is not None:
                products = parse_search_products_markdown(markdown)["products"]
        except Exception as exc:  # noqa: BLE001
            print(f"search_products FAILED: {exc}")

        if products:
            try:
                product_id = str(products[0].get("product_id") or products[0].get("id"))
                result = await client.get_product(GetProductInput(product_id=product_id))
                checks.append(("get_product", result))
            except Exception as exc:  # noqa: BLE001
                print(f"get_product FAILED: {exc}")

        try:
            result = await client.list_categories(ListCategoriesInput(depth=1))
            checks.append(("list_categories", result))
        except Exception as exc:  # noqa: BLE001
            print(f"list_categories FAILED: {exc}")

        cities: list[dict] = []
        try:
            result = await client.list_delivery_cities(ListDeliveryCitiesInput(query="colombo", limit=5))
            checks.append(("list_delivery_cities", result))
            cities = (result.data or {}).get("cities") or (result.data or {}).get("matches") or []
            markdown = extract_markdown_result(result.data)
            if markdown is not None:
                cities = parse_delivery_cities_markdown(markdown)["cities"]
        except Exception as exc:  # noqa: BLE001
            print(f"list_delivery_cities FAILED: {exc}")

        if cities:
            try:
                result = await client.check_delivery(
                    CheckDeliveryInput(city=cities[0]["name"], delivery_date="2026-08-20")
                )
                checks.append(("check_delivery", result))
            except Exception as exc:  # noqa: BLE001
                print(f"check_delivery FAILED: {exc}")

        # create_order is deliberately NOT smoke-tested here -- it places a real order
        # against Kapruka's 30-orders/hour-per-IP cap. Test it manually via /api/checkout.
        try:
            result = await client.track_order(TrackOrderInput(order_number="TEST-0000"))
            checks.append(("track_order (expected not-found)", result))
        except Exception as exc:  # noqa: BLE001
            print(f"track_order -- expected failure for a fake order number: {exc}")

        print("\nResults:")
        for name, result in checks:
            status = "OK" if result.ok else "FAILED"
            print(f"  {name:<28} {status:<8} {result.latency_ms:.0f}ms")

        print("\nRaw response shapes (for verifying catalog_agent.py / logistics_agent.py field names):")
        # Every tool that ran gets printed now -- get_product and check_delivery were
        # excluded before simply because they'd never successfully run yet to have
        # anything to show. Now that chaining works, their field-name assumptions in
        # catalog_agent.py / logistics_agent.py are just as unverified as
        # search_products / list_delivery_cities were, so they need the same check.
        for name, result in checks:
            if result.data is not None:
                print(f"\n--- {name} raw data ---")
                print(json.dumps(result.data, indent=2, default=str)[:2000])

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())