"""Adapter contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from chalicelib.records import RechargeOrder, Subscription
from chalicelib.timeutil import parse_timestamp


class UnsupportedPayload(ValueError):
    """The payload is missing a field the adapter needs."""


def required_timestamp(obj: Mapping[str, Any], field: str) -> datetime:
    value = parse_timestamp(obj.get(field))
    if value is None:
        raise UnsupportedPayload(f"missing {field}")
    return value


class SubscriptionPlatform(Protocol):
    """A subscription platform (Recharge today; others later, TDD §9 #10).

    ``normalize`` turns one webhook into zero or more normalized records. An
    empty list means the topic is not one the prototype acts on.
    """

    def normalize(self, topic: str, payload: Mapping[str, Any]) -> list[RechargeOrder | Subscription]: ...
