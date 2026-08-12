"""Tests for RebuildStatusReader.shadow_picks() -- a read-only dashboard
projection of the shadow ledger's real trade_decisions, deliberately
separate from the incumbent Main-ledger picks/order-execution pipeline
(dashboard_server.py's dashboard_picks()/preview_order()). Writes real rows
through ShadowLedger's own record_run/record_prediction/record_trade_decision
(not hand-crafted SQL) so this exercises the same schema/idempotency the
real rebuild-shadow CLI relies on.
"""

from __future__ import annotations

import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
if str(DASHBOARD_DIR.parent) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR.parent))

from dashboard.rebuild_status import RebuildStatusReader
from model_prediction.rebuild.shadow_ledger import ShadowLedger
from model_prediction.runtime_paths import RuntimePaths


def _seed_one_decision(ledger: ShadowLedger, *, action: str, side: str, reason_code: str) -> tuple[str, int]:
    run_id = ledger.record_run("tennis", run_type="rebuild-shadow-cli", horizon="late")
    ledger.record_stage_result(
        run_id,
        "decide",
        "SUCCESS",
        {
            "games": [
                {
                    "event_id": "181721",
                    "home_team": "Luciano Darderi",
                    "away_team": "Brandon Nakashima",
                    "predicted_winner": "home",
                }
            ]
        },
    )
    ledger.record_prediction(
        run_id=run_id, sport="tennis", event_id="181721", horizon="late",
        decision_time_utc="2026-08-10T22:30:00+00:00",
        forecast={
            "event_id": "181721",
            "predicted_winner": "home",
            # No calibrated probabilities here on purpose: this seeds the
            # no-calibration path, where the conservative lower bound must
            # surface instead of raw (assertion below pins 0.56, not 0.59).
            "raw_probabilities": {"home": 0.59, "away": 0.41},
            "probability_lower": {"home": 0.56, "away": 0.41},
            "probability_upper": {"home": 0.62, "away": 0.41},
            "expected_home_score": 0.0,
            "expected_away_score": 0.0,
            "model_artifact_hash": "elo-hash-1",
            "calibration_artifact_hash": "elo_fixed_haircut_v1",
        },
    )
    decision_id, _ = ledger.record_trade_decision(
        run_id=run_id, sport="tennis", event_id="181721", horizon="late",
        decision_time_utc="2026-08-10T22:30:00+00:00",
        model_artifact_hash="elo-hash-1", market_snapshot_hash="mkt-hash-1",
        decision_policy_version="winner_first_v1",
        decision={
            "event_id": "181721", "action": action, "predicted_winner": "home",
            "market_type": "moneyline", "selected_market": None, "units": 1.5 if action == "BET" else 0.0,
            "reason_code": reason_code, "cost_adjusted_edge": 0.055 if action == "BET" else None,
            "evaluated_market": {"market_id": "390859", "team_or_side": side, "line": None},
        },
    )
    return run_id, decision_id


def test_shadow_picks_returns_real_rows_with_team_names_from_decide_stage(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    paths.rebuild_root.mkdir(parents=True, exist_ok=True)
    ledger = ShadowLedger(paths.rebuild_shadow_db)
    _seed_one_decision(ledger, action="BET", side="home", reason_code="qualified")
    ledger.close()

    reader = RebuildStatusReader(paths=paths)
    result = reader.shadow_picks()

    assert result["status"] == "ok"
    assert len(result["picks"]) == 1
    pick = result["picks"][0]
    assert pick["record_type"] == "shadow_research"
    assert pick["home_team"] == "Luciano Darderi"
    assert pick["away_team"] == "Brandon Nakashima"
    assert pick["market_type"] == "moneyline"
    assert pick["trade_candidate"] is True
    assert pick["units"] == 1.5
    assert pick["model_probability"] == 0.56  # side="home" -> probability_lower.home
    assert pick["status"] == "open"  # no settlement recorded
    assert pick["sportsbook"] == "polymarket"


def test_shadow_picks_marks_no_bet_decisions_as_not_trade_candidates(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    paths.rebuild_root.mkdir(parents=True, exist_ok=True)
    ledger = ShadowLedger(paths.rebuild_shadow_db)
    _seed_one_decision(ledger, action="NO_BET", side="away", reason_code="not_aligned_with_predicted_winner")
    ledger.close()

    reader = RebuildStatusReader(paths=paths)
    result = reader.shadow_picks()

    pick = result["picks"][0]
    assert pick["decision"] == "NO_BET"
    assert pick["trade_candidate"] is False
    assert pick["reason_code"] == "not_aligned_with_predicted_winner"
    assert pick["units"] == 0.0


def test_shadow_picks_never_creates_a_missing_database(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    reader = RebuildStatusReader(paths=paths)
    result = reader.shadow_picks()
    assert result["status"] == "unavailable"
    assert result["picks"] == []
    assert not reader.shadow_db.exists()


def test_shadow_picks_reflects_real_settlement_when_present(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    paths.rebuild_root.mkdir(parents=True, exist_ok=True)
    ledger = ShadowLedger(paths.rebuild_shadow_db)
    run_id, decision_id = _seed_one_decision(ledger, action="BET", side="home", reason_code="qualified")
    ledger.conn.execute(
        "INSERT INTO settlements (created_at, run_id, sport, event_id, schema_version, trade_decision_id, "
        "outcome, settled_price, pnl, settled_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-08-11T00:00:00+00:00", run_id, "tennis", "181721", "1", decision_id,
         "win", 0.62, 1.05, "2026-08-11T00:00:00+00:00"),
    )
    ledger.conn.commit()
    ledger.close()

    reader = RebuildStatusReader(paths=paths)
    result = reader.shadow_picks()
    pick = result["picks"][0]
    assert pick["status"] == "settled"
    assert pick["result"] == "win"
    assert pick["pnl_units"] == 1.05


def test_shadow_picks_route_is_registered_in_read_rebuild_view(tmp_path):
    from dashboard.rebuild_status import read_rebuild_view

    paths = RuntimePaths.for_test(tmp_path)
    result = read_rebuild_view("shadow-picks", paths=paths)
    assert result["status"] == "unavailable"
    assert result["picks"] == []
