"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from datetime import timedelta

DEFAULT_DATABASE_URL = "sqlite:///shipinsure.db"

# How the ShipInsure line item is recognised on a Shopify order. Production
# identification is an open question (TDD §10 #3); the prototype matches a
# case-insensitive keyword against the line item's vendor or title.
DEFAULT_SHIPINSURE_KEYWORD = "shipinsure"


# How long before a subscription was created its original Shopify order may have
# been placed (TDD §4). Recharge creates the subscription from Shopify's order
# webhook, so seconds to minutes is normal. Open question #5.
DEFAULT_SUBSCRIPTION_ORIGIN_WINDOW_MINUTES = 60


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def shipinsure_keyword() -> str:
    return os.environ.get("SHIPINSURE_LINE_ITEM_KEYWORD", DEFAULT_SHIPINSURE_KEYWORD).lower()


def subscription_origin_window() -> timedelta:
    minutes = int(os.environ.get("SUBSCRIPTION_ORIGIN_WINDOW_MINUTES", DEFAULT_SUBSCRIPTION_ORIGIN_WINDOW_MINUTES))
    return timedelta(minutes=minutes)
