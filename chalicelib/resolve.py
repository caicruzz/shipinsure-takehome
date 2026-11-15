"""Coverage rules (TDD §4). Pure functions over a snapshot of stored state.

``resolve_all`` never looks at "what just happened": its output depends only on
which events have been stored, never on the order they arrived in.

Linkage on Shopify-checkout stores (TDD §1-2):

    Shopify order O  <-matched: same customer, contains S's variant, placed shortly before S was created-
    Recharge Subscription S  <-purchase_item_id-  Recharge recurring order  -ecommerce->  Shopify order R

Recharge creates no order for the first purchase, and nothing ShipInsure can
read links S to O directly, so O is found by matching. Exactly one candidate
means R inherits O's coverage; none or several leaves R pending, never guessed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from chalicelib.config import DEFAULT_SUBSCRIPTION_ORIGIN_WINDOW_MINUTES
from chalicelib.records import RechargeOrder, RechargeOrderType, ShopifyOrder, Subscription

DEFAULT_ORIGIN_WINDOW = timedelta(minutes=DEFAULT_SUBSCRIPTION_ORIGIN_WINDOW_MINUTES)


class CoverageStatus(StrEnum):
    COVERED_DIRECT = "covered_direct"
    COVERED_INHERITED = "covered_inherited"
    PENDING = "pending"
    NOT_COVERED = "not_covered"

    @property
    def is_covered(self) -> bool:
        return self in (CoverageStatus.COVERED_DIRECT, CoverageStatus.COVERED_INHERITED)


class Reason(StrEnum):
    # pending: data is missing or ambiguous; resolves automatically as events arrive
    MISSING_SHOPIFY_ORDER = "missing_shopify_order"
    MISSING_SUBSCRIPTION = "missing_subscription"
    MISSING_SUBSCRIPTION_ORIGIN = "missing_subscription_origin"
    AMBIGUOUS_SUBSCRIPTION_ORIGIN = "ambiguous_subscription_origin"
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
    # Shopify renewal order id -> {subscription id: Shopify customer id}, from recurring Recharge orders
    renewal_links: Mapping[str, Mapping[str, str | None]]
    # Shopify customer id -> that customer's Shopify orders, the candidates for an origin match
    orders_by_customer: Mapping[str, tuple[ShopifyOrder, ...]]

    @classmethod
    def build(
        cls,
        shopify_orders: Iterable[ShopifyOrder],
        recharge_orders: Iterable[RechargeOrder],
        subscriptions: Iterable[Subscription],
    ) -> Snapshot:
        orders = {o.order_id: o for o in shopify_orders}  # Shopify order ids are globally unique

        renewal_links: dict[str, dict[str, str | None]] = defaultdict(dict)
        for ro in sorted(recharge_orders, key=lambda r: r.recharge_order_id):
            if ro.type != RechargeOrderType.RECURRING or ro.shopify_order_id is None:
                continue
            for subscription_id in ro.subscription_ids:
                links = renewal_links[ro.shopify_order_id]
                links[subscription_id] = links.get(subscription_id) or ro.shopify_customer_id

        by_customer: dict[str, list[ShopifyOrder]] = defaultdict(list)
        for order in orders.values():
            if order.customer_id is not None:
                by_customer[order.customer_id].append(order)

        return cls(
            shopify_orders=orders,
            subscriptions={s.subscription_id: s for s in subscriptions},
            renewal_links={k: dict(v) for k, v in renewal_links.items()},
            orders_by_customer={k: tuple(sorted(v, key=lambda o: o.order_id)) for k, v in by_customer.items()},
        )

    def origin_candidates(
        self, subscription: Subscription, customer_id: str, shop: str, window: timedelta
    ) -> list[ShopifyOrder]:
        """Orders that could be ``subscription``'s original order (TDD §4, origin match)."""
        if subscription.variant_id is None:
            return []
        earliest = subscription.created_at - window
        return [
            order
            for order in self.orders_by_customer.get(customer_id, ())
            if order.shop == shop
            and subscription.variant_id in order.variant_ids
            and earliest <= order.created_at <= subscription.created_at
            # A renewal of another subscription can look like a purchase of the same variant.
            and order.order_id not in self.renewal_links
        ]


def _inherit_via(
    order: ShopifyOrder, subscription_id: str, customer_id: str | None, snapshot: Snapshot, window: timedelta
) -> Resolution:
    pending = Resolution(order.order_id, order.shop, CoverageStatus.PENDING, subscription_id=subscription_id)

    subscription = snapshot.subscriptions.get(subscription_id)
    if subscription is None:
        return replace(pending, reason=Reason.MISSING_SUBSCRIPTION)

    # Decisive on its own: no origin can make this order covered.
    if subscription.cancelled_at and subscription.cancelled_at < order.created_at:
        return replace(pending, status=CoverageStatus.NOT_COVERED, reason=Reason.SUBSCRIPTION_CANCELLED)

    candidates = (
        snapshot.origin_candidates(subscription, customer_id, order.shop, window) if customer_id is not None else []
    )
    if not candidates:
        return replace(pending, reason=Reason.MISSING_SUBSCRIPTION_ORIGIN)
    if len(candidates) > 1:
        return replace(pending, reason=Reason.AMBIGUOUS_SUBSCRIPTION_ORIGIN)

    [origin] = candidates
    if origin.cancelled_at is not None or origin.premium_cents is None:
        return replace(
            pending,
            status=CoverageStatus.NOT_COVERED,
            reason=Reason.ORIGIN_NOT_COVERED,
            origin_order_id=origin.order_id,
        )
    return replace(
        pending,
        status=CoverageStatus.COVERED_INHERITED,
        premium_cents=origin.premium_cents,
        currency=origin.currency,
        origin_order_id=origin.order_id,
    )


_STATUS_PRIORITY = {
    CoverageStatus.COVERED_INHERITED: 0,
    CoverageStatus.PENDING: 1,
    CoverageStatus.NOT_COVERED: 2,
}


def resolve(order_id: str, snapshot: Snapshot, window: timedelta = DEFAULT_ORIGIN_WINDOW) -> Resolution:
    order = snapshot.shopify_orders.get(order_id)
    if order is None:
        # A recurring Recharge order points at a Shopify order we have not seen yet.
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

    links = snapshot.renewal_links.get(order_id)
    if not links:
        return Resolution(order_id, order.shop, CoverageStatus.NOT_COVERED, reason=Reason.NO_SHIPINSURE_ITEM)

    # A renewal can carry several subscriptions: covered wins, then pending, then not covered.
    candidates = [_inherit_via(order, s, links[s], snapshot, window) for s in sorted(links)]
    return min(candidates, key=lambda r: _STATUS_PRIORITY[r.status])


def resolve_all(snapshot: Snapshot, window: timedelta = DEFAULT_ORIGIN_WINDOW) -> list[Resolution]:
    order_ids = set(snapshot.shopify_orders) | set(snapshot.renewal_links)
    return [resolve(order_id, snapshot, window) for order_id in sorted(order_ids)]
