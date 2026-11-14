"""Coverage rules (TDD §4). Pure functions over a snapshot of stored state.

``resolve_all`` never looks at "what just happened": its output depends only on
which events have been stored, never on the order they arrived in.

Linkage (TDD §1):

    Shopify order O  <-ecommerce-  Recharge checkout order  -purchase_item_id->  Subscription S
    Shopify order R  <-ecommerce-  Recharge recurring order -purchase_item_id->  S

R inherits coverage from O, S's original order.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from chalicelib.records import RechargeOrder, RechargeOrderType, ShopifyOrder, Subscription


class CoverageStatus(StrEnum):
    COVERED_DIRECT = "covered_direct"
    COVERED_INHERITED = "covered_inherited"
    PENDING = "pending"
    NOT_COVERED = "not_covered"

    @property
    def is_covered(self) -> bool:
        return self in (CoverageStatus.COVERED_DIRECT, CoverageStatus.COVERED_INHERITED)


class Reason(StrEnum):
    # pending: a link is missing, will resolve automatically once it arrives
    MISSING_SHOPIFY_ORDER = "missing_shopify_order"
    MISSING_SUBSCRIPTION_ORIGIN = "missing_subscription_origin"
    MISSING_ORIGIN_ORDER = "missing_origin_order"
    # not covered
    ORDER_CANCELLED = "order_cancelled"
    NO_SHIPINSURE_ITEM = "no_shipinsure_item"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    ORIGIN_NOT_COVERED = "origin_not_covered"


@dataclass(frozen=True)
class Resolution:
    order_id: str
    shop: str | None  # None when the Shopify order has not been seen
    status: CoverageStatus
    premium_cents: int | None = None
    currency: str | None = None
    reason: Reason | None = None
    subscription_id: str | None = None
    origin_order_id: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """Stored state, indexed for the linkage walk."""

    shopify_orders: Mapping[str, ShopifyOrder]
    subscriptions: Mapping[str, Subscription]
    # Shopify order id -> subscription ids, from recurring Recharge orders
    renewal_links: Mapping[str, frozenset[str]]
    # subscription id -> Shopify order ids it originated from, from checkout Recharge orders
    origin_links: Mapping[str, frozenset[str]]
    # every Shopify order id a Recharge order points at
    referenced_order_ids: frozenset[str]

    @classmethod
    def build(
        cls,
        shopify_orders: Iterable[ShopifyOrder],
        recharge_orders: Iterable[RechargeOrder],
        subscriptions: Iterable[Subscription],
    ) -> Snapshot:
        renewal_links: dict[str, set[str]] = defaultdict(set)
        origin_links: dict[str, set[str]] = defaultdict(set)
        referenced: set[str] = set()
        for ro in recharge_orders:
            if ro.shopify_order_id is None:
                continue
            referenced.add(ro.shopify_order_id)
            if ro.type == RechargeOrderType.RECURRING:
                renewal_links[ro.shopify_order_id].update(ro.subscription_ids)
            elif ro.type == RechargeOrderType.CHECKOUT:
                for subscription_id in ro.subscription_ids:
                    origin_links[subscription_id].add(ro.shopify_order_id)
        # Shopify order ids are globally unique, so state is joined on order_id alone.
        return cls(
            shopify_orders={o.order_id: o for o in shopify_orders},
            subscriptions={s.subscription_id: s for s in subscriptions},
            renewal_links={k: frozenset(v) for k, v in renewal_links.items()},
            origin_links={k: frozenset(v) for k, v in origin_links.items()},
            referenced_order_ids=frozenset(referenced),
        )


def _inherit_via(order: ShopifyOrder, subscription_id: str, snapshot: Snapshot) -> Resolution:
    base = Resolution(order.order_id, order.shop, CoverageStatus.PENDING, subscription_id=subscription_id)

    # Decisive on its own: no origin lookup can make this order covered.
    subscription = snapshot.subscriptions.get(subscription_id)
    if subscription and subscription.cancelled_at and subscription.cancelled_at < order.created_at:
        return replace(base, status=CoverageStatus.NOT_COVERED, reason=Reason.SUBSCRIPTION_CANCELLED)

    origins = snapshot.origin_links.get(subscription_id)
    if not origins:
        return replace(base, reason=Reason.MISSING_SUBSCRIPTION_ORIGIN)
    # A subscription has one original order; min() keeps this deterministic regardless.
    origin_id = min(origins)
    origin = snapshot.shopify_orders.get(origin_id)
    if origin is None:
        return replace(base, reason=Reason.MISSING_ORIGIN_ORDER, origin_order_id=origin_id)

    if origin.cancelled_at is not None or origin.premium_cents is None:
        return replace(
            base, status=CoverageStatus.NOT_COVERED, reason=Reason.ORIGIN_NOT_COVERED, origin_order_id=origin_id
        )

    return replace(
        base,
        status=CoverageStatus.COVERED_INHERITED,
        premium_cents=origin.premium_cents,
        currency=origin.currency,
        origin_order_id=origin_id,
    )


_STATUS_PRIORITY = {
    CoverageStatus.COVERED_INHERITED: 0,
    CoverageStatus.PENDING: 1,
    CoverageStatus.NOT_COVERED: 2,
}


def resolve(order_id: str, snapshot: Snapshot) -> Resolution:
    order = snapshot.shopify_orders.get(order_id)
    if order is None:
        # A Recharge order points at a Shopify order we have not seen yet.
        return Resolution(order_id, None, CoverageStatus.PENDING, reason=Reason.MISSING_SHOPIFY_ORDER)

    if order.cancelled_at is not None:
        return Resolution(order_id, order.shop, CoverageStatus.NOT_COVERED, reason=Reason.ORDER_CANCELLED)

    if order.premium_cents is not None:
        return Resolution(
            order_id,
            order.shop,
            CoverageStatus.COVERED_DIRECT,
            premium_cents=order.premium_cents,
            currency=order.currency,
        )

    subscription_ids = snapshot.renewal_links.get(order_id)
    if not subscription_ids:
        return Resolution(order_id, order.shop, CoverageStatus.NOT_COVERED, reason=Reason.NO_SHIPINSURE_ITEM)

    # A renewal can carry several subscriptions: covered wins, then pending, then not covered.
    candidates = [_inherit_via(order, s, snapshot) for s in sorted(subscription_ids)]
    return min(candidates, key=lambda r: _STATUS_PRIORITY[r.status])


def resolve_all(snapshot: Snapshot) -> list[Resolution]:
    order_ids = set(snapshot.shopify_orders) | snapshot.referenced_order_ids
    return [resolve(order_id, snapshot) for order_id in sorted(order_ids)]
