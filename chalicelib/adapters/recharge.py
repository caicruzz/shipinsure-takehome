"""Recharge 2021-11 webhooks -> ``RechargeOrder`` / ``Subscription``.

Recharge webhook bodies are the REST GET payload wrapped in the resource name,
e.g. ``{"order": {...}}`` or ``{"subscription": {...}}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chalicelib.adapters.base import UnsupportedPayload, required_timestamp
from chalicelib.records import RechargeOrder, Subscription
from chalicelib.timeutil import parse_timestamp

SUBSCRIPTION_PURCHASE_ITEM_TYPE = "subscription"

ORDER_TOPICS = frozenset({"order/created", "order/updated", "order/processed", "order/cancelled"})
SUBSCRIPTION_TOPICS = frozenset(
    {
        "subscription/created",
        "subscription/updated",
        "subscription/activated",
        "subscription/cancelled",
    }
)


def resource_for_topic(topic: str) -> str:
    """``order/created`` -> ``order``: the key the object is wrapped in."""
    return topic.split("/", 1)[0]


def _ecommerce_id(external: Mapping[str, Any] | None) -> str | None:
    """Recharge wraps platform ids as ``{"ecommerce": "123"}``."""
    value = (external or {}).get("ecommerce")
    return str(value) if value else None


def _normalize_order(order: Mapping[str, Any]) -> RechargeOrder:
    ecommerce_id = _ecommerce_id(order.get("external_order_id"))
    customer = order.get("customer") or {}
    subscription_ids = sorted(
        {
            str(item["purchase_item_id"])
            for item in order.get("line_items") or []
            if item.get("purchase_item_type") == SUBSCRIPTION_PURCHASE_ITEM_TYPE
            and item.get("purchase_item_id") is not None
        }
    )
    return RechargeOrder(
        recharge_order_id=str(order["id"]),
        type=str(order["type"]),
        shopify_order_id=ecommerce_id,
        shopify_customer_id=_ecommerce_id(customer.get("external_customer_id")),
        subscription_ids=tuple(subscription_ids),
        status=str(order["status"]),
        updated_at=required_timestamp(order, "updated_at"),
    )


def _normalize_subscription(subscription: Mapping[str, Any]) -> Subscription:
    return Subscription(
        subscription_id=str(subscription["id"]),
        customer_id=str(subscription["customer_id"]) if subscription.get("customer_id") is not None else None,
        variant_id=_ecommerce_id(subscription.get("external_variant_id")),
        status=str(subscription["status"]),
        created_at=required_timestamp(subscription, "created_at"),
        cancelled_at=parse_timestamp(subscription.get("cancelled_at")),
        updated_at=required_timestamp(subscription, "updated_at"),
    )


class RechargePlatform:
    """``SubscriptionPlatform`` implementation for Recharge."""

    def normalize(self, topic: str, payload: Mapping[str, Any]) -> list[RechargeOrder | Subscription]:
        try:
            if topic in ORDER_TOPICS:
                return [_normalize_order(payload["order"])]
            if topic in SUBSCRIPTION_TOPICS:
                return [_normalize_subscription(payload["subscription"])]
        except (KeyError, TypeError, ValueError) as exc:
            raise UnsupportedPayload(f"Recharge {topic} payload is missing or has an invalid field: {exc}") from exc
        return []
