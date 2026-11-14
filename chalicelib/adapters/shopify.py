"""Shopify order webhooks -> ``ShopifyOrder``.

Shopify webhooks deliver the REST Order resource (not the GraphQL Order), so
fields are the REST names: ``id``, ``line_items``, ``cancelled_at``,
``updated_at`` (GraphQL ``legacyResourceId``, ``lineItems``, ``cancelledAt``,
``updatedAt``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chalicelib import config
from chalicelib.adapters.base import UnsupportedPayload, required_timestamp
from chalicelib.money import to_minor_units
from chalicelib.records import ShopifyOrder
from chalicelib.timeutil import parse_timestamp

# Topics whose payload is the full Order resource.
ORDER_TOPICS = frozenset(
    {
        "orders/create",
        "orders/updated",
        "orders/cancelled",
        "orders/paid",
        "orders/fulfilled",
        "orders/partially_fulfilled",
    }
)


def is_shipinsure_line_item(line_item: Mapping[str, Any]) -> bool:
    keyword = config.shipinsure_keyword()
    vendor = str(line_item.get("vendor") or "").lower()
    title = str(line_item.get("title") or "").lower()
    return keyword in vendor or keyword in title


def _current_quantity(line_item: Mapping[str, Any]) -> int:
    # After an order edit, a removed line item stays in ``line_items`` with
    # ``current_quantity`` 0; ``quantity`` keeps the originally ordered amount.
    if line_item.get("current_quantity") is not None:
        return int(line_item["current_quantity"])
    return int(line_item.get("quantity") or 0)


def shipinsure_premium_cents(order: Mapping[str, Any]) -> int | None:
    """Sum of ShipInsure line items still on the order, or None if there are none."""
    premiums = [
        to_minor_units(item["price"], quantity)
        for item in order.get("line_items") or []
        if is_shipinsure_line_item(item) and (quantity := _current_quantity(item)) > 0
    ]
    return sum(premiums) if premiums else None


def normalize(topic: str, shop: str, payload: Mapping[str, Any]) -> list[ShopifyOrder]:
    if topic not in ORDER_TOPICS:
        return []
    try:
        return [
            ShopifyOrder(
                shop=shop,
                order_id=str(payload["id"]),
                premium_cents=shipinsure_premium_cents(payload),
                currency=str(payload["currency"]),
                created_at=required_timestamp(payload, "created_at"),
                cancelled_at=parse_timestamp(payload.get("cancelled_at")),
                updated_at=required_timestamp(payload, "updated_at"),
            )
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedPayload(f"Shopify order payload is missing or has an invalid field: {exc}") from exc
