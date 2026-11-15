"""Read models for the terminal report and the tests: inbox, coverage, ledger, balances."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chalicelib import config, state
from chalicelib.models import EventInbox, LedgerEntry
from chalicelib.money import format_minor_units
from chalicelib.resolve import CoverageStatus, Resolution, resolve_all


@dataclass(frozen=True)
class Balance:
    shop: str
    order_id: str
    currency: str
    cents: int


def coverage(session: Session) -> list[Resolution]:
    return resolve_all(state.load_snapshot(session), config.subscription_origin_window())


def balances(session: Session) -> dict[str, Balance]:
    rows = session.execute(
        select(
            LedgerEntry.shop,
            LedgerEntry.order_id,
            LedgerEntry.currency,
            func.sum(LedgerEntry.amount_cents),
        ).group_by(LedgerEntry.shop, LedgerEntry.order_id, LedgerEntry.currency)
    )
    return {order_id: Balance(shop, order_id, currency, int(cents)) for shop, order_id, currency, cents in rows}


def inbox(session: Session) -> list[EventInbox]:
    return list(session.scalars(select(EventInbox).order_by(EventInbox.id)))


def ledger(session: Session) -> list[LedgerEntry]:
    return list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.id)))


def _table(title: str, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    cells = [[str(h) for h in headers]] + [["" if v is None else str(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    line = "  ".join("-" * w for w in widths)
    out = [f"\n{title}", line]
    out.append("  ".join(h.ljust(w) for h, w in zip(cells[0], widths, strict=True)))
    out.append(line)
    out.extend("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)) for row in cells[1:])
    if not rows:
        out.append("(none)")
    return "\n".join(out)


def render(session: Session) -> str:
    resolutions = coverage(session)
    pending = [r for r in resolutions if r.status == CoverageStatus.PENDING]
    sections = [
        _table(
            "EVENTS INBOX",
            ["id", "source", "topic", "dedupe_key", "deliveries", "status", "error"],
            [(e.id, e.source, e.topic, e.dedupe_key, e.delivery_count, e.status, e.error) for e in inbox(session)],
        ),
        _table(
            "COVERAGE",
            ["order_id", "status", "premium", "reason", "subscription", "origin_order"],
            [
                (
                    r.order_id,
                    r.status,
                    format_minor_units(r.premium_cents, r.currency),
                    r.reason,
                    r.subscription_id,
                    r.origin_order_id,
                )
                for r in resolutions
            ],
        ),
        _table(
            "PENDING",
            ["order_id", "reason", "subscription", "origin_order"],
            [(r.order_id, r.reason, r.subscription_id, r.origin_order_id) for r in pending],
        ),
        _table(
            "BILLING LEDGER",
            ["id", "order_id", "entry_type", "amount", "basis", "subscription", "source_event"],
            [
                (
                    e.id,
                    e.order_id,
                    e.entry_type,
                    format_minor_units(e.amount_cents, e.currency),
                    e.basis,
                    e.subscription_id,
                    e.source_event_id,
                )
                for e in ledger(session)
            ],
        ),
        _table(
            "BALANCES",
            ["order_id", "balance"],
            [(b.order_id, format_minor_units(b.cents, b.currency)) for b in balances(session).values()],
        ),
    ]
    return "\n".join(sections)
