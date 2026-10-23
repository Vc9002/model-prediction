from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from model_prediction.college_basketball_forward import forecast_ncaab_sport
from model_prediction.features.base import GameRecord
from model_prediction.main_ledgers import MultiSportPickLedger


def test_ncaab_records_real_model_output_and_rerun_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    games = [
        GameRecord(
            str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 65 + i % 3, 72 + i % 3
        )
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
        "markets": [
            {
                "market_type": "moneyline",
                "sides": [
                    {"selection": "away", "description": "Away", "price_probability": 0.45},
                    {"selection": "home", "description": "Home", "price_probability": 0.55},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate", lambda *a: [event]
    )
    flat_ledger = MultiSportPickLedger(tmp_path, flat=True)
    main_ledger = MultiSportPickLedger(tmp_path)
    args = {
        "data_root": tmp_path,
        "args_date": now.date().isoformat(),
        "config": {"models": {"NCAAB": {}}},
        "flat_ledger": flat_ledger,
        "main_ledger": main_ledger,
    }
    report = forecast_ncaab_sport(**args)
    assert not report["errors"]
    assert report["flat_logged"] == 1
    assert report["main_logged"] == 1
    flat_rows = flat_ledger.rows()
    main_rows = main_ledger.rows()
    assert len(flat_rows) == len(main_rows) == 1
    assert flat_rows[0]["selection"] == main_rows[0]["selection"] == "home"
    assert all(r["model_version"] == "ncaab-tempo-v1" for r in flat_rows + main_rows)
    assert all(r["sportsbook"] == "model_opinion_indicative_market" for r in flat_rows + main_rows)
    rerun = forecast_ncaab_sport(**args)
    assert rerun["flat_logged"] == 0
    assert rerun["main_logged"] == 0
    assert len(flat_ledger.rows()) == len(main_ledger.rows()) == 1


def test_no_games_does_not_construct_market_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard", lambda *a: {"events": []}
    )
    report = forecast_ncaab_sport(data_root=tmp_path, args_date="2026-10-20", config={})
    assert report["status"] == "no_scheduled_events"
    assert report["logged"] == 0


def test_ncaab_selects_best_market_edge_not_favorite(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before",
        lambda *a: [
            GameRecord("past", (now - timedelta(days=1)).isoformat(), "NCAAB", "Away", "Home", 70, 75)
        ],
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate",
        lambda *a: [
            {
                "event_id": "fixture",
                "event_start_utc": start,
                "markets": [
                    {
                        "market_type": "moneyline",
                        "sides": [
                            {"selection": "away", "description": "Away", "price_probability": 0.20},
                            {"selection": "home", "description": "Home", "price_probability": 0.80},
                        ],
                    }
                ],
            }
        ],
    )

    class Model:
        version = "ncaab-tempo-v1"

        def predict_games(self, history, upcoming):
            return [
                SimpleNamespace(
                    market_type="moneyline",
                    probabilities={"home": 0.60, "away": 0.40},
                    uncertainty=0.1,
                    rationale="fixture",
                )
            ]

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", Model)
    flat = MultiSportPickLedger(tmp_path, flat=True)
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=now.date().isoformat(),
        config={"models": {"NCAAB": {}}},
        flat_ledger=flat,
    )
    assert report["flat_logged"] == 1
    assert flat.rows()[0]["selection"] == "away"
    assert int(float(flat.rows()[0]["american_odds"])) == 400
