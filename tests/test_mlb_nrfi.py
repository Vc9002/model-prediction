"""Unit tests for MLB YRFI/NRFI feature engineering and predictive model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from model_prediction.features import yrfi_nrfi
from model_prediction.models.mlb_nrfi import MLBNRFIModel


def _write_snapshots(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _game(
    game_start: str,
    home_starter_id: int,
    away_starter_id: int,
    home_players: list[dict],
    away_players: list[dict],
    first_away: int = 0,
    first_home: int = 0,
) -> dict:
    return {
        "game_start_utc": game_start,
        "first_inning_runs_away": first_away,
        "first_inning_runs_home": first_home,
        "yrfi": int(first_away > 0 or first_home > 0),
        "home": {
            "team_name": "Home Team",
            "pitcher_order": [home_starter_id],
            "batting_order": [p["player_id"] for p in home_players if "batting" in p],
            "players": home_players,
        },
        "away": {
            "team_name": "Away Team",
            "pitcher_order": [away_starter_id],
            "batting_order": [p["player_id"] for p in away_players if "batting" in p],
            "players": away_players,
        },
    }


def _pitcher(player_id: int, name: str, ip: str, er: int, so: int, bb: int, hr: int = 0) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "pitching": {
            "inningsPitched": ip,
            "earnedRuns": er,
            "runs": er,
            "strikeOuts": so,
            "baseOnBalls": bb,
            "homeRuns": hr,
            "hits": er,
        },
    }


def _batter(player_id: int, name: str, pa: int, hits: int, walks: int, tb: int) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "batting": {
            "plateAppearances": pa,
            "hits": hits,
            "baseOnBalls": walks,
            "hitByPitch": 0,
            "strikeOuts": 1,
            "totalBases": tb,
        },
    }


def _clear_caches() -> None:
    yrfi_nrfi._PITCHER_FIRST_INNING_CACHE.clear()


class TestStarterFirstInningProfile:
    def test_unknown_pitcher_gets_league_baseline(self, tmp_path: Path) -> None:
        path = tmp_path / "snapshots.jsonl"
        _write_snapshots(path, [])
        _clear_caches()

        prof = yrfi_nrfi.starter_first_inning_profile(
            9999, datetime(2026, 5, 1, tzinfo=UTC), snapshot_path=path
        )
        assert prof["status"] == "league_baseline"
        assert prof["starts"] == 0
        assert prof["nrfi_rate"] == pytest.approx(yrfi_nrfi.LEAGUE_NRFI_PROBABILITY)

    def test_excludes_games_at_or_after_decision_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                "2026-05-01T18:00:00Z",
                100,
                200,
                [_pitcher(100, "Ace", "6.0", 0, 8, 1)],
                [_pitcher(200, "Opp", "5.0", 3, 4, 2)],
                first_away=0,
                first_home=0,
            ),
            _game(
                "2026-05-10T18:00:00Z",  # future start
                100,
                200,
                [_pitcher(100, "Ace", "1.0", 5, 0, 3)],
                [_pitcher(200, "Opp", "5.0", 1, 4, 2)],
                first_away=3,
                first_home=0,
            ),
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        prof = yrfi_nrfi.starter_first_inning_profile(
            100, datetime(2026, 5, 5, tzinfo=UTC), snapshot_path=path
        )
        assert prof["starts"] == 1
        assert prof["raw_nrfi_rate"] == 1.0  # only clean game 1 counted


class TestTop3LineupOffenseProfile:
    def test_top3_profile_aggregates_shrunk_priors(self, tmp_path: Path) -> None:
        path = tmp_path / "snapshots.jsonl"
        rows = [
            _game(
                "2026-05-01T18:00:00Z",
                100,
                200,
                [
                    _batter(1, "B1", 4, 2, 1, 4),
                    _batter(2, "B2", 4, 2, 1, 4),
                    _batter(3, "B3", 4, 1, 2, 2),
                ],
                [
                    _batter(4, "Weak1", 4, 0, 0, 0),
                    _batter(5, "Weak2", 4, 0, 0, 0),
                    _batter(6, "Weak3", 4, 0, 0, 0),
                ],
            )
        ]
        _write_snapshots(path, rows)
        _clear_caches()

        home_top3 = yrfi_nrfi.top3_lineup_offense_profile(
            "Home Team", [1, 2, 3], datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )
        away_top3 = yrfi_nrfi.top3_lineup_offense_profile(
            "Away Team", [4, 5, 6], datetime(2026, 5, 10, tzinfo=UTC), snapshot_path=path
        )

        assert home_top3["composite"] > away_top3["composite"]


class TestMLBNRFIModel:
    def test_predict_and_edge_evaluation(self, tmp_path: Path) -> None:
        path = tmp_path / "snapshots.jsonl"
        _write_snapshots(path, [])
        _clear_caches()

        model = MLBNRFIModel()
        pred = model.predict(
            "San Diego Padres",
            "Miami Marlins",
            datetime(2026, 5, 20, tzinfo=UTC),
            snapshot_path=path,
        )

        assert 0.0 < pred.p_nrfi < 1.0
        assert pred.p_nrfi + pred.p_yrfi == pytest.approx(1.0)
        assert isinstance(pred.fair_american_nrfi, int)
        assert pred.total_first_inning_expected_runs > 0

        # Market edge test
        edge_eval = model.evaluate_edge(
            pred,
            market_nrfi_american=-110,  # ~52.38%
            market_yrfi_american=-110,
        )
        assert "model_p_nrfi" in edge_eval
        assert "recommended_edge" in edge_eval

    def test_model_serialization_roundtrip(self) -> None:
        model = MLBNRFIModel(intercept=0.15, decomposed_blend_weight=0.60)
        data = model.to_dict()
        loaded = MLBNRFIModel.from_dict(data)

        assert loaded.intercept == 0.15
        assert loaded.decomposed_blend_weight == 0.60
        assert loaded.model_version == model.model_version


class TestNRFIForecastAndLedgerWiring:
    def test_forecast_mlb_nrfi_flat_and_main_ledger(self, tmp_path: Path) -> None:
        from model_prediction.cli.forecast import _forecast_mlb_nrfi_flat
        from model_prediction.domain import MarketType
        from model_prediction.ledger import PickLedger

        flat_ledger = PickLedger(tmp_path / "flat_picks.xlsx", tier="flat", sport="mlb")
        main_ledger = PickLedger(tmp_path / "main_picks.xlsx", tier="main", sport="mlb")

        class _MockESPN:
            def scoreboard(self, league: str, date: str):
                return {
                    "events": [
                        {
                            "id": "mlb_1001",
                            "date": "2026-05-20T23:05:00Z",
                            "competitions": [
                                {
                                    "competitors": [
                                        {"homeAway": "home", "team": {"displayName": "New York Yankees"}},
                                        {"homeAway": "away", "team": {"displayName": "Boston Red Sox"}},
                                    ]
                                }
                            ],
                        }
                    ]
                }

        res = _forecast_mlb_nrfi_flat(
            args_date="2026-05-20",
            log=True,
            config={},
            registry=None,
            bans=[],
            flat_ledger=flat_ledger,
            audit=None,
            main_ledger=main_ledger,
            client=_MockESPN(),
        )

        assert res["status"] == "ok"
        assert res["nrfi_candidates"] == 1
        assert res["logged"] == 1

        from model_prediction.xlsx_ledger import read_xlsx_rows

        # Verify row in flat ledger
        _, flat_rows = read_xlsx_rows(flat_ledger.path)
        assert len(flat_rows) == 1
        row = flat_rows[0]
        assert row["event_id"] == "mlb_1001"
        assert row["market_type"] == MarketType.NRFI.value
        assert row["selection"] == "nrfi"
        assert float(row["line"]) == 0.5
        assert 0.0 < float(row["model_probability"]) < 1.0

    def test_nrfi_grading_and_settlement(self) -> None:
        from model_prediction.domain import MarketType, PickResult
        from model_prediction.pricing import grade_pick

        # 0-0 in 1st inning -> NRFI wins
        assert grade_pick(MarketType.NRFI, "nrfi", 0.5, 0, 0) == PickResult.WIN
        # 1-0 in 1st inning -> NRFI loses
        assert grade_pick(MarketType.NRFI, "nrfi", 0.5, 1, 0) == PickResult.LOSS
        # 0-1 in 1st inning -> NRFI loses
        assert grade_pick(MarketType.NRFI, "nrfi", 0.5, 0, 1) == PickResult.LOSS

        # YRFI betting
        assert grade_pick(MarketType.YRFI, "yrfi", 0.5, 0, 0) == PickResult.LOSS
        assert grade_pick(MarketType.YRFI, "yrfi", 0.5, 1, 0) == PickResult.WIN
        assert grade_pick(MarketType.YRFI, "yrfi", 0.5, 1, 1) == PickResult.WIN

    def test_nrfi_polymarket_scanner_workflow(self) -> None:
        import json

        from model_prediction.portfolio.polymarket_scanner import PolymarketSlateScanner

        scanner = PolymarketSlateScanner()

        raw_line = json.dumps(
            {
                "event_id": "mlb_1001",
                "market_type": "nrfi",
                "league": "MLB",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "long": {"ask": 0.52, "bid": 0.50},
                "short": {"ask": 0.50, "bid": 0.48},
            }
        )

        parsed = scanner.parse_snapshot_line(raw_line, require_model=False)
        assert parsed is not None
        assert parsed.league == "MLB"
        assert parsed.best_ask == 0.52
        assert parsed.best_bid == 0.50
