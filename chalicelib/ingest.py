"""Webhook ingest: persist first, then normalize -> recompute -> post the diff.

1. ``_persist`` writes the raw delivery to ``events_inbox`` and commits. A
   duplicate hits ``UNIQUE(source, dedupe_key)``, bumps ``delivery_count`` and
   is not processed again. Only a failure here should surface as a 5xx.
2. ``process_event`` reads the stored body back (not the request), upserts the
   latest-state tables, recomputes coverage for every order, and posts ledger
   diffs, all in one transaction. A failure rolls that back and marks the inbox
   row ``failed`` for a later retry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from chalicelib import ledger, state
from chalicelib.adapters import SUBSCRIPTION_PLATFORMS, UnsupportedPayload
from chalicelib.adapters import shopify as shopify_adapter
from chalicelib.adapters.recharge import resource_for_topic
from chalicelib.db import SessionFactory
from chalicelib.models import EventInbox, EventSource, EventStatus
from chalicelib.records import StateRecord
from chalicelib.resolve import resolve_all
from chalicelib.timeutil import utcnow

logger = logging.getLogger(__name__)

SHOPIFY_WEBHOOK_ID = "x-shopify-webhook-id"
SHOPIFY_TOPIC = "x-shopify-topic"
SHOPIFY_SHOP_DOMAIN = "x-shopify-shop-domain"

_MAX_ERROR_LENGTH = 2000


class InvalidWebhook(ValueError):
    """The delivery cannot be accepted (missing headers, unparseable body)."""


@dataclass(frozen=True)
class IngestResult:
    event_id: int
    duplicate: bool
    status: str
    delivery_count: int


def _lower_keys(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _decode(raw_body: bytes | str) -> tuple[str, dict[str, Any]]:
    try:
        body = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebhook(f"body is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidWebhook("body must be a JSON object")
    return body, payload


def ingest_shopify(session_factory: SessionFactory, headers: Mapping[str, str], raw_body: bytes | str) -> IngestResult:
    headers = _lower_keys(headers)
    missing = [h for h in (SHOPIFY_WEBHOOK_ID, SHOPIFY_TOPIC, SHOPIFY_SHOP_DOMAIN) if not headers.get(h)]
    if missing:
        raise InvalidWebhook(f"missing Shopify headers: {', '.join(missing)}")
    body, _ = _decode(raw_body)
    return _ingest(
        session_factory,
        source=EventSource.SHOPIFY,
        # Documented per-delivery id; retries of the same webhook reuse it.
        dedupe_key=headers[SHOPIFY_WEBHOOK_ID],
        topic=headers[SHOPIFY_TOPIC],
        shop=headers[SHOPIFY_SHOP_DOMAIN],
        headers=headers,
        body=body,
    )


def recharge_dedupe_key(topic: str, payload: Mapping[str, Any]) -> str:
    """Recharge documents no per-delivery id, so key on the object version.

    Retries carry the same body, so ``{topic}:{object_id}:{updated_at}`` is stable.
    """
    obj = payload.get(resource_for_topic(topic))
    if not isinstance(obj, dict) or obj.get("id") is None or not obj.get("updated_at"):
        raise InvalidWebhook(f"Recharge {topic} body needs {resource_for_topic(topic)}.id and .updated_at")
    return f"{topic}:{obj['id']}:{obj['updated_at']}"


def ingest_recharge(
    session_factory: SessionFactory, topic: str, headers: Mapping[str, str], raw_body: bytes | str
) -> IngestResult:
    body, payload = _decode(raw_body)
    return _ingest(
        session_factory,
        source=EventSource.RECHARGE,
        dedupe_key=recharge_dedupe_key(topic, payload),
        topic=topic,
        shop=None,
        headers=_lower_keys(headers),
        body=body,
    )


def _ingest(session_factory: SessionFactory, **event: Any) -> IngestResult:
    event_id, delivery_count = _persist(session_factory, **event)
    if delivery_count > 1:
        with session_factory() as session:
            current = session.get_one(EventInbox, event_id)
            return IngestResult(event_id, duplicate=True, status=current.status, delivery_count=delivery_count)
    status = process_event(session_factory, event_id)
    return IngestResult(event_id, duplicate=False, status=status, delivery_count=delivery_count)


def _persist(
    session_factory: SessionFactory,
    *,
    source: str,
    dedupe_key: str,
    topic: str,
    shop: str | None,
    headers: dict[str, str],
    body: str,
) -> tuple[int, int]:
    now = utcnow()
    stmt = sqlite_insert(EventInbox).values(
        source=source,
        dedupe_key=dedupe_key,
        topic=topic,
        shop=shop,
        headers=headers,
        body=body,
        received_at=now,
        last_received_at=now,
        delivery_count=1,
        status=EventStatus.RECEIVED,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "dedupe_key"],
        set_={"delivery_count": EventInbox.delivery_count + 1, "last_received_at": now},
    ).returning(EventInbox.id, EventInbox.delivery_count)
    with session_factory.begin() as session:
        event_id, delivery_count = session.execute(stmt).one()
    return event_id, delivery_count


def _normalize(event: EventInbox) -> list[StateRecord]:
    payload = json.loads(event.body)
    if event.source == EventSource.SHOPIFY:
        return list(shopify_adapter.normalize(event.topic, event.shop or "", payload))
    platform = SUBSCRIPTION_PLATFORMS.get(event.source)
    if platform is None:
        raise UnsupportedPayload(f"no adapter for source {event.source!r}")
    return list(platform.normalize(event.topic, payload))


def process_event(session_factory: SessionFactory, event_id: int) -> str:
    try:
        with session_factory.begin() as session:
            event = session.get_one(EventInbox, event_id)
            records = _normalize(event)
            for record in records:
                state.upsert(session, record)
            if records:
                resolutions = resolve_all(state.load_snapshot(session))
                ledger.post_all(session, resolutions, source_event_id=event_id)
            event.status = EventStatus.PROCESSED if records else EventStatus.IGNORED
            event.error = None
            event.processed_at = utcnow()
            return event.status
    except Exception as exc:  # any processing failure is recorded for retry, never lost
        logger.exception("processing failed for inbox event %s", event_id)
        with session_factory.begin() as session:
            session.execute(
                update(EventInbox)
                .where(EventInbox.id == event_id)
                .values(status=EventStatus.FAILED, error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH])
            )
        return EventStatus.FAILED
