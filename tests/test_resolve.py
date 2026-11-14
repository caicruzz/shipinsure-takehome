"""``resolve()`` as a pure function over hand-built snapshots (TDD §4)."""

from __future__ import annotations

from datetime import UTC, datetime

from chalicelib.records import RechargeOrder, ShopifyOrder, Subscription
from chalicelib.resolve import CoverageStatus, Reason, Snapshot, resolve, resolve_all

SHOP = "demo-coffee.myshopify.com"
T0 = datetime(2026, 8, 1, 14, tzinfo=UTC)
T1 = datetime(2026, 9, 1, 14, tzinfo=UTC)


def order(order_id, premium=None, created=T0, cancelled=None):
    return ShopifyOrder(SHOP, order_id, premium, "USD", created, cancelled, created)


def recharge(rid, typ, shopify_id, *subs):
    return RechargeOrder(rid, typ, shopify_id, tuple(subs), "success", T0)


def sub(sid, cancelled=None):
    return Subscription(sid, "cancelled" if cancelled else "active", cancelled, cancelled or T0)


def snap(orders=(), recharge_orders=(), subscriptions=()):
    return Snapshot.build(orders, recharge_orders, subscriptions)


LINKED = dict(
    recharge_orders=[recharge("c1", "checkout", "O", "S"), recharge("r1", "recurring", "R", "S")],
    subscriptions=[sub("S")],
)


def test_direct_coverage_uses_line_item_premium():
    result = resolve("O", snap([order("O", premium=250)]))
    assert (result.status, result.premium_cents, result.currency) == (CoverageStatus.COVERED_DIRECT, 250, "USD")


def test_cancelled_order_is_not_covered_even_with_item():
    result = resolve("O", snap([order("O", premium=250, cancelled=T1)]))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.ORDER_CANCELLED)


def test_order_without_item_or_link_is_not_covered():
    result = resolve("X", snap([order("X")]))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.NO_SHIPINSURE_ITEM)


def test_renewal_inherits_original_premium():
    result = resolve("R", snap([order("O", premium=250), order("R", created=T1)], **LINKED))
    assert result.status == CoverageStatus.COVERED_INHERITED
    assert (result.premium_cents, result.subscription_id, result.origin_order_id) == (250, "S", "O")


def test_renewal_with_unknown_origin_is_pending():
    snapshot = snap([order("R", created=T1)], [recharge("r1", "recurring", "R", "S")])
    result = resolve("R", snapshot)
    assert (result.status, result.reason) == (CoverageStatus.PENDING, Reason.MISSING_SUBSCRIPTION_ORIGIN)


def test_renewal_whose_origin_shopify_order_is_unseen_is_pending():
    result = resolve("R", snap([order("R", created=T1)], **LINKED))
    assert (result.status, result.reason) == (CoverageStatus.PENDING, Reason.MISSING_ORIGIN_ORDER)


def test_recharge_order_pointing_at_unseen_shopify_order_is_pending():
    result = resolve("R", snap([], [recharge("r1", "recurring", "R", "S")]))
    assert (result.status, result.reason, result.shop) == (CoverageStatus.PENDING, Reason.MISSING_SHOPIFY_ORDER, None)


def test_subscription_cancelled_before_renewal_blocks_inheritance():
    linked = dict(LINKED, subscriptions=[sub("S", cancelled=T1.replace(day=1, hour=0))])
    result = resolve("R", snap([order("O", premium=250), order("R", created=T1)], **linked))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.SUBSCRIPTION_CANCELLED)


def test_subscription_cancelled_after_renewal_keeps_it_covered():
    linked = dict(LINKED, subscriptions=[sub("S", cancelled=T1.replace(day=2))])
    result = resolve("R", snap([order("O", premium=250), order("R", created=T1)], **linked))
    assert result.status == CoverageStatus.COVERED_INHERITED


def test_renewal_follows_current_state_of_original_no_freeze():
    result = resolve("R", snap([order("O", premium=None), order("R", created=T1)], **LINKED))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.ORIGIN_NOT_COVERED)


def test_cancelled_original_means_renewal_not_covered():
    result = resolve("R", snap([order("O", premium=250, cancelled=T0), order("R", created=T1)], **LINKED))
    assert result.reason == Reason.ORIGIN_NOT_COVERED


def test_renewal_with_several_subscriptions_prefers_covered():
    recharge_orders = [
        recharge("c1", "checkout", "O", "S1"),
        recharge("r1", "recurring", "R", "S0", "S1"),  # S0 has no known origin -> pending on its own
    ]
    result = resolve("R", snap([order("O", premium=250), order("R", created=T1)], recharge_orders))
    assert (result.status, result.subscription_id) == (CoverageStatus.COVERED_INHERITED, "S1")


def test_direct_item_wins_over_inheritance():
    result = resolve("R", snap([order("O", premium=250), order("R", premium=300, created=T1)], **LINKED))
    assert (result.status, result.premium_cents) == (CoverageStatus.COVERED_DIRECT, 300)


def test_resolve_all_includes_orders_known_only_from_recharge():
    snapshot = snap([order("O", premium=250)], [recharge("r1", "recurring", "R", "S")])
    assert [r.order_id for r in resolve_all(snapshot)] == ["O", "R"]
