"""``resolve()`` as a pure function over hand-built snapshots (TDD §4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chalicelib.records import RechargeOrder, ShopifyOrder, Subscription
from chalicelib.resolve import CoverageStatus, Reason, Snapshot, resolve, resolve_all

SHOP = "demo-coffee.myshopify.com"
CUSTOMER = "C1"
VARIANT = "V1"
T0 = datetime(2026, 8, 1, 14, tzinfo=UTC)  # original purchase
T_SUB = T0 + timedelta(seconds=5)  # Recharge creates the subscription right after
T1 = datetime(2026, 9, 1, 14, tzinfo=UTC)  # renewal


def order(order_id, premium=None, created=T0, cancelled=None, customer=CUSTOMER, variants=(VARIANT,), shop=SHOP):
    return ShopifyOrder(shop, order_id, customer, tuple(variants), premium, "USD", created, cancelled, created)


def renewal(order_id="R", created=T1, **kwargs):
    return order(order_id, created=created, **kwargs)


def recurring(rid, shopify_id, *subs, customer=CUSTOMER):
    return RechargeOrder(rid, "recurring", shopify_id, customer, tuple(subs), "success", T1)


def sub(sid="S", created=T_SUB, cancelled=None, variant=VARIANT):
    status = "cancelled" if cancelled else "active"
    return Subscription(sid, "rc-1", variant, status, created, cancelled, cancelled or created)


def snap(orders=(), recharge_orders=(), subscriptions=()):
    return Snapshot.build(orders, recharge_orders, subscriptions)


def linked(*orders, subscriptions=None, recharge_orders=None):
    """O and R linked the normal way: subscription S plus a recurring order R -> S."""
    return snap(
        orders,
        recharge_orders if recharge_orders is not None else [recurring("r1", "R", "S")],
        subscriptions if subscriptions is not None else [sub()],
    )


# --- direct coverage -------------------------------------------------------


def test_direct_coverage_uses_line_item_premium():
    result = resolve("O", snap([order("O", premium=250)]))
    assert (result.status, result.premium_cents, result.currency) == (CoverageStatus.COVERED_DIRECT, 250, "USD")


def test_cancelled_order_is_not_covered_even_with_item():
    result = resolve("O", snap([order("O", premium=250, cancelled=T1)]))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.ORDER_CANCELLED)


def test_order_without_item_or_link_is_not_covered():
    result = resolve("X", snap([order("X")]))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.NO_SHIPINSURE_ITEM)


def test_direct_item_wins_over_inheritance():
    result = resolve("R", linked(order("O", premium=250), renewal(premium=300)))
    assert (result.status, result.premium_cents) == (CoverageStatus.COVERED_DIRECT, 300)


# --- inherited coverage through the origin match ---------------------------


def test_renewal_inherits_from_the_matched_original_order():
    result = resolve("R", linked(order("O", premium=250), renewal()))
    assert result.status == CoverageStatus.COVERED_INHERITED
    assert (result.premium_cents, result.subscription_id, result.origin_order_id) == (250, "S", "O")


def test_checkout_type_recharge_orders_are_ignored():
    # Legacy Recharge checkout orders are out of scope (TDD §9): they neither link nor match.
    checkout = RechargeOrder("c1", "checkout", "O", CUSTOMER, ("S",), "success", T0)
    result = resolve("R", linked(order("O", premium=250), renewal(), recharge_orders=[checkout]))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.NO_SHIPINSURE_ITEM)


def test_unknown_subscription_is_pending():
    result = resolve("R", linked(order("O", premium=250), renewal(), subscriptions=[]))
    assert (result.status, result.reason) == (CoverageStatus.PENDING, Reason.MISSING_SUBSCRIPTION)


def test_no_candidate_order_is_pending():
    result = resolve("R", linked(renewal()))
    assert (result.status, result.reason) == (CoverageStatus.PENDING, Reason.MISSING_SUBSCRIPTION_ORIGIN)


def test_two_candidate_orders_are_ambiguous_and_never_billed():
    result = resolve(
        "R", linked(order("O", premium=250), order("O-dup", created=T0 - timedelta(minutes=20)), renewal())
    )
    assert (result.status, result.reason) == (CoverageStatus.PENDING, Reason.AMBIGUOUS_SUBSCRIPTION_ORIGIN)
    assert result.premium_cents is None


def test_recharge_order_pointing_at_unseen_shopify_order_is_pending():
    result = resolve("R", snap([], [recurring("r1", "R", "S")], [sub()]))
    assert (result.status, result.reason, result.shop) == (CoverageStatus.PENDING, Reason.MISSING_SHOPIFY_ORDER, None)


def test_recurring_order_without_shopify_customer_cannot_match():
    result = resolve(
        "R", linked(order("O", premium=250), renewal(), recharge_orders=[recurring("r1", "R", "S", customer=None)])
    )
    assert result.reason == Reason.MISSING_SUBSCRIPTION_ORIGIN


# --- what does and doesn't count as a candidate ----------------------------


def test_order_outside_the_window_is_not_a_candidate():
    old = order("O", premium=250, created=T_SUB - timedelta(hours=2))
    assert resolve("R", linked(old, renewal())).reason == Reason.MISSING_SUBSCRIPTION_ORIGIN
    assert resolve("R", linked(old, renewal()), window=timedelta(hours=3)).status == CoverageStatus.COVERED_INHERITED


def test_order_placed_after_the_subscription_is_not_a_candidate():
    late = order("O", premium=250, created=T_SUB + timedelta(seconds=1))
    assert resolve("R", linked(late, renewal())).reason == Reason.MISSING_SUBSCRIPTION_ORIGIN


def test_other_customer_variant_or_shop_is_not_a_candidate():
    for other in (
        order("O", premium=250, customer="C2"),
        order("O", premium=250, variants=("V2",)),
        order("O", premium=250, shop="other.myshopify.com"),
    ):
        assert resolve("R", linked(other, renewal())).reason == Reason.MISSING_SUBSCRIPTION_ORIGIN


def test_a_renewal_is_never_another_subscriptions_original_order():
    # S2 is bought 25 minutes after renewal R of S, same customer and variant. R must not count.
    t_o2 = T1 + timedelta(minutes=25)
    snapshot = snap(
        [
            order("O", premium=250),
            renewal("R"),
            order("O2", premium=300, created=t_o2),
            renewal("R4", created=T1 + timedelta(days=30)),
        ],
        [recurring("r1", "R", "S"), recurring("r4", "R4", "S2")],
        [sub("S"), sub("S2", created=t_o2 + timedelta(seconds=5))],
    )
    result = resolve("R4", snapshot)
    assert (result.status, result.origin_order_id, result.premium_cents) == (
        CoverageStatus.COVERED_INHERITED,
        "O2",
        300,
    )


# --- cancellation and coverage that follows the original order -------------


def test_subscription_cancelled_before_renewal_blocks_inheritance():
    cancelled = [sub(cancelled=T1 - timedelta(days=1))]
    result = resolve("R", linked(order("O", premium=250), renewal(), subscriptions=cancelled))
    assert (result.status, result.reason) == (CoverageStatus.NOT_COVERED, Reason.SUBSCRIPTION_CANCELLED)


def test_subscription_cancelled_after_renewal_keeps_it_covered():
    cancelled = [sub(cancelled=T1 + timedelta(days=1))]
    result = resolve("R", linked(order("O", premium=250), renewal(), subscriptions=cancelled))
    assert result.status == CoverageStatus.COVERED_INHERITED


def test_renewal_follows_current_state_of_original_no_freeze():
    result = resolve("R", linked(order("O", premium=None), renewal()))
    assert (result.status, result.reason, result.origin_order_id) == (
        CoverageStatus.NOT_COVERED,
        Reason.ORIGIN_NOT_COVERED,
        "O",
    )


def test_cancelled_original_means_renewal_not_covered():
    result = resolve("R", linked(order("O", premium=250, cancelled=T0 + timedelta(days=1)), renewal()))
    assert result.reason == Reason.ORIGIN_NOT_COVERED


def test_renewal_with_several_subscriptions_prefers_covered():
    snapshot = snap(
        [order("O", premium=250), renewal()],
        [recurring("r1", "R", "S0", "S1")],
        [sub("S1")],  # S0 unknown -> pending on its own
    )
    result = resolve("R", snapshot)
    assert (result.status, result.subscription_id) == (CoverageStatus.COVERED_INHERITED, "S1")


def test_resolve_all_includes_orders_known_only_from_recharge():
    snapshot = snap([order("O", premium=250)], [recurring("r1", "R", "S")], [sub()])
    assert [r.order_id for r in resolve_all(snapshot)] == ["O", "R"]
