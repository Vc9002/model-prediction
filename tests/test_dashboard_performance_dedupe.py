from __future__ import annotations

from model_prediction.dashboard.picks import _dedupe_contract_observations, performance


def _row(pick_id: str, *, line: float, observed: str, pnl: float) -> dict:
    return {
        "pick_id": pick_id,
        "ledger_tier": "main",
        "sport": "tennis",
        "league": "TENNIS",
        "event_id": "match-1",
        "event_start_utc": "2026-08-23T19:00:00Z",
        "created_at_utc": observed,
        "observed_at_utc": observed,
        "settled_at_utc": "2026-08-23T22:00:00Z",
        "market_type": "total",
        "selection": "over",
        "line": line,
        "sportsbook": "polymarket_us",
        "status": "settled",
        "result": "win" if pnl > 0 else "loss",
        "units": 1.0,
        "pnl_units": pnl,
        "model_probability": 0.7,
    }


def test_performance_excludes_refresh_observation_but_preserves_alternate_line() -> None:
    old = _row("old", line=22.5, observed="2026-08-23T12:00:00Z", pnl=-1.0)
    refreshed = _row("new", line=22.5, observed="2026-08-23T13:00:00Z", pnl=-1.0)
    alternate = _row("alternate", line=17.5, observed="2026-08-23T13:00:00Z", pnl=0.2)

    payload = performance([old, refreshed, alternate])

    assert payload["settled"] == 2
    assert payload["duplicate_observations_excluded"] == 1
    assert payload["total_pnl"] == -0.8


def test_late_refresh_never_displaces_verified_pregame_observation() -> None:
    valid = _row("valid", line=22.5, observed="2026-08-23T18:59:00Z", pnl=-1.0)
    late = _row("late", line=22.5, observed="2026-08-23T19:01:00Z", pnl=1.0)

    assert [row["pick_id"] for row in _dedupe_contract_observations([valid, late])] == ["valid"]
