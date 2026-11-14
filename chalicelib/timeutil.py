"""Timestamp parsing. Everything inside the app is a timezone-aware UTC datetime."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from a webhook payload into aware UTC.

    Shopify sends offsets like ``-04:00``; Recharge 2021-11 sends ``+00:00``.
    A timestamp without an offset is assumed to be UTC.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
