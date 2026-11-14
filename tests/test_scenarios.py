"""TDD §7 scenarios 1, 2 and 4, plus decisions that change after billing (§9 Built #6)."""

from __future__ import annotations

from sqlalchemy import func, select

from chalicelib.models import EventInbox, EventStatus, LedgerEntry
from chalicelib.resolve import CoverageStatus, Reason
from chalicelib.scenarios import replay
from tests.helpers import (
    ORIGINAL,
    RENEWAL,
    RENEWAL_2,
    SUBSCRIPTION,
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
    assert coverage[RENEWAL].origin_order_id == ORIGINAL
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

    assert sum(not r.duplicate for r in results) == 5
    assert sum(r.duplicate for r in results) == 10
    with session_factory() as session:
        rows = list(session.scalars(select(EventInbox)))
        assert len(rows) == 5
        assert {row.delivery_count for row in rows} == {3}
        assert session.scalar(select(func.count()).select_from(LedgerEntry)) == 2
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


def test_scenario_4_missing_checkout_is_pending_then_charged_when_it_arrives(session_factory):
    *first, late_checkout = deliveries("missing_checkout")
    replay(session_factory, first)

    renewal = coverage_by_order(session_factory)[RENEWAL]
    assert renewal.status == CoverageStatus.PENDING
    assert renewal.reason == Reason.MISSING_SUBSCRIPTION_ORIGIN
    assert balance_cents(session_factory) == {ORIGINAL: 250}

    replay(session_factory, [late_checkout])

    assert coverage_by_order(session_factory)[RENEWAL].status == CoverageStatus.COVERED_INHERITED
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


def test_out_of_order_walks_through_each_pending_reason(session_factory):
    steps = deliveries("out_of_order")  # recurring RO, R, S, checkout RO, O
    seen = []
    for delivery in steps:
        replay(session_factory, [delivery])
        seen.append(coverage_by_order(session_factory)[RENEWAL].reason)

    assert seen == [
        Reason.MISSING_SHOPIFY_ORDER,
        Reason.MISSING_SUBSCRIPTION_ORIGIN,
        Reason.MISSING_SUBSCRIPTION_ORIGIN,
        Reason.MISSING_ORIGIN_ORDER,
        None,
    ]
    assert balance_cents(session_factory) == {ORIGINAL: 250, RENEWAL: 250}


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
