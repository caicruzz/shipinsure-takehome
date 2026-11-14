"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "sqlite:///shipinsure.db"

# How the ShipInsure line item is recognised on a Shopify order. Production
# identification is an open question (TDD §10 #3); the prototype matches a
# case-insensitive keyword against the line item's vendor or title.
DEFAULT_SHIPINSURE_KEYWORD = "shipinsure"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def shipinsure_keyword() -> str:
    return os.environ.get("SHIPINSURE_LINE_ITEM_KEYWORD", DEFAULT_SHIPINSURE_KEYWORD).lower()
