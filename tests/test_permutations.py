"""TDD §7 scenario 3: arrival order cannot change the outcome.

Every permutation is replayed against a fresh database and must end with the
same coverage and the same per-order balances. Scenarios with an update event
also cover the stale-update case: whenever the newer version arrives first, the
older ``orders/create`` must not regress state.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from chalicelib.db import dispose
from chalicelib.scenarios import replay
from tests.helpers import balance_cents, coverage_by_order, deliveries, fresh_db

EXPECTED_BALANCES = {
    "happy_path": {"6100000000001": 250, "6100000000002": 250},
    "late_cancellation": {"6100000000001": 250, "6100000000002": 0},
    "item_removed": {"6100000000001": 0, "6100000000002": 0},
}


def _final_state(steps):
    sf = fresh_db()
    try:
        replay(sf, steps)
        return coverage_by_order(sf), balance_cents(sf)
    finally:
        dispose(sf)


@pytest.mark.parametrize(
    "name",
    [
        "happy_path",  # 5 events, 120 permutations: TDD scenario 3
        pytest.param("late_cancellation", marks=pytest.mark.slow),  # 6 events, 720 permutations
        pytest.param("item_removed", marks=pytest.mark.slow),  # 6 events, 720 permutations, includes a stale update
    ],
)
def test_all_permutations_reach_the_same_final_state(name):
    steps = deliveries(name)
    reference_coverage, reference_balances = _final_state(steps)
    assert reference_balances == EXPECTED_BALANCES[name]

    expected_nonzero = {k: v for k, v in reference_balances.items() if v}
    for order in permutations(steps):
        coverage, balances = _final_state(order)
        labels = [d.label for d in order]
        assert coverage == reference_coverage, labels
        # A zero balance may be "charge then reversal" or "never charged" depending on order.
        assert {k: v for k, v in balances.items() if v} == expected_nonzero, labels
