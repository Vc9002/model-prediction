"""Main totals restored (2026-11-13) behind the current-season sample gate and a policy switch.

The pause (93aa093c) was a hard `kind != "total"` in the Main destination. Now a total reaches
Main under exactly the spread/moneyline conditions -- executable fresh quote, edge >= min_edge,
divergence gate, current-season sample >= models.NCAAB.min_team_games -- unless
models.NCAAB.main_totals_enabled is false, which is the operator's pause lever.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from model_prediction.college_basketball_forward import forecast_ncaab_sport
from model_prediction.features.base import GameRecord
from model_prediction.main_ledgers import MultiSportPickLedger


def _run(tmp_path, monkeypatch, *, sample: int, policy: dict) -> dict:
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    games = [
        GameRecord(str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 70, 75)
        for i in range(20)
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before", lambda *a: games
    )
    event = {
        "event_id": "fixture",
        "event_start_utc": start,
        "title": "Away vs. Home",
        "markets": [
            {
                "market_id": "fixture-tot",
                "market_slug": "fixture-tot",
                "market_type": "total",
                "line": 145.5,
                "sides": [
                    {"selection": "over", "price_probability": 0.52, "line": 145.5, "is_long": True},
                    {"selection": "under", "price_probability": 0.48, "line": 145.5, "is_long": False},
                ],
            }
        ],
    }
    archive = tmp_path / "odds" / "ncaab" / now.date().isoformat() / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        json.dumps(
            {
                "provider": "polymarket_us",
                "reconstructed": False,
                "timestamp_valid": True,
                "market_state": "MARKET_STATE_OPEN",
                "event_id": "fixture",
                "event_start_utc": start,
                "market_id": "fixture-tot",
                "market_slug": "fixture-tot",
                "market_type": "total",
                "line": 145.5,
                "observed_at_utc": (now - timedelta(minutes=1)).isoformat(),
                "transact_time_utc": (now - timedelta(minutes=2)).isoformat(),
                "long": {"bid": 0.51, "ask": 0.53},
                "short": {"bid": 0.47, "ask": 0.49},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate", lambda *a: [event]
    )

    class SpreadModel:
        version = "ncaab-spread-v6"

        def predict_games(self, history, upcoming):
            return []

    class TotalModel:
        version = "ncaab-total-v6"

        def predict_games(self, history, upcoming):
            return [
                SimpleNamespace(
                    event_id=g.event_id,
                    market_type="total",
                    probabilities={"over": 0.62, "under": 0.38},
                    uncertainty=0.1,
                    rationale="fixture",
                    line=145.5,
                    inference_inputs={"mean": 150.0, "sd": 16.5, "line": 145.5},
                    feature_basis={"current_season_sample": sample},
                )
                for g in upcoming
            ]

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", SpreadModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_total_model", TotalModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", SpreadModel)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        lambda *a, **k: SimpleNamespace(
            champion=lambda sport, market: SimpleNamespace(
                available=True, model_id="ncaab-total-v6" if market == "total" else "ncaab-spread-v6"
            )
        ),
    )
    flat = MultiSportPickLedger(tmp_path, flat=True)
    main = MultiSportPickLedger(tmp_path / "main")
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=now.date().isoformat(),
        config={"models": {"NCAAB": {"min_edge": 0.035, "min_team_games": 8, **policy}}},
        flat_ledger=flat,
        main_ledger=main,
    )
    assert not report["errors"], report["errors"]
    report["_main_rows"] = main.rows()
    return report


def test_total_with_sample_and_executable_quote_reaches_main(tmp_path, monkeypatch):
    report = _run(tmp_path, monkeypatch, sample=9, policy={})
    assert report["flat_logged"] == 1
    assert report["main_logged"] == 1
    assert report["_main_rows"][0]["market_type"] == "total"
    assert report["_main_rows"][0]["sportsbook"] == "polymarket_us_executable_ask"


def test_total_below_sample_gate_stays_out_of_main(tmp_path, monkeypatch):
    report = _run(tmp_path, monkeypatch, sample=3, policy={})
    assert report["flat_logged"] == 1
    assert report["main_logged"] == 0
    assert any(
        s.get("reason") == "insufficient_current_season_sample_for_main" and s.get("market_type") == "total"
        for s in report["skipped"]
    )


@pytest.mark.parametrize("flag", [False, "false", 0])
def test_policy_switch_pauses_main_totals_only(tmp_path, monkeypatch, flag):
    report = _run(
        tmp_path,
        monkeypatch,
        sample=9,
        policy={"main_totals_enabled": bool(flag) if flag != "false" else False},
    )
    assert report["flat_logged"] == 1
    assert report["main_logged"] == 0
    assert any(s.get("reason") == "main_totals_disabled_by_policy" for s in report["skipped"])
