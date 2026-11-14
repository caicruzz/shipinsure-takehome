"""Replay a scenario through the ingest pipeline and print the resulting state.

    python replay.py scenarios/out_of_order.json
    python replay.py scenarios/happy_path.json --db shipinsure.db   # keep the SQLite file

Without ``--db`` the scenario runs against a fresh temporary SQLite file.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from chalicelib.db import dispose, make_session_factory
from chalicelib.report import coverage, render
from chalicelib.resolve import CoverageStatus
from chalicelib.scenarios import deliver, load_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path, help="path to a scenario JSON file")
    parser.add_argument("--db", type=Path, help="SQLite file to write (default: fresh temporary file)")
    args = parser.parse_args(argv)

    scenario = load_scenario(args.scenario)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = args.db or Path(tmp) / "replay.db"
        session_factory = make_session_factory(f"sqlite:///{db_path}")

        print(f"Scenario: {args.scenario}\n{scenario.description}\n")
        for delivery in scenario.deliveries:
            result = deliver(session_factory, delivery)
            marker = "duplicate" if result.duplicate else result.status
            with session_factory() as session:
                pending = [f"{r.order_id}:{r.reason}" for r in coverage(session) if r.status == CoverageStatus.PENDING]
            print(
                f"  -> {delivery.source:<8} {delivery.topic:<22} {delivery.label:<38} "
                f"event {result.event_id:>2} {marker:<9}  pending: {', '.join(pending) or '-'}"
            )

        with session_factory() as session:
            print(render(session))
        dispose(session_factory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
