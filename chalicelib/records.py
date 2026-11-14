"""Normalized, source-independent records produced by the adapters.

These are the only shapes the state layer and ``resolve()`` understand; raw
webhook payloads never leave the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RechargeOrderType(StrEnum):
    CHECKOUT = "checkout"
    RECURRING = "recurring"


@dataclass(frozen=True)
class ShopifyOrder:
    shop: str
    order_id: str
    premium_cents: int | None  # None when the order has no ShipInsure line item
    currency: str
    created_at: datetime
    cancelled_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class RechargeOrder:
    recharge_order_id: str
    type: str
    shopify_order_id: str | None  # external_order_id.ecommerce
    subscription_ids: tuple[str, ...]  # line_items[].purchase_item_id where type = subscription
    status: str
    updated_at: datetime


@dataclass(frozen=True)
class Subscription:
    subscription_id: str
    status: str
    cancelled_at: datetime | None
    updated_at: datetime


StateRecord = ShopifyOrder | RechargeOrder | Subscription
