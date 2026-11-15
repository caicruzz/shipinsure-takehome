"""Latest-state tables: guarded upserts and the snapshot read for ``resolve()``.

Each upsert is one atomic ``INSERT ... ON CONFLICT DO UPDATE ... WHERE
existing.updated_at < excluded.updated_at`` statement, so a stale event never
regresses state and there is no read-then-write in Python.
"""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from chalicelib.models import Base, RechargeOrderRow, ShopifyOrderRow, SubscriptionRow
from chalicelib.records import RechargeOrder, ShopifyOrder, StateRecord, Subscription
from chalicelib.resolve import Snapshot


def _guarded_upsert(session: Session, model: type[Base], values: dict, key_columns: list[str]) -> None:
    table = model.__table__
    stmt = sqlite_insert(table).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=key_columns,
        set_={name: stmt.excluded[name] for name in values if name not in key_columns},
        where=table.c.updated_at < stmt.excluded.updated_at,
    )
    session.execute(stmt)


def upsert(session: Session, record: StateRecord) -> None:
    values = asdict(record)
    match record:
        case ShopifyOrder():
            values["variant_ids"] = list(record.variant_ids)
            _guarded_upsert(session, ShopifyOrderRow, values, ["shop", "order_id"])
        case RechargeOrder():
            values["subscription_ids"] = list(record.subscription_ids)
            _guarded_upsert(session, RechargeOrderRow, values, ["recharge_order_id"])
        case Subscription():
            _guarded_upsert(session, SubscriptionRow, values, ["subscription_id"])
        case _:
            raise TypeError(f"unsupported record type: {type(record).__name__}")


def load_snapshot(session: Session) -> Snapshot:
    shopify_orders = [
        ShopifyOrder(
            shop=row.shop,
            order_id=row.order_id,
            customer_id=row.customer_id,
            variant_ids=tuple(row.variant_ids),
            premium_cents=row.premium_cents,
            currency=row.currency,
            created_at=row.created_at,
            cancelled_at=row.cancelled_at,
            updated_at=row.updated_at,
        )
        for row in session.scalars(select(ShopifyOrderRow))
    ]
    recharge_orders = [
        RechargeOrder(
            recharge_order_id=row.recharge_order_id,
            type=row.type,
            shopify_order_id=row.shopify_order_id,
            shopify_customer_id=row.shopify_customer_id,
            subscription_ids=tuple(row.subscription_ids),
            status=row.status,
            updated_at=row.updated_at,
        )
        for row in session.scalars(select(RechargeOrderRow))
    ]
    subscriptions = [
        Subscription(
            subscription_id=row.subscription_id,
            customer_id=row.customer_id,
            variant_id=row.variant_id,
            status=row.status,
            created_at=row.created_at,
            cancelled_at=row.cancelled_at,
            updated_at=row.updated_at,
        )
        for row in session.scalars(select(SubscriptionRow))
    ]
    return Snapshot.build(shopify_orders, recharge_orders, subscriptions)
