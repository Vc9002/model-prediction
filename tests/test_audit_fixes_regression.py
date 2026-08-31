"""Regression tests verifying bug fixes from in-depth model prediction audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from model_prediction.models.college_football import build_cfb_slate
from model_prediction.models.mlb_first_inning import (
    build_first_inning_ledger,
)
from model_prediction.models.mlb_first_inning_live import live_first_inning_features
from model_prediction.portfolio.auto_buyer_ledger import (
    read_auto_buyer_ledger,
    record_auto_buy_execution,
    settle_auto_buyer_ledger,
)
from model_prediction.portfolio.auto_executor import AutoExecutionConfig, AutoPolymarketBuyer


def test_starter_opponent_runs_mapping_correctness(tmp_path: Path):
    """Verify away starter receives home team 1st inning runs and home starter receives away team runs."""
    fake_snapshots = [
        {
            "game_pk": 1001,
            "game_start_utc": "2026-05-01T19:05:00Z",
            "venue_name": "Yankee Stadium",
            "first_inning_runs_home": 2.0,  # Yankees (home) scored 2 runs in bottom 1st against Red Sox starter
            "first_inning_runs_away": 0.0,  # Red Sox (away) scored 0 runs in top 1st against Yankees starter
            "yrfi": 1,
            "home": {
                "team_name": "New York Yankees",
                "pitcher_order": [202],
                "batting_order": [2001, 2002, 2003],
                "players": [
                    {
                        "player_id": 202,
                        "name": "Home Pitcher",
                        "pitch_hand": "L",
                        "pitching": {
                            "inningsPitched": "1.0",
                            "strikeOuts": 2,
                            "baseOnBalls": 0,
                            "battersFaced": 3,
                            "homeRuns": 0,
                        },
                    }
                ],
            },
            "away": {
                "team_name": "Boston Red Sox",
                "pitcher_order": [101],
                "batting_order": [1001, 1002, 1003],
                "players": [
                    {
                        "player_id": 101,
                        "name": "Away Pitcher",
                        "pitch_hand": "R",
                        "pitching": {
                            "inningsPitched": "1.0",
                            "strikeOuts": 1,
                            "baseOnBalls": 1,
                            "battersFaced": 5,
                            "homeRuns": 1,
                        },
                    }
                ],
            },
        },
        {
            "game_pk": 1002,
            "game_start_utc": "2026-05-02T19:05:00Z",
            "venue_name": "Yankee Stadium",
            "first_inning_runs_home": 1.0,
            "first_inning_runs_away": 0.0,
            "yrfi": 1,
            "home": {
                "team_name": "New York Yankees",
                "pitcher_order": [202],
                "batting_order": [2001, 2002, 2003],
                "players": [
                    {
                        "player_id": 202,
                        "name": "Home Pitcher",
                        "pitch_hand": "L",
                        "pitching": {
                            "inningsPitched": "1.0",
                            "strikeOuts": 1,
                            "baseOnBalls": 0,
                            "battersFaced": 3,
                            "homeRuns": 0,
                        },
                    }
                ],
            },
            "away": {
                "team_name": "Boston Red Sox",
                "pitcher_order": [101],
                "batting_order": [1001, 1002, 1003],
                "players": [
                    {
                        "player_id": 101,
                        "name": "Away Pitcher",
                        "pitch_hand": "R",
                        "pitching": {
                            "inningsPitched": "1.0",
                            "strikeOuts": 1,
                            "baseOnBalls": 0,
                            "battersFaced": 4,
                            "homeRuns": 0,
                        },
                    }
                ],
            },
        },
    ]

    snap_file = tmp_path / "game_snapshots.jsonl"
    with snap_file.open("w", encoding="utf-8") as f:
        for s in fake_snapshots:
            f.write(json.dumps(s) + "\n")

    # Symmetric priors to isolate starter accumulator behavior
    priors = {
        "mean_total": 0.50,
        "yrfi_rate": 0.50,
        "half_home": 0.25,
        "half_away": 0.25,
        "total": 0.50,
        "fip": 4.0,
        "k_pct": 0.22,
        "bb_pct": 0.08,
        "prod": 0.30,
        "disc": 0.05,
        "pow": 0.15,
        "same_hand": 0.55,
        "n_games": 2,
    }

    rows = build_first_inning_ledger(snap_file, priors=priors)
    assert len(rows) == 2

    # In game 1002, the features use accumulators after game 1001:
    # Starter 101 (Away SP) in game 1001 allowed 2 runs (runs_1st_home) -> raw 2.0 shrunk against 0.25 is > 0.25
    # Starter 202 (Home SP) in game 1001 allowed 0 runs (runs_1st_away) -> raw 0.0 shrunk against 0.25 is < 0.25
    row2 = rows[1]
    assert row2.features["away_starter_opp_1st_runs"] > row2.features["home_starter_opp_1st_runs"]

    # Also check live feature extraction with snapshot file and symmetric priors
    live_feats = live_first_inning_features(
        home_team="New York Yankees",
        away_team="Boston Red Sox",
        venue_name="Yankee Stadium",
        home_starter_name="Home Pitcher",
        away_starter_name="Away Pitcher",
        decision=datetime(2026, 5, 3, tzinfo=UTC),
        snapshot_path=snap_file,
        priors=priors,
    )
    # After both games (1001: 2 runs, 1002: 1 run): Away SP allowed runs_1st_home (3 runs total), Home SP allowed runs_1st_away (0 runs total)
    assert live_feats["away_starter_opp_1st_runs"] > live_feats["home_starter_opp_1st_runs"]


def test_cfb_slate_default_status_is_research(tmp_path: Path):
    """Verify build_cfb_slate returns status 'research'."""
    slate = build_cfb_slate(
        data_root=tmp_path,
        game_date="2026-09-01",
        observed_at=datetime.now(UTC),
    )
    assert slate["status"] == "research"


def test_auto_executor_initialization():
    """Verify AutoPolymarketBuyer initializes and executes cleanly with no linter or runtime issues."""
    config = AutoExecutionConfig(execute_live=False)
    buyer = AutoPolymarketBuyer(config=config, live_quote_fn=lambda slug: {"ask": 0.50, "fresh": True})
    res = buyer.evaluate_and_execute(picks=[])
    assert res.total_evaluated == 0


def test_auto_buyer_totals_and_spreads_settlement_and_line_extraction(tmp_path: Path):
    """Verify totals and spreads are graded accurately against extracted lines."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    # Record total over 8.5 on 5-2 final score (Total 7 < 8.5 => LOSS)
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_TOTAL",
            "pick_id": "PICK_TEST_TOTAL",
            "market_slug": "tsc-mlb-phi-laa-2026-08-30-8pt5",
            "selection": "over",
            "token_side": "long",
            "limit_price": 0.47,
            "cost_usd": 0.47,
            "shares": 1.0,
            "sport": "MLB",
            "event_start_utc": "2026-08-30T20:07:00Z",
        },
        pick_row={
            "away_team": "Philadelphia Phillies",
            "home_team": "Los Angeles Angels",
            "market_type": "total",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    # Mock ESPN scoreboard
    mock_espn = MagicMock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {
                                "homeAway": "away",
                                "team": {"displayName": "Philadelphia Phillies"},
                                "score": "5",
                            },
                            {"homeAway": "home", "team": {"displayName": "Los Angeles Angels"}, "score": "2"},
                        ],
                    }
                ]
            }
        ]
    }

    settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    records = read_auto_buyer_ledger(j_path)
    assert len(records) == 1
    r = records[0]
    assert r["status"] == "settled"
    assert r["result"] == "loss"
    assert r["away_score"] == 5
    assert r["home_score"] == 2
    assert r["line"] == 8.5
    assert r["pnl_usd"] == -0.47
    assert r["pnl_units"] == -1.0


def test_auto_buyer_tennis_settlement_and_scheduled_reversion(tmp_path: Path):
    """Verify tennis matches settle correctly and uncompleted matches revert to open status."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    # Match 1: Completed match with Home winning (Bublik won)
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_TENNIS_COMPLETED",
            "pick_id": "PICK_TEST_TENNIS_1",
            "market_slug": "aec-atp-alebub-jjwol-2026-08-30",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.69,
            "cost_usd": 0.69,
            "shares": 1.0,
            "sport": "TENNIS",
            "event_start_utc": "2026-08-30T20:00:00Z",
        },
        pick_row={
            "away_team": "J.J. Wolf",
            "home_team": "Alexander Bublik",
            "market_type": "moneyline",
            "units": 1.5,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    # Match 2: Scheduled match not yet played (Marcinko vs Birrell)
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_TENNIS_SCHEDULED",
            "pick_id": "PICK_TEST_TENNIS_2",
            "market_slug": "aec-wta-kimbir-petmar-2026-08-30",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.51,
            "cost_usd": 0.51,
            "shares": 1.0,
            "sport": "TENNIS",
            "event_start_utc": "2026-08-30T20:30:00Z",
        },
        pick_row={
            "away_team": "Petra Marcinko",
            "home_team": "Kimberly Birrell",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    # Mock ESPN scoreboard
    mock_espn = MagicMock()

    def mock_scoreboard(tour: str, date_str: str):
        t_upper = tour.upper()
        if t_upper == "ATP":
            return {
                "events": [
                    {
                        "groupings": [
                            {
                                "competitions": [
                                    {
                                        "status": {"type": {"completed": True}},
                                        "competitors": [
                                            {"athlete": {"displayName": "J.J. Wolf"}, "winner": False},
                                            {"athlete": {"displayName": "Alexander Bublik"}, "winner": True},
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        elif t_upper == "WTA":
            return {
                "events": [
                    {
                        "groupings": [
                            {
                                "competitions": [
                                    {
                                        "status": {"type": {"completed": False}},
                                        "competitors": [
                                            {"athlete": {"displayName": "Petra Marcinko"}, "winner": None},
                                            {"athlete": {"displayName": "Kimberly Birrell"}, "winner": None},
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        return {"events": []}

    mock_espn.scoreboard.side_effect = mock_scoreboard

    settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    records = read_auto_buyer_ledger(j_path)
    assert len(records) == 2
    r_bublik = next(r for r in records if r["order_id"] == "ORD_TEST_TENNIS_COMPLETED")
    assert r_bublik["status"] == "settled"
    assert r_bublik["result"] == "win"
    assert r_bublik["pnl_usd"] == 0.31
    assert r_bublik["away_score"] == 0
    assert r_bublik["home_score"] == 1

    r_birrell = next(r for r in records if r["order_id"] == "ORD_TEST_TENNIS_SCHEDULED")
    assert r_birrell["status"] in ("open", "submitted", "filled")
    assert r_birrell["result"] == "open"
    assert r_birrell["pnl_usd"] == 0.0


def test_wnba_spread_margin_v2_model_and_forecast(tmp_path: Path):
    """Verify wnba-spread-margin-v2 artifact, registry resolution, and BasketballModel execution."""
    from model_prediction.cli.forecast import _load_exact_artifact_contract
    from model_prediction.models.basketball import BasketballModel, UpcomingGame
    from model_prediction.portfolio.auto_executor import DEFAULT_WHITELIST_MODELS, EXPLICIT_BLACKLIST_MODELS
    from model_prediction.production_registry import ProductionModelRegistry

    # 1. Exact Artifact Contract Verification
    artifact, err = _load_exact_artifact_contract("wnba-spread-margin-v2")
    assert err is None, f"Artifact failed to load: {err}"
    assert artifact is not None
    assert artifact["model_version"] == "wnba-spread-margin-v2"
    assert artifact["margin_sd"] == 13.37
    assert artifact["qualification"]["qualified"] is True

    # 2. Production Registry Resolution
    registry = ProductionModelRegistry.load(Path("."))
    champ = registry.champion("WNBA", "spread")
    assert champ.model_id == "wnba-spread-margin-v2"
    assert champ.available is True
    assert "wnba-spread-margin-v2" not in registry.blocked_workflows

    # 3. BasketballModel Composite Forecast
    model = BasketballModel(
        sport="wnba",
        version="wnba-spread-margin-v2",
        margin_sd=13.37,
        total_sd=15.0,
        league="WNBA",
        elo_weight=0.52,
        trend_weight=0.44,
        rest_weight=0.20,
        home_court_points=2.26,
    )
    upcoming = [
        UpcomingGame(
            event_id="wnba-test-1",
            event_start_utc="2026-08-31T23:00:00Z",
            away_team="Minnesota Lynx",
            home_team="Atlanta Dream",
            spread_away_line=-1.5,
        )
    ]
    preds = model.predict_games(history=[], upcoming=upcoming)
    assert len(preds) == 2  # moneyline + spread
    spread_p = next(p for p in preds if p.market_type == "spread")
    assert spread_p.probabilities["away"] > 0
    assert spread_p.probabilities["home"] > 0
    assert round(spread_p.probabilities["away"] + spread_p.probabilities["home"], 5) == 1.0

    # 4. Auto-buyer Whitelist/Blacklist
    assert "wnba-spread-margin-v2" in DEFAULT_WHITELIST_MODELS
    assert "wnba-spread-margin-v1" in EXPLICIT_BLACKLIST_MODELS


def test_wnba_total_margin_v2_model_and_forecast():
    """Verify wnba-total-margin-v2 artifact hash, production registry, BasketballModel and auto_executor."""
    from model_prediction.models.basketball import BasketballModel, UpcomingGame
    from model_prediction.portfolio.auto_executor import DEFAULT_WHITELIST_MODELS, EXPLICIT_BLACKLIST_MODELS
    from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash

    # 1. Artifact Hash Verification
    artifact_path = Path("config/models/wnba-total-margin-v2.json")
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    computed_hash = compute_artifact_hash(payload)
    assert embedded_hash == computed_hash
    assert payload["total_sd"] == 16.0
    assert payload["trend_total_weight"] == 0.60
    assert payload["team_total_weight"] == 0.40

    # 2. Production Registry Resolution
    registry = ProductionModelRegistry.load(Path("."))
    champ = registry.champion("WNBA", "total")
    assert champ.model_id == "wnba-total-margin-v2"
    assert champ.available is True
    assert "wnba-total-margin-v2" not in registry.blocked_workflows

    # 3. BasketballModel Composite Total Forecast
    model = BasketballModel(
        sport="wnba",
        version="wnba-total-margin-v2",
        margin_sd=13.37,
        total_sd=16.0,
        league="WNBA",
        trend_total_weight=0.60,
        team_total_weight=0.40,
    )
    upcoming = [
        UpcomingGame(
            event_id="wnba-total-test-1",
            event_start_utc="2026-08-31T23:00:00Z",
            away_team="Minnesota Lynx",
            home_team="Atlanta Dream",
            total_line=162.5,
        )
    ]
    preds = model.predict_games(history=[], upcoming=upcoming)
    assert len(preds) == 2  # moneyline + total
    total_p = next(p for p in preds if p.market_type == "total")
    assert total_p.probabilities["over"] > 0
    assert total_p.probabilities["under"] > 0
    assert round(total_p.probabilities["over"] + total_p.probabilities["under"], 5) == 1.0

    # 4. Auto-buyer Whitelist/Blacklist
    assert "wnba-total-margin-v2" in DEFAULT_WHITELIST_MODELS
    assert "wnba-total-margin-v1" in EXPLICIT_BLACKLIST_MODELS
