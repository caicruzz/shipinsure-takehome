from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from chalicelib.adapters import UnsupportedPayload, shopify
from chalicelib.adapters.recharge import RechargePlatform
from chalicelib.records import RechargeOrder, Subscription
from tests.helpers import ROOT

SHOP = "demo-coffee.myshopify.com"


def fixture(path: str) -> dict:
    return json.loads((ROOT / "fixtures" / path).read_text())


def test_shopify_original_order_premium_and_utc_timestamps():
    [order] = shopify.normalize("orders/create", SHOP, fixture("shopify/order_original_create.json"))
    assert order.order_id == "6100000000001"
    assert order.premium_cents == 250
    assert order.currency == "USD"
    assert order.created_at == datetime(2026, 8, 1, 14, 0, tzinfo=UTC)  # -04:00 normalised
    assert order.cancelled_at is None


def test_shopify_renewal_has_no_premium():
    [order] = shopify.normalize("orders/create", SHOP, fixture("shopify/order_renewal_create.json"))
    assert order.premium_cents is None


def test_shopify_removed_line_item_does_not_count():
    [order] = shopify.normalize("orders/updated", SHOP, fixture("shopify/order_original_item_removed.json"))
    assert order.premium_cents is None


def test_shopify_premium_uses_quantity_and_decimal_math():
    payload = fixture("shopify/order_original_create.json")
    payload["line_items"][1] |= {"price": "0.10", "quantity": 3, "current_quantity": 3}
    [order] = shopify.normalize("orders/create", SHOP, payload)
    assert order.premium_cents == 30  # float math would give 30.000000000000004


def test_shopify_keyword_is_configurable(monkeypatch):
    monkeypatch.setenv("SHIPINSURE_LINE_ITEM_KEYWORD", "package protection")
    payload = fixture("shopify/order_original_create.json")
    payload["line_items"][1]["vendor"] = "Someone Else"
    [order] = shopify.normalize("orders/create", SHOP, payload)
    assert order.premium_cents == 250


def test_shopify_unhandled_topic_is_ignored():
    assert shopify.normalize("orders/delete", SHOP, {"id": 1}) == []


def test_shopify_missing_field_raises():
    with pytest.raises(UnsupportedPayload):
        shopify.normalize("orders/create", SHOP, {"id": 1})


def test_recharge_checkout_order_links_subscription_to_shopify_order():
    [record] = RechargePlatform().normalize("order/created", fixture("recharge/order_checkout_created.json"))
    assert record == RechargeOrder(
        recharge_order_id="520000001",
        type="checkout",
        shopify_order_id="6100000000001",
        subscription_ids=("410000001",),
        status="success",
        updated_at=datetime(2026, 8, 1, 14, 0, 6, tzinfo=UTC),
    )


def test_recharge_ignores_onetime_line_items_and_missing_external_id():
    payload = fixture("recharge/order_recurring_created.json")
    payload["order"]["external_order_id"] = {"ecommerce": None}
    payload["order"]["line_items"].append({"purchase_item_id": 999, "purchase_item_type": "onetime"})
    [record] = RechargePlatform().normalize("order/updated", payload)
    assert record.shopify_order_id is None
    assert record.subscription_ids == ("410000001",)


def test_recharge_subscription_cancelled():
    [record] = RechargePlatform().normalize("subscription/cancelled", fixture("recharge/subscription_cancelled.json"))
    assert isinstance(record, Subscription)
    assert record.status == "cancelled"
    assert record.cancelled_at == datetime(2026, 9, 15, 16, tzinfo=UTC)


def test_recharge_unhandled_topic_is_ignored():
    assert RechargePlatform().normalize("charge/created", {"charge": {}}) == []
