"""TDD §7 scenarios replayed from fixtures (scenario 3, the permutations, is in test_permutations.py)."""

from __future__ import annotations

from sqlalchemy import func, select

from chalicelib.models import EventInbox, EventStatus, LedgerEntry
from chalicelib.resolve import CoverageStatus, Reason
from chalicelib.scenarios import replay
from tests.helpers import (
    DUPLICATE_PURCHASE,
    ORIGINAL,
    ORIGINAL_2,
    RENEWAL,
    RENEWAL_2,
    RENEWAL_4,
    SUBSCRIPTION,
    SUBSCRIPTION_2,
    balance_cents,
    balance_rows,
    coverage_by_order,
    deliveries,
)


def _ledger(sf) -> list[LedgerEntry]:
    with sf() as session:
        return list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.id)))


def test_scenario_1_happy_path_charges_direct_and_inherited(session_factory):
    results = replay(session_factory, deliveries("happy_path"))

    assert all(r.status == EventStatus.PROCESSED and not r.duplicate for r in results)
    coverage = coverage_by_order(session_factory)
    assert coverage[ORIGINAL].status == CoverageStatus.COVERED_DIRECT
    assert coverage[RENEWAL].status == CoverageStatus.COVERED_INHERITED
    assert coverage[RENEWAL].subscription_id == SUBSCRIPTION
    assert coverage[RENEWAL].origin_order_id == ORIGINAL  # found by customer + variant + time
    assert not [r for r in coverage.values() if r.status == CoverageStatus.PENDING]

    entries = _ledger(session_factory)
    assert [(e.order_id, e.entry_type, e.amount_cents, e.basis) for e in entries] == [
        (ORIGINAL, "charge", 250, "covered_direct"),
        (RENEWAL, "charge", 250, "covered_inherited"),
    ]
    assert entries[1].subscription_id == SUBSCRIPTION
    assert {b.currency for b in balance_rows(session_factory).values()} == {"USD"}


def test_ledger_rows_trace_back_to_the_raw_event_that_caused_them(session_factory):
    replay(session_factory, deliveries("happy_path"))
    with session_factory() as session:
        for entry in _ledger(session_factory):
            event = session.get_one(EventInbox, entry.source_event_id)
            assert event.body  # the raw webhook body is there to inspect


def test_scenario_2_every_delivery_three_times_is_deduplicated(session_factory):
    results = replay(session_factory, deliveries("duplicates"))

    assert sum(not r.duplicate for r in results) == 4
    assert sum(r.duplicate for r in results) == 8
    with session_factory() as session:
        rows = list(session.scalars(select(EventInbox)))
        assert len(rows) == 4
        assert {row.delivery_count for row in rows} == {3}
        assert session.scalar(select(func.count()).select_from(LedgerEntry)) == 2
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


def test_out_of_order_walks_through_each_pending_reason(session_factory):
    seen = []
    for delivery in deliveries("out_of_order"):  # recurring Recharge order, R, S, O
        replay(session_factory, [delivery])
        seen.append(coverage_by_order(session_factory)[RENEWAL].reason)

    assert seen == [
        Reason.MISSING_SHOPIFY_ORDER,
        Reason.MISSING_SUBSCRIPTION,
        Reason.MISSING_SUBSCRIPTION_ORIGIN,
        None,
    ]
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


def test_scenario_4_original_order_last_is_pending_then_charged(session_factory):
    *first, original = deliveries("original_order_last")
    replay(session_factory, first)

    renewal = coverage_by_order(session_factory)[RENEWAL]
    assert (renewal.status, renewal.reason) == (CoverageStatus.PENDING, Reason.MISSING_SUBSCRIPTION_ORIGIN)
    assert balance_cents(session_factory) == {}

    replay(session_factory, [original])

    assert coverage_by_order(session_factory)[RENEWAL].status == CoverageStatus.COVERED_INHERITED
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


def test_scenario_5_two_qualifying_orders_leave_renewal_pending_and_unbilled(session_factory):
    replay(session_factory, deliveries("ambiguous_origin"))

    coverage = coverage_by_order(session_factory)
    assert (coverage[RENEWAL].status, coverage[RENEWAL].reason) == (
        CoverageStatus.PENDING,
        Reason.AMBIGUOUS_SUBSCRIPTION_ORIGIN,
    )
    assert coverage[DUPLICATE_PURCHASE].reason == Reason.NO_SHIPINSURE_ITEM
    assert balance_cents(session_factory) == {ORIGINAL: 250}


def test_ambiguity_that_appears_after_billing_reverses_the_charge(session_factory):
    steps = {d.label: d for d in deliveries("ambiguous_origin")}
    duplicate = steps.pop("second order, same customer + variant")
    replay(session_factory, list(steps.values()))
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}

    replay(session_factory, [duplicate])

    assert coverage_by_order(session_factory)[RENEWAL].reason == Reason.AMBIGUOUS_SUBSCRIPTION_ORIGIN
    last = _ledger(session_factory)[-1]
    assert (last.order_id, last.amount_cents, last.basis) == (
        RENEWAL,
        -250,
        "reversal:ambiguous_subscription_origin",
    )
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 0}


def test_scenario_6_earlier_renewal_is_not_mistaken_for_an_original_order(session_factory):
    replay(session_factory, deliveries("renewal_not_origin"))

    renewal_4 = coverage_by_order(session_factory)[RENEWAL_4]
    assert renewal_4.status == CoverageStatus.COVERED_INHERITED
    assert (renewal_4.subscription_id, renewal_4.origin_order_id) == (SUBSCRIPTION_2, ORIGINAL_2)
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250, ORIGINAL_2: 250, RENEWAL_4: 250}


def test_renewal_cancelled_after_billing_posts_negative_adjustment(session_factory):
    replay(session_factory, deliveries("late_cancellation"))

    assert coverage_by_order(session_factory)[RENEWAL].reason == Reason.ORDER_CANCELLED
    last = _ledger(session_factory)[-1]
    assert (last.order_id, last.entry_type, last.amount_cents, last.basis) == (
        RENEWAL,
        "adjustment",
        -250,
        "reversal:order_cancelled",
    )
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 0}


def test_shipinsure_item_removed_from_original_reverses_original_and_renewal(session_factory):
    replay(session_factory, deliveries("item_removed"))

    coverage = coverage_by_order(session_factory)
    assert coverage[ORIGINAL].reason == Reason.NO_SHIPINSURE_ITEM
    assert coverage[RENEWAL].reason == Reason.ORIGIN_NOT_COVERED
    assert balance_cents(session_factory) == {ORIGINAL: 0, RENEWAL: 0}
    # Append-only: the original charges are still there, offset by adjustments.
    assert [e.amount_cents for e in _ledger(session_factory)] == [250, 250, -250, -250]


def test_subscription_cancelled_keeps_earlier_renewal_and_skips_later_one(session_factory):
    replay(session_factory, deliveries("subscription_cancelled"))

    coverage = coverage_by_order(session_factory)
    assert coverage[RENEWAL].status == CoverageStatus.COVERED_INHERITED  # placed before cancellation
    assert coverage[RENEWAL_2].status == CoverageStatus.NOT_COVERED
    assert coverage[RENEWAL_2].reason == Reason.SUBSCRIPTION_CANCELLED
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}
