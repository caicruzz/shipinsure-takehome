"""SQLAlchemy 2.0 declarative models: the five tables from TDD §5.

Unique constraints live here so idempotency is enforced by the schema, not by
Python ``if`` checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC and returns them as aware UTC.

    SQLite has no timezone type. Normalising to UTC keeps the stored strings in a
    single fixed format, so the ``updated_at`` guard in the upserts compares
    chronologically.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime, dict[str, Any]: JSON, list[str]: JSON}


class EventSource(StrEnum):
    SHOPIFY = "shopify"
    RECHARGE = "recharge"


class EventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"  # persisted, but a topic the prototype does not act on
    FAILED = "failed"


class LedgerEntryType(StrEnum):
    CHARGE = "charge"
    ADJUSTMENT = "adjustment"


class EventInbox(Base):
    """Raw, append-only record of every webhook delivery."""

    __tablename__ = "events_inbox"
    __table_args__ = (UniqueConstraint("source", "dedupe_key", name="uq_events_inbox_source_dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(64))
    shop: Mapped[str | None] = mapped_column(String(255))
    headers: Mapped[dict[str, Any]]
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime]
    last_received_at: Mapped[datetime]
    delivery_count: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(16), default=EventStatus.RECEIVED)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None]


class ShopifyOrderRow(Base):
    """Latest known state of a Shopify order."""

    __tablename__ = "shopify_orders"

    shop: Mapped[str] = mapped_column(String(255), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    premium_cents: Mapped[int | None]
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class RechargeOrderRow(Base):
    """Latest known state of a Recharge order (checkout or recurring)."""

    __tablename__ = "recharge_orders"

    recharge_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    shopify_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    subscription_ids: Mapped[list[str]]
    status: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime]


class SubscriptionRow(Base):
    """Latest known state of a Recharge subscription."""

    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    cancelled_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class LedgerEntry(Base):
    """Append-only billing ledger. Rows are never updated or deleted.

    The balance of an order is ``SUM(amount_cents)``. Each processed event posts
    at most one row per order (the unique key), carrying the difference between
    what should be billed and what was already posted.
    """

    __tablename__ = "billing_ledger"
    __table_args__ = (UniqueConstraint("shop", "order_id", "source_event_id", name="uq_billing_ledger_order_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop: Mapped[str] = mapped_column(String(255))
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    entry_type: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    basis: Mapped[str] = mapped_column(String(64))
    subscription_id: Mapped[str | None] = mapped_column(String(64))
    source_event_id: Mapped[int] = mapped_column(ForeignKey("events_inbox.id"))
    created_at: Mapped[datetime]
