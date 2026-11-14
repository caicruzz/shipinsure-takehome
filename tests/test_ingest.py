"""Inbox persistence, dedupe, failure handling and the ``updated_at`` guard."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from chalicelib import ingest
from chalicelib.ingest import InvalidWebhook, ingest_recharge, ingest_shopify
from chalicelib.models import EventInbox, EventStatus, ShopifyOrderRow
from chalicelib.scenarios import replay
from tests.helpers import ORIGINAL, ROOT, balance_cents, deliveries

SHOPIFY_HEADERS = {
    "X-Shopify-Topic": "orders/create",
    "X-Shopify-Shop-Domain": "demo-coffee.myshopify.com",
    "X-Shopify-Webhook-Id": "wh-1",
}


def body(path: str) -> bytes:
    return (ROOT / "fixtures" / path).read_bytes()


def test_raw_body_and_headers_are_stored_verbatim(session_factory):
    raw = body("shopify/order_original_create.json")
    result = ingest_shopify(session_factory, SHOPIFY_HEADERS, raw)

    with session_factory() as session:
        event = session.get_one(EventInbox, result.event_id)
    assert event.body == raw.decode()
    assert event.headers["x-shopify-webhook-id"] == "wh-1"
    assert (event.source, event.topic, event.shop) == ("shopify", "orders/create", "demo-coffee.myshopify.com")
    assert event.status == EventStatus.PROCESSED


def test_duplicate_delivery_bumps_count_and_is_not_reprocessed(session_factory, monkeypatch):
    raw = body("shopify/order_original_create.json")
    first = ingest_shopify(session_factory, SHOPIFY_HEADERS, raw)

    calls = []
    monkeypatch.setattr(ingest, "process_event", lambda *a: calls.append(a))
    second = ingest_shopify(session_factory, SHOPIFY_HEADERS, raw)

    assert calls == []
    assert (second.event_id, second.duplicate, second.delivery_count) == (first.event_id, True, 2)
    assert second.status == EventStatus.PROCESSED


def test_recharge_dedupe_key_is_topic_object_and_version(session_factory):
    result = ingest_recharge(session_factory, "order/created", {}, body("recharge/order_checkout_created.json"))
    with session_factory() as session:
        event = session.get_one(EventInbox, result.event_id)
    assert event.dedupe_key == "order/created:520000001:2026-08-01T14:00:06+00:00"


def test_new_version_of_same_recharge_object_is_a_new_event(session_factory):
    payload = json.loads(body("recharge/order_checkout_created.json"))
    first = ingest_recharge(session_factory, "order/updated", {}, json.dumps(payload))
    payload["order"]["updated_at"] = "2026-08-01T15:00:00+00:00"
    second = ingest_recharge(session_factory, "order/updated", {}, json.dumps(payload))
    assert not second.duplicate and second.event_id != first.event_id


def test_processing_failure_marks_row_failed_and_keeps_it(session_factory, monkeypatch):
    def boom(_snapshot):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(ingest, "resolve_all", boom)
    result = ingest_shopify(session_factory, SHOPIFY_HEADERS, body("shopify/order_original_create.json"))

    assert result.status == EventStatus.FAILED
    with session_factory() as session:
        event = session.get_one(EventInbox, result.event_id)
        assert event.status == EventStatus.FAILED
        assert event.error == "RuntimeError: resolver exploded"
        # The state upsert ran in the same transaction and was rolled back with it.
        assert session.scalars(select(ShopifyOrderRow)).all() == []


def test_unhandled_topic_is_stored_and_marked_ignored(session_factory):
    headers = SHOPIFY_HEADERS | {"X-Shopify-Topic": "orders/delete", "X-Shopify-Webhook-Id": "wh-del"}
    result = ingest_shopify(session_factory, headers, b'{"id": 1}')
    assert result.status == EventStatus.IGNORED


@pytest.mark.parametrize("missing", ["X-Shopify-Topic", "X-Shopify-Shop-Domain", "X-Shopify-Webhook-Id"])
def test_shopify_requires_delivery_headers(session_factory, missing):
    headers = {k: v for k, v in SHOPIFY_HEADERS.items() if k != missing}
    with pytest.raises(InvalidWebhook, match=missing.lower()):
        ingest_shopify(session_factory, headers, b"{}")


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_unparseable_body_is_rejected(session_factory, raw):
    with pytest.raises(InvalidWebhook):
        ingest_shopify(session_factory, SHOPIFY_HEADERS, raw)


def test_recharge_body_without_id_or_updated_at_is_rejected(session_factory):
    with pytest.raises(InvalidWebhook):
        ingest_recharge(session_factory, "order/created", {}, b'{"order": {"id": 1}}')


def test_stale_update_does_not_regress_state(session_factory):
    happy = {d.label: d for d in deliveries("item_removed")}
    edit = happy["O edited: ShipInsure item removed"]
    create = happy["original order O (ShipInsure item)"]

    replay(session_factory, [edit, create])  # newer version first, older create after

    with session_factory() as session:
        row = session.scalars(select(ShopifyOrderRow)).one()
    assert row.premium_cents is None  # the older create did not restore the removed item
    assert balance_cents(session_factory).get(ORIGINAL, 0) == 0
