from __future__ import annotations

from pathlib import Path

from chalicelib.db import SessionFactory, make_session_factory
from chalicelib.report import Balance, balances, coverage
from chalicelib.resolve import Resolution
from chalicelib.scenarios import Delivery, Scenario, load_scenario

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"

ORIGINAL = "6100000000001"
RENEWAL = "6100000000002"
RENEWAL_2 = "6100000000003"
DUPLICATE_PURCHASE = "6100000000004"
ORIGINAL_2 = "6100000000005"
RENEWAL_4 = "6100000000006"
SUBSCRIPTION = "410000001"
SUBSCRIPTION_2 = "410000002"


def fresh_db() -> SessionFactory:
    return make_session_factory("sqlite://")


def scenario(name: str) -> Scenario:
    return load_scenario(SCENARIOS / f"{name}.json")


def deliveries(name: str) -> list[Delivery]:
    return scenario(name).deliveries


def coverage_by_order(sf: SessionFactory) -> dict[str, Resolution]:
    with sf() as session:
        return {r.order_id: r for r in coverage(session)}


def balance_cents(sf: SessionFactory) -> dict[str, int]:
    with sf() as session:
        return {order_id: b.cents for order_id, b in balances(session).items()}


def balance_rows(sf: SessionFactory) -> dict[str, Balance]:
    with sf() as session:
        return balances(session)
