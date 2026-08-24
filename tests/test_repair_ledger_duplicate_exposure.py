from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair_ledger_duplicate_exposure.py"
SPEC = importlib.util.spec_from_file_location("repair_ledger_duplicate_exposure", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _row(pick_id: str, *, line: float, p: float, price: float, observed: str, result: str) -> dict:
    return {
        "pick_id": pick_id,
        "ledger_tier": "main",
        "sport": "tennis",
        "league": "TENNIS",
        "event_id": "match-1",
        "event_start_utc": "2026-08-23T19:00:00Z",
        "created_at_utc": observed,
        "observed_at_utc": observed,
        "market_type": "total",
        "selection": "over",
        "line": line,
        "sportsbook": "polymarket_us",
        "model_probability": p,
        "market_probability_at_decision": price,
        "edge": p - price,
        "status": "settled",
        "result": result,
        "away_score": 0,
        "home_score": 2,
    }


def test_plan_collapses_refresh_then_uses_pregame_ev_not_outcome() -> None:
    old = _row("old", line=22.5, p=0.76, price=0.50, observed="2026-08-23T12:00:00Z", result="loss")
    refreshed = _row(
        "refreshed", line=22.5, p=0.788, price=0.49, observed="2026-08-23T13:00:00Z", result="loss"
    )
    low_line_winner = _row(
        "winner", line=17.5, p=0.977, price=0.89, observed="2026-08-23T13:00:00Z", result="win"
    )

    plan = MODULE.build_plan([old, refreshed, low_line_winner])

    assert plan["refresh_groups"] == 1
    assert plan["tennis_ladder_groups"] == 1
    assert set(plan["archive_ids"]) == {"old", "winner"}
    survivor_by_id = {entry["row"]["pick_id"]: entry["survivor_pick_id"] for entry in plan["entries"]}
    assert survivor_by_id == {"old": "refreshed", "winner": "refreshed"}


def test_plan_preserves_different_tennis_market_families() -> None:
    total = _row("total", line=22.5, p=0.7, price=0.5, observed="2026-08-23T13:00:00Z", result="win")
    spread = {**total, "pick_id": "spread", "market_type": "spread", "selection": "home"}

    plan = MODULE.build_plan([total, spread])

    assert plan["archive_ids"] == []
    assert plan["remove_ids"] == []
