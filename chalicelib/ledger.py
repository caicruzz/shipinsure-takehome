"""Billing ledger posted as a diff (TDD §3 step 4).

For each order: ``delta = should_be_billed - already_posted``. A non-zero delta
is appended as a charge (positive) or adjustment (negative). Posting is a single
``INSERT ... SELECT ... ON CONFLICT DO NOTHING`` statement per order, so the
read of the posted balance and the write are atomic and a replayed event can
never post twice (``UNIQUE(shop, order_id, source_event_id)``).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Integer, String, bindparam, case, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from chalicelib.models import LedgerEntry, LedgerEntryType, UTCDateTime
from chalicelib.resolve import Resolution
from chalicelib.timeutil import utcnow


def basis_for(resolution: Resolution) -> str:
    if resolution.status.is_covered:
        return resolution.status.value
    return f"reversal:{resolution.reason.value if resolution.reason else resolution.status.value}"


def _build_post_diff_statement():
    """One reusable ``INSERT ... SELECT ... ON CONFLICT DO NOTHING``; values are bound per order."""
    shop = bindparam("shop", type_=String)
    order_id = bindparam("order_id", type_=String)
    posted = (
        select(
            func.coalesce(func.sum(LedgerEntry.amount_cents), 0).label("cents"),
            func.max(LedgerEntry.currency).label("currency"),
        )
        .where(LedgerEntry.shop == shop, LedgerEntry.order_id == order_id)
        .subquery()
    )
    delta = bindparam("desired_cents", type_=Integer) - posted.c.cents
    diff = select(
        shop,
        order_id,
        case((delta > 0, LedgerEntryType.CHARGE.value), else_=LedgerEntryType.ADJUSTMENT.value),
        delta,
        # A reversal has no desired currency, so it reuses the currency already posted.
        func.coalesce(bindparam("desired_currency", type_=String), posted.c.currency),
        bindparam("basis", type_=String),
        bindparam("subscription_id", type_=String),
        bindparam("source_event_id", type_=Integer),
        bindparam("created_at", type_=UTCDateTime),
    ).where(delta != 0)
    return (
        sqlite_insert(LedgerEntry.__table__)  # Core table: a plain statement, not an ORM bulk insert
        .from_select(
            [
                "shop",
                "order_id",
                "entry_type",
                "amount_cents",
                "currency",
                "basis",
                "subscription_id",
                "source_event_id",
                "created_at",
            ],
            diff,
        )
        .on_conflict_do_nothing(index_elements=["shop", "order_id", "source_event_id"])
    )


_POST_DIFF = _build_post_diff_statement()


def post_diff(session: Session, resolution: Resolution, source_event_id: int) -> None:
    if resolution.shop is None:
        return  # no Shopify order yet, so nothing can have been billed

    covered = resolution.status.is_covered
    session.execute(
        _POST_DIFF,
        {
            "shop": resolution.shop,
            "order_id": resolution.order_id,
            "desired_cents": resolution.premium_cents if covered else 0,
            "desired_currency": resolution.currency if covered else None,
            "basis": basis_for(resolution),
            "subscription_id": resolution.subscription_id,
            "source_event_id": source_event_id,
            "created_at": utcnow(),
        },
    )


def post_all(session: Session, resolutions: Iterable[Resolution], source_event_id: int) -> None:
    for resolution in resolutions:
        post_diff(session, resolution, source_event_id)
