"""Fixture-replay scenarios (TDD §7).

A scenario is a JSON file listing webhook deliveries in arrival order::

    {
      "description": "...",
      "shop": "demo-coffee.myshopify.com",
      "deliveries": [
        {"source": "shopify", "topic": "orders/create", "webhook_id": "...",
         "fixture": "shopify/order_original_create.json"},
        {"source": "recharge", "topic": "subscription/created",
         "fixture": "recharge/subscription_created.json", "times": 3}
      ]
    }

Shopify deliveries get the headers Shopify sends (topic, shop domain, webhook
id). The raw body is the fixture file's bytes, untouched. ``times`` repeats a
delivery to simulate retries.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from chalicelib.db import SessionFactory
from chalicelib.ingest import IngestResult, ingest_recharge, ingest_shopify

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass(frozen=True)
class Delivery:
    source: str
    topic: str
    raw_body: bytes
    label: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    description: str
    deliveries: list[Delivery]


def load_scenario(path: str | Path, fixtures_dir: Path = FIXTURES_DIR) -> Scenario:
    spec = json.loads(Path(path).read_text())
    shop = spec.get("shop", "demo-coffee.myshopify.com")
    deliveries: list[Delivery] = []
    for entry in spec["deliveries"]:
        raw_body = (fixtures_dir / entry["fixture"]).read_bytes()
        headers = {"Content-Type": "application/json"}
        if entry["source"] == "shopify":
            headers |= {
                "X-Shopify-Topic": entry["topic"],
                "X-Shopify-Shop-Domain": shop,
                "X-Shopify-Webhook-Id": entry["webhook_id"],
                "X-Shopify-API-Version": "2025-10",
            }
        delivery = Delivery(
            source=entry["source"],
            topic=entry["topic"],
            raw_body=raw_body,
            label=entry.get("label") or entry["fixture"],
            headers=headers,
        )
        deliveries.extend([delivery] * int(entry.get("times", 1)))
    return Scenario(description=spec.get("description", ""), deliveries=deliveries)


def deliver(session_factory: SessionFactory, delivery: Delivery) -> IngestResult:
    """Send one delivery through the same ingest functions the Chalice routes use."""
    if delivery.source == "shopify":
        return ingest_shopify(session_factory, delivery.headers, delivery.raw_body)
    if delivery.source == "recharge":
        return ingest_recharge(session_factory, delivery.topic, delivery.headers, delivery.raw_body)
    raise ValueError(f"unknown source {delivery.source!r}")


def replay(session_factory: SessionFactory, deliveries: Iterable[Delivery]) -> list[IngestResult]:
    return [deliver(session_factory, d) for d in deliveries]
