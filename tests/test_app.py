"""The Chalice routes, exercised through ``chalice.test.Client``."""

from __future__ import annotations

import json

import pytest
from chalice.test import Client

from app import app
from chalicelib import db
from tests.helpers import ROOT, fresh_db


@pytest.fixture
def client():
    db.set_session_factory(fresh_db())
    try:
        with Client(app) as client:
            yield client
    finally:
        db.set_session_factory(None)


def shopify_headers(webhook_id="wh-1", topic="orders/create"):
    return {
        "Content-Type": "application/json",
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": "demo-coffee.myshopify.com",
        "X-Shopify-Webhook-Id": webhook_id,
    }


def fixture_body(path: str) -> str:
    return (ROOT / "fixtures" / path).read_text()


def test_shopify_webhook_is_stored_and_processed(client):
    response = client.http.post(
        "/webhooks/shopify", headers=shopify_headers(), body=fixture_body("shopify/order_original_create.json")
    )
    assert response.status_code == 200
    assert response.json_body == {"event_id": 1, "duplicate": False, "status": "processed", "delivery_count": 1}


def test_shopify_duplicate_returns_200(client):
    raw = fixture_body("shopify/order_original_create.json")
    client.http.post("/webhooks/shopify", headers=shopify_headers(), body=raw)
    response = client.http.post("/webhooks/shopify", headers=shopify_headers(), body=raw)
    assert response.status_code == 200
    assert response.json_body["duplicate"] is True
    assert response.json_body["delivery_count"] == 2


def test_shopify_missing_header_is_400(client):
    headers = shopify_headers()
    del headers["X-Shopify-Webhook-Id"]
    response = client.http.post("/webhooks/shopify", headers=headers, body="{}")
    assert response.status_code == 400


def test_recharge_topic_comes_from_the_route(client):
    response = client.http.post(
        "/webhooks/recharge/order/created",
        headers={"Content-Type": "application/json"},
        body=fixture_body("recharge/order_recurring_created.json"),
    )
    assert response.status_code == 200
    assert response.json_body["status"] == "processed"


def test_recharge_bad_body_is_400(client):
    response = client.http.post(
        "/webhooks/recharge/order/created",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"order": {}}),
    )
    assert response.status_code == 400


def test_processing_failure_still_returns_200(client, monkeypatch):
    from chalicelib import ingest

    monkeypatch.setattr(ingest, "resolve_all", lambda *_: 1 / 0)
    response = client.http.post(
        "/webhooks/shopify", headers=shopify_headers(), body=fixture_body("shopify/order_original_create.json")
    )
    assert response.status_code == 200
    assert response.json_body["status"] == "failed"
