"""
Parsers for Kapruka MCP tool responses.

Supports both Markdown and JSON product-detail responses. JSON is preferred
for product details because Kapruka's structured description field can contain
more complete text than its human-readable Markdown output.

The other parsers preserve their existing responsibilities.
"""
from __future__ import annotations

import json
import re
from typing import Any

_STOCK_SEPARATOR_RE = re.compile(r"[·•]")
_FIELD_LINE_RE = re.compile(
    r"^\*\*(?P<label>[^*]+)\*\*:\s*(?P<value>.+)$"
)


def parse_search_products_markdown(markdown: str) -> dict:
    """Parse kapruka_search_products Markdown into product dictionaries."""
    products: list[dict] = []
    current: dict | None = None

    for line in markdown.splitlines():
        stripped = line.strip()

        name_match = re.match(
            r"\*\*\d+\.\s+(.+?)\*\*$",
            stripped,
        )
        if name_match:
            if current:
                products.append(current)

            current = {
                "name": name_match.group(1).strip()
            }
            continue

        id_match = re.match(
            r"ID:\s*`([^`]+)`\s*[·•]\s*LKR\s*"
            r"([\d,]+(?:\.\d+)?)\s*[·•]\s*(.+)",
            stripped,
        )
        if id_match and current is not None:
            current["product_id"] = id_match.group(1)
            current["price"] = float(
                id_match.group(2).replace(",", "")
            )
            current["currency"] = "LKR"

            rest = id_match.group(3)
            parts = [
                part.strip()
                for part in _STOCK_SEPARATOR_RE.split(rest)
            ]
            stock_text = parts[0] if parts else ""

            current["in_stock"] = (
                "out of stock" not in stock_text.lower()
            )
            current["stock_note"] = stock_text
            continue

        url_match = re.match(
            r"\[View product\]\(([^)]+)\)",
            stripped,
        )
        if url_match and current is not None:
            current["url"] = url_match.group(1)
            continue

    if current:
        products.append(current)

    cursor_match = re.search(
        r'cursor="([^"]+)"',
        markdown,
    )

    return {
        "products": products,
        "next_cursor": (
            cursor_match.group(1)
            if cursor_match
            else None
        ),
    }


def _normalize_product_detail_json(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize Kapruka's JSON product schema into the application's flat shape.

    Retain the original fields and preserve the full description string.
    """
    data: dict[str, Any] = dict(payload)

    data["product_id"] = str(
        payload.get("product_id")
        or payload.get("id")
        or ""
    ).strip()

    price = payload.get("price")

    if isinstance(price, dict):
        amount = price.get("amount")

        if amount is not None:
            try:
                data["price"] = float(amount)
            except (TypeError, ValueError):
                pass

        data["currency"] = str(
            price.get("currency")
            or payload.get("currency")
            or "LKR"
        )

    elif price is not None:
        try:
            data["price"] = float(price)
        except (TypeError, ValueError):
            pass

        data["currency"] = str(
            payload.get("currency") or "LKR"
        )

    else:
        data["currency"] = str(
            payload.get("currency") or "LKR"
        )

    category = payload.get("category")
    if isinstance(category, dict):
        data["category"] = (
            category.get("name")
            or category.get("path")
        )

    attributes = payload.get("attributes")
    if isinstance(attributes, dict):
        data["vendor"] = (
            data.get("vendor")
            or attributes.get("vendor")
        )
        data["weight"] = (
            data.get("weight")
            or attributes.get("weight")
        )
        data["product_type"] = attributes.get("type")
        data["product_subtype"] = attributes.get("subtype")

    shipping = payload.get("shipping")
    if isinstance(shipping, dict):
        if "ships_internationally" not in data:
            data["ships_internationally"] = (
                shipping.get("ships_internationally")
            )

    stock_level = payload.get("stock_level")

    if payload.get("in_stock") is None and stock_level is not None:
        normalized_stock = (
            str(stock_level).strip().lower()
        )

        data["in_stock"] = normalized_stock not in {
            "out",
            "out of stock",
            "unavailable",
            "false",
            "none",
        }

    if stock_level is not None:
        data["stock_note"] = str(stock_level)

    images = payload.get("images")

    if isinstance(images, list):
        data["images"] = [
            str(image)
            for image in images
            if image
        ]

        data["image_url"] = (
            data.get("image_url")
            or (
                data["images"][0]
                if data["images"]
                else None
            )
        )

    elif payload.get("image_url"):
        data["images"] = [
            str(payload["image_url"])
        ]

    # Use description as the primary source. Summary is a fallback only
    # when description is absent or empty.
    description = payload.get("description")

    if (
        not isinstance(description, str)
        or not description.strip()
    ):
        description = payload.get("summary")

    if isinstance(description, str):
        # Do not slice or truncate the description here.
        data["description"] = description.strip()

    data["variants"] = payload.get("variants") or []
    data["url"] = payload.get("url") or data.get("url")

    return data


def parse_product_detail_payload(payload: Any) -> dict:
    """
    Parse product details returned as JSON, wrapped JSON, or Markdown.

    Supports MCP responses such as:
      {"result": "...Markdown..."}
      {"result": "{\"id\": \"...\", \"description\": \"...\"}"}
      {"raw_markdown": "...Markdown..."}
    """
    if isinstance(payload, dict):
        wrapped_result = payload.get("result")

        if isinstance(wrapped_result, str):
            return parse_product_detail_markdown(
                wrapped_result
            )

        if isinstance(wrapped_result, dict):
            return _normalize_product_detail_json(
                wrapped_result
            )

        raw_markdown = payload.get("raw_markdown")

        if isinstance(raw_markdown, str):
            return parse_product_detail_markdown(
                raw_markdown
            )

        if payload:
            return _normalize_product_detail_json(
                payload
            )

        return {}

    if isinstance(payload, str):
        return parse_product_detail_markdown(payload)

    return {}


def parse_product_detail_markdown(markdown: str) -> dict:
    """
    Parse product detail output in either JSON or Markdown format.

    JSON is handled first so the complete structured description is preserved
    whenever Kapruka returns it.
    """
    text = str(markdown or "").strip()

    if not text:
        return {}

    # The MCP client may wrap JSON in a text result.
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed_json = json.loads(text)

            if isinstance(parsed_json, dict):
                return _normalize_product_detail_json(
                    parsed_json
                )

        except (json.JSONDecodeError, TypeError, ValueError):
            # Fall back to the existing Markdown path.
            pass

    data: dict[str, Any] = {}
    lines = text.splitlines()

    # Product heading.
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## "):
            data["name"] = stripped[3:].strip()
            break

    # Collect metadata from the entire response.
    metadata: dict[str, str] = {}

    for line in lines:
        match = _FIELD_LINE_RE.match(line.strip())

        if match:
            metadata[
                match.group("label").strip()
            ] = match.group("value").strip()

    if "ID" in metadata:
        data["product_id"] = metadata["ID"].strip("`")

    if "Price" in metadata:
        price_match = re.search(
            r"LKR\s*([\d,]+(?:\.\d+)?)",
            metadata["Price"],
        )

        if price_match:
            data["price"] = float(
                price_match.group(1).replace(",", "")
            )

        data["currency"] = "LKR"

    if "Stock" in metadata:
        data["in_stock"] = (
            "out of stock"
            not in metadata["Stock"].lower()
        )
        data["stock_note"] = metadata["Stock"]

    if "Category" in metadata:
        data["category"] = metadata["Category"]

    if "Vendor" in metadata:
        data["vendor"] = metadata["Vendor"]

    if "Weight" in metadata:
        data["weight"] = metadata["Weight"]

    if "International shipping" in metadata:
        data["ships_internationally"] = (
            metadata["International shipping"]
            .strip()
            .lower()
            .startswith("y")
        )

    if "Image" in metadata:
        data["images"] = [metadata["Image"]]

    url_match = re.search(
        r"\[View on Kapruka\]\(([^)]+)\)",
        text,
    )

    if url_match:
        data["url"] = url_match.group(1)

    # Find the start of the free-text description.
    content_start_idx: int | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()

        if (
            not stripped
            or stripped.startswith(("## ", "["))
        ):
            continue

        if _FIELD_LINE_RE.match(stripped):
            continue

        content_start_idx = index
        break

    if content_start_idx is not None:
        desc_lines: list[str] = []

        for line in lines[content_start_idx:]:
            stripped = line.strip()

            # Preserve paragraph boundaries rather than stopping at the first
            # blank line. This fixes loss of later description paragraphs.
            if not stripped:
                if desc_lines and desc_lines[-1] != "":
                    desc_lines.append("")
                continue

            # Do not include later structured metadata or navigation links.
            if (
                stripped.startswith(("## ", "**", "["))
                or _FIELD_LINE_RE.match(stripped)
            ):
                break

            desc_lines.append(stripped)

        description = "\n".join(desc_lines).strip()

        if description:
            data["description"] = description

    return data


def parse_categories_markdown(markdown: str) -> dict:
    """Parse Kapruka category Markdown."""
    categories: list[dict] = []

    for line in markdown.splitlines():
        match = re.match(
            r"-\s+\[(.+?)\]\((.+?)\)",
            line.strip(),
        )

        if match:
            categories.append({
                "name": match.group(1).strip(),
                "url": match.group(2).strip(),
            })

    return {"categories": categories}


def parse_delivery_cities_markdown(markdown: str) -> dict:
    """Parse Kapruka delivery-city Markdown."""
    cities: list[dict] = []

    for line in markdown.splitlines():
        stripped = line.strip()

        match = re.match(
            r"-\s+\*\*(.+?)\*\*"
            r"(?:\s+_aliases:\s+(.+?)_)?$",
            stripped,
        )

        if match:
            name = match.group(1).strip()
            aliases_text = match.group(2)

            aliases = (
                aliases_text.split()
                if aliases_text
                else []
            )

            cities.append({
                "name": name,
                "aliases": aliases,
            })

    total_match = re.search(
        r"\((\d+)\s+of\s+(\d+)\)",
        markdown,
    )

    shown = (
        int(total_match.group(1))
        if total_match
        else None
    )
    total = (
        int(total_match.group(2))
        if total_match
        else None
    )

    return {
        "cities": cities,
        "shown": shown,
        "total": total,
    }


def parse_check_delivery_markdown(markdown: str) -> dict:
    """Parse Kapruka delivery-availability Markdown."""
    data: dict[str, Any] = {}

    header_match = re.search(
        r"## Delivery to (.+?) on (\d{4}-\d{2}-\d{2})",
        markdown,
    )

    if header_match:
        data["city"] = header_match.group(1).strip()
        data["delivery_date"] = header_match.group(2).strip()

    if re.search(
        r"\*\*Available\*\*",
        markdown,
        re.IGNORECASE,
    ):
        data["deliverable"] = True

    elif re.search(
        r"\*\*(Not available|Unavailable)\*\*",
        markdown,
        re.IGNORECASE,
    ):
        data["deliverable"] = False

    else:
        data["deliverable"] = None

    rate_match = re.search(
        r"LKR\s*([\d,]+(?:\.\d+)?)",
        markdown,
    )

    if rate_match:
        data["rate_lkr"] = float(
            rate_match.group(1).replace(",", "")
        )

    warning_match = re.search(
        r"(?i)perishable[^\n]*",
        markdown,
    )

    data["perishable_warning"] = (
        warning_match.group(0).strip()
        if warning_match
        else None
    )

    return data


def parse_create_order_markdown(markdown: str) -> dict:
    """Parse Kapruka create-order success or error Markdown."""
    text = str(markdown or "").strip()

    if not text:
        return {}

    error_match = re.match(
        r"^\s*Error\s*\((?P<code>[^)]+)\)\s*:\s*"
        r"(?P<message>.+?)\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if error_match:
        return {
            "error_code": error_match.group("code").strip(),
            "message": error_match.group("message").strip(),
            "raw_markdown": text,
        }

    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)

            if isinstance(parsed, dict):
                return parsed

        except Exception:
            pass

    data: dict[str, Any] = {
        "raw_markdown": text
    }

    metadata: dict[str, str] = {}

    for line in text.splitlines():
        stripped = line.strip()
        match = _FIELD_LINE_RE.match(stripped)

        if match:
            label = re.sub(
                r"[*_`]+",
                "",
                match.group("label"),
            ).strip().lower()

            metadata[label] = match.group("value").strip()
            continue

        plain = re.match(
            r"^\s*([A-Za-z][A-Za-z _/-]{1,60})\s*:\s*"
            r"(.+?)\s*$",
            stripped,
        )

        if plain:
            metadata[
                plain.group(1).strip().lower()
            ] = plain.group(2).strip()

    for label in (
        "order number",
        "order no",
        "order no.",
        "order reference",
        "order ref",
        "order id",
        "order",
    ):
        value = metadata.get(label)

        if value:
            value = value.strip("` ")

            value_match = re.search(
                r"[A-Za-z0-9][A-Za-z0-9_-]{4,}",
                value,
            )

            if value_match:
                data["order_number"] = value_match.group(0)
                break

    url_labels = (
        "checkout url",
        "checkout link",
        "payment url",
        "payment link",
        "pay url",
        "pay link",
        "proceed to checkout",
        "complete payment",
        "pay now",
    )

    for label in url_labels:
        value = metadata.get(label)

        if not value:
            continue

        markdown_link = re.search(
            r"\[[^]]+\]\((https?://[^)]+)\)",
            value,
            re.IGNORECASE,
        )

        if markdown_link:
            data["checkout_url"] = markdown_link.group(1).strip()
            break

        bare = re.search(
            r"https?://[^\s)]+",
            value,
            re.IGNORECASE,
        )

        if bare:
            data["checkout_url"] = bare.group(0).rstrip(".,;)")
            break

    if "checkout_url" not in data:
        for match in re.finditer(
            r"\[([^]]*(?:checkout|pay|payment|complete)[^]]*)\]"
            r"\((https?://[^)]+)\)",
            text,
            re.IGNORECASE,
        ):
            data["checkout_url"] = match.group(2).strip()
            break

    if "checkout_url" not in data:
        for match in re.finditer(
            r"https?://[^\s)<>]+",
            text,
            re.IGNORECASE,
        ):
            url = match.group(0).rstrip(".,;)")

            if re.search(
                r"(?:checkout|payment|pay|order)",
                url,
                re.IGNORECASE,
            ):
                data["checkout_url"] = url
                break

    for label in (
        "total",
        "grand total",
        "order total",
        "amount",
        "amount due",
    ):
        value = metadata.get(label)

        if not value:
            continue

        amount = re.search(
            r"(?:LKR|Rs\.?|₨)\s*([\d,]+(?:\.\d+)?)",
            value,
            re.IGNORECASE,
        )

        if amount:
            data["total"] = float(
                amount.group(1).replace(",", "")
            )
            break

    if "total" not in data:
        amount = re.search(
            r"(?:grand\s+total|order\s+total|amount\s+due|total)"
            r"\s*[:\-]?\s*(?:LKR|Rs\.?|₨)\s*"
            r"([\d,]+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )

        if amount:
            data["total"] = float(
                amount.group(1).replace(",", "")
            )

    for label in (
        "price lock expires at",
        "expires at",
        "checkout expires at",
        "expiry",
        "expires",
    ):
        value = metadata.get(label)

        if value:
            data["price_lock_expires_at"] = value.strip("` ")
            break

    return data


def parse_track_order_markdown(markdown: str) -> dict:
    """Parse common Kapruka order-tracking response formats."""
    stripped = markdown.strip()

    error_match = re.match(
        r"Error \((?P<code>[^)]+)\):\s*(?P<message>.+)",
        stripped,
        re.IGNORECASE,
    )

    if error_match:
        return {
            "error_code": error_match.group("code"),
            "message": error_match.group("message").strip(),
            "raw_markdown": markdown,
        }

    data: dict[str, Any] = {
        "raw_markdown": markdown
    }

    lines = [
        line.strip()
        for line in markdown.splitlines()
        if line.strip()
    ]

    field_aliases = {
        "status": "status",
        "order status": "status",
        "recipient": "recipient",
        "recipient name": "recipient",
        "customer": "recipient",
        "message": "message",
        "order message": "message",
    }

    for line in lines:
        field_match = re.match(
            r"(?:[-*]\s*)?(?:\*\*)?([^:*|]+?)(?:\*\*)?:\s*(.+)$",
            line,
        )

        if not field_match:
            continue

        label = re.sub(
            r"[*_`]",
            "",
            field_match.group(1),
        ).strip().lower()

        value = re.sub(
            r"[`*]",
            "",
            field_match.group(2),
        ).strip()

        key = field_aliases.get(label)

        if key:
            data[key] = value

    if "status" not in data:
        status_match = re.search(
            r"(?im)^#+\s*(?:order\s+)?status\s*[:\-]\s*(.+)$",
            markdown,
        )

        if status_match:
            data["status"] = status_match.group(1).strip()

    items: list[dict] = []
    timeline: list[dict] = []
    section: str | None = None
    table_headers: list[str] | None = None

    for line in lines:
        heading = re.match(
            r"^#{1,6}\s+(.+)$",
            line,
        )

        if heading:
            title = heading.group(1).strip().lower()

            if "item" in title or "product" in title:
                section = "items"
            elif (
                "timeline" in title
                or "progress" in title
                or "history" in title
                or "tracking" in title
            ):
                section = "timeline"
            else:
                section = None

            table_headers = None
            continue

        if not line.startswith("|"):
            if (
                section == "timeline"
                and line.startswith(("-", "*", "•"))
            ):
                text = re.sub(
                    r"^[-*•]\s*",
                    "",
                    line,
                ).strip()

                if text:
                    timeline.append({"event": text})

            elif (
                section == "items"
                and line.startswith(("-", "*", "•"))
            ):
                text = re.sub(
                    r"^[-*•]\s*",
                    "",
                    line,
                ).strip()

                if text:
                    items.append({"item": text})

            continue

        cells = [
            cell.strip()
            for cell in line.strip("|").split("|")
        ]

        if not any(cells):
            continue

        if all(
            re.fullmatch(r":?-{3,}:?", cell)
            for cell in cells
        ):
            continue

        if table_headers is None:
            table_headers = [
                re.sub(
                    r"[*_`]",
                    "",
                    cell,
                ).strip().lower().replace(" ", "_")
                for cell in cells
            ]
            continue

        row = {
            (
                table_headers[index]
                if index < len(table_headers)
                and table_headers[index]
                else f"column_{index + 1}"
            ): cells[index]
            for index in range(len(cells))
        }

        if section == "items":
            items.append(row)
        elif section == "timeline":
            timeline.append(row)

    if items:
        data["items"] = items

    if timeline:
        data["timeline"] = timeline

    if not timeline:
        for line in lines:
            match = re.match(
                r"^[-*•]\s+(.+?)"
                r"(?:\s+[—-]\s+"
                r"(\d{4}-\d{2}-\d{2}(?:[^ ]*)?))?$",
                line,
            )

            if match and any(
                word in match.group(1).lower()
                for word in (
                    "processing",
                    "packed",
                    "shipped",
                    "delivered",
                    "out for delivery",
                    "cancelled",
                )
            ):
                entry = {
                    "event": match.group(1).strip()
                }

                if match.group(2):
                    entry["date"] = match.group(2)

                timeline.append(entry)

        if timeline:
            data["timeline"] = timeline

    if "status" not in data and timeline:
        data["status"] = str(
            timeline[-1].get("event")
            or timeline[-1].get("status")
            or "unknown"
        )

    return data


def extract_markdown_result(
    data: dict | None,
) -> str | None:
    """
    Extract Kapruka's textual tool result.

    Some responses use `result`; the MCP client can also preserve text in
    `raw_markdown`.
    """
    if not data:
        return None

    for key in ("result", "raw_markdown"):
        value = data.get(key)

        if isinstance(value, str):
            return value

    return None