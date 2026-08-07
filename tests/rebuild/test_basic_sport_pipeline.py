"""Tests for the shared basic-adapter pipeline (basic_sport_pipeline.py) --
the Elo-baseline load_state/predict_stage/match_markets_stage/decide_stage
functions _BasicEloAdapter (sport_adapter.py) calls for NBA/WNBA/NFL/
Soccer/Tennis."""

from __future__ import annotations

import polars as pl

from model_prediction.rebuild import basic_sport_pipeline as pipeline
from model_prediction.rebuild.storage import NormalizedStore, provenance_row, utc_now


def _write_scoreboard(data_root, sport: str, event_id: str, event_start_utc: str, home: str, away: str,
                       status: str, home_score: int = 0, away_score: int = 0) -> None:
    norm = NormalizedStore(f"{data_root}/normalized")
    row = {
        **provenance_row(source="espn_public", source_record_id=event_id, source_version="v1",
                          observed_at_utc=utc_now().isoformat(), effective_at_utc=event_start_utc,
                          event_start_utc=event_start_utc),
        "event_id": event_id, "home_team": home, "away_team": away,
        "home_score": home_score, "away_score": away_score, "status": status, "venue": "",
    }
    norm.write(sport, "scoreboard", pl.DataFrame([row]), primary_key=["event_id"])


class TestLoadState:
    def test_no_scoreboard_ever_collected_returns_none_not_a_crash(self, tmp_path):
        assert pipeline.load_state(str(tmp_path), "nba", "2026-08-06") is None

    def test_no_scheduled_games_for_date_returns_none(self, tmp_path):
        _write_scoreboard(tmp_path, "nba", "1", "2026-08-05T22:10:00+00:00", "A", "B", "STATUS_FINAL")
        assert pipeline.load_state(str(tmp_path), "nba", "2026-08-06") is None

    def test_real_scheduled_game_produces_real_state(self, tmp_path):
        _write_scoreboard(tmp_path, "nba", "1", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")
        assert state is not None
        assert state.tonight.height == 1
        assert "1" in state.decision_times


class TestPredictStage:
    def test_insufficient_history_stops_honestly(self, tmp_path):
        _write_scoreboard(tmp_path, "nba", "1", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")
        result = pipeline.predict_stage(state, str(tmp_path))
        assert result["status"] == "insufficient_history"

    def test_predicted_winner_is_frozen_before_any_market_is_inspected(self, tmp_path):
        for i in range(12):
            _write_scoreboard(tmp_path, "nba", f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                               "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90)
        _write_scoreboard(tmp_path, "nba", "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")

        result = pipeline.predict_stage(state, str(tmp_path))
        assert result["status"] == "ok"
        assert result["games_predicted"] == 1
        forecast = state.forecasts["401"]
        assert forecast.predicted_winner == "home"
        assert forecast.model_artifact_hash  # real, non-empty artifact identity

    def test_records_a_real_model_artifact_when_given_a_ledger(self, tmp_path):
        from model_prediction.rebuild.shadow_ledger import ShadowLedger

        for i in range(12):
            _write_scoreboard(tmp_path, "nba", f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                               "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90)
        _write_scoreboard(tmp_path, "nba", "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        run_id = ledger.record_run("nba", run_type="test")
        pipeline.predict_stage(state, str(tmp_path), ledger=ledger, run_id=run_id)
        row = ledger.conn.execute(
            "SELECT run_id FROM model_artifacts WHERE artifact_hash = ?", (state.forecasts["401"].model_artifact_hash,)
        ).fetchone()
        ledger.close()
        assert row is not None
        assert row["run_id"] == run_id


class TestDecideStage:
    def test_decide_without_predict_first_raises(self, tmp_path):
        _write_scoreboard(tmp_path, "nba", "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")
        try:
            pipeline.decide_stage(state)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "predict_stage" in str(e)

    def test_no_real_market_candidates_produces_a_real_no_bet_not_a_crash(self, tmp_path):
        for i in range(12):
            _write_scoreboard(tmp_path, "nba", f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                               "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90)
        _write_scoreboard(tmp_path, "nba", "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")
        pipeline.predict_stage(state, str(tmp_path))

        result = pipeline.decide_stage(state)
        assert result["status"] == "ok"
        assert result["total_bets"] == 0
        assert result["games"][0]["predicted_winner"] == "home"


class TestMatchMarketsStage:
    def test_spread_and_total_rows_are_excluded_moneyline_only(self, tmp_path):
        from model_prediction.rebuild.storage import MarketStore

        for i in range(12):
            _write_scoreboard(tmp_path, "nba", f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                               "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90)
        _write_scoreboard(tmp_path, "nba", "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "nba", "2026-08-06")
        pipeline.predict_stage(state, str(tmp_path))

        markets = MarketStore(f"{tmp_path}/markets")
        rows = []
        for market_type, team_or_side, line in [("moneyline", "home", None), ("spread", "home", -3.5), ("total", "over", 210.5)]:
            rows.append({
                **provenance_row("polymarket_us", f"401_{market_type}", "v1", utc_now().isoformat(),
                                  utc_now().isoformat(), "2026-08-06T22:10:00+00:00"),
                "event_id": "401", "market_id": f"m_{market_type}", "market_type": market_type,
                "team_or_side": team_or_side, "team": "Alpha" if market_type != "total" else None,
                "line": line, "executable_price": 0.55, "decimal_odds": None, "american_odds": None,
                "available_depth": None,
            })
        markets.write_books("nba", "2026-08-06", pl.DataFrame(rows))

        result = pipeline.match_markets_stage(state, str(tmp_path), lambda d: {"status": "ok"})
        assert result["status"] == "ok"
        candidates = state.candidates_by_event["401"]
        assert len(candidates) == 1
        assert candidates[0].market_type == "moneyline"
