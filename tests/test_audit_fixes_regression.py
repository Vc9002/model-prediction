"""Regression tests verifying bug fixes from in-depth model prediction audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from model_prediction.models.college_football import build_cfb_slate
from model_prediction.models.mlb_first_inning import (
    build_first_inning_ledger,
)
from model_prediction.models.mlb_first_inning_live import live_first_inning_features
from model_prediction.portfolio.auto_buyer_ledger import (
    manually_settle_auto_buyer_order,
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
    from types import SimpleNamespace

    slate = build_cfb_slate(
        data_root=tmp_path,
        game_date="2026-09-01",
        client=SimpleNamespace(scoreboard=lambda *args: {"events": []}),
        observed_at=datetime.now(UTC),
    )
    assert slate["status"] == "research"


def test_auto_executor_initialization():
    """Verify AutoPolymarketBuyer initializes and executes cleanly with no linter or runtime issues."""
    config = AutoExecutionConfig(execute_live=False)
    buyer = AutoPolymarketBuyer(config=config, live_quote_fn=lambda slug: {"ask": 0.50, "fresh": True})
    res = buyer.evaluate_and_execute(picks=[])
    assert res.total_evaluated == 0


def test_auto_buyer_totals_and_spreads_settlement_and_line_extraction(monkeypatch, tmp_path: Path):
    """Verify totals and spreads are graded accurately against extracted lines."""
    # Block real HTTP calls to PolymarketUSClient so the ESPN branch actually runs.
    # Without this, the real resolved market on Polymarket short-circuits before
    # ESPN is consulted, leaving away_score/home_score as None.
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_buyer_ledger.PolymarketUSClient",
        None,
        raising=False,
    )
    import model_prediction.data_sources.polymarket_us as _pm_mod

    monkeypatch.setattr(
        _pm_mod,
        "PolymarketUSClient",
        type("_Raise", (), {"__init__": lambda self: (_ for _ in ()).throw(RuntimeError("mocked offline"))}),
    )

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
    assert r["pnl_units"] == -0.94


def test_scheduled_auto_buyer_settlement_uses_resolved_exchange_side(tmp_path: Path):
    """Explicit data roots must not disable exchange settlement or grade by team name."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    for order_id, slug, side, selection, price in (
        ("ORD_SHORT_LOSS", "aec-cs2-alpha-beta-2026-08-30", "short", "Alpha", 0.32),
        ("ORD_SHORT_WIN", "aec-cs2-gamma-delta-2026-08-30", "short", "Gamma", 0.41),
        ("ORD_UNREACHABLE", "aec-cs2-unreachable-market-2026-08-30", "long", "Market", 0.50),
    ):
        record_auto_buy_execution(
            order_payload={
                "order_id": order_id,
                "pick_id": f"PICK_{order_id}",
                "market_slug": slug,
                "selection": selection,
                "token_side": side,
                "limit_price": price,
                "cost_usd": price,
                "shares": 1.0,
                "sport": "MLB" if order_id == "ORD_SHORT_WIN" else "CS2",
                "event_start_utc": "2026-08-30T20:00:00Z",
            },
            pick_row={
                "away_team": "Beta" if "alpha" in slug else "Delta",
                "home_team": selection,
                "market_type": "moneyline",
                "units": 1.0,
            },
            jsonl_path=j_path,
            xlsx_path=x_path,
        )

    mock_market_client = MagicMock()

    def market(slug: str) -> dict:
        # The selected team name deliberately matches outcome index 0 in both
        # markets. Settlement must instead honor the recorded short side.
        if "unreachable" in slug:
            raise httpx.ConnectError("simulated connection failure")
        prices = ["1", "0"] if "alpha-beta" in slug else ["0", "1"]
        return {
            "status": "MARKET_STATUS_RESOLVED",
            "outcomes": ["Alpha" if "alpha-beta" in slug else "Gamma", "Other"],
            "outcomePrices": prices,
        }

    mock_market_client.market.side_effect = market
    mock_espn = MagicMock()
    mock_espn.scoreboard.side_effect = httpx.ConnectError("simulated ESPN connection failure")
    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=mock_espn,
        polymarket_client=mock_market_client,
    )

    records = {row["order_id"]: row for row in read_auto_buyer_ledger(j_path)}
    assert result["settled"] == 2
    assert result["pending"] == 1
    assert result["newly_settled"] == 2
    assert result["changed"] == 2
    assert result["corrected"] == 0
    assert result["reopened"] == 0
    assert records["ORD_SHORT_LOSS"]["status"] == "settled"
    assert records["ORD_SHORT_LOSS"]["result"] == "loss"
    assert records["ORD_SHORT_LOSS"]["pnl_usd"] == -0.32
    assert records["ORD_SHORT_WIN"]["status"] == "settled"
    assert records["ORD_SHORT_WIN"]["result"] == "win"
    assert records["ORD_SHORT_WIN"]["pnl_usd"] == 0.59
    assert records["ORD_UNREACHABLE"]["result"] == "open"

    repeated = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=mock_espn,
        polymarket_client=mock_market_client,
    )
    assert repeated["settled"] == 2
    assert repeated["pending"] == 1
    assert repeated["newly_settled"] == 0
    assert repeated["changed"] == 0
    assert repeated["changes"] == []


def test_auto_buyer_settlement_pending_count_includes_ungradeable_open_rows(tmp_path: Path):
    """The response count must match the ledger's pending/open table semantics."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.write_text(
        json.dumps(
            {
                "order_id": "ORD_OPEN_BAD_START",
                "status": "submitted",
                "result": "open",
                "event_start_utc": "",
                "sport": "CS2",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = settle_auto_buyer_ledger(data_root=tmp_path, espn=MagicMock())

    assert result["pending"] == 1
    assert result["remaining_pending"] == 1
    assert result["changed"] == 0


def test_auto_buyer_settlement_survives_settled_tennis_scoreboard_failure(tmp_path: Path):
    """A transient ESPN failure must not abort or rewrite a settled tennis result."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    settled = {
        "order_id": "ORD_SETTLED_TENNIS",
        "status": "settled",
        "result": "win",
        "event_start_utc": "2026-08-30T20:00:00Z",
        "sport": "TENNIS",
        "away_team": "Away Player",
        "home_team": "Home Player",
        "selection": "home",
        "entry_price": 0.5,
        "shares": 1.0,
        "cost_usd": 0.5,
        "units": 1.0,
        "pnl_usd": 0.5,
        "pnl_units": 1.0,
    }
    j_path.write_text(json.dumps(settled) + "\n", encoding="utf-8")
    mock_espn = MagicMock()
    mock_espn.scoreboard.side_effect = httpx.ConnectError("simulated ESPN outage")

    result = settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    record = read_auto_buyer_ledger(j_path)[0]
    assert result["settled"] == 1
    assert result["remaining_pending"] == 0
    assert result["changed"] == 0
    assert record["status"] == "settled"
    assert record["result"] == "win"
    assert record["pnl_usd"] == 0.5


def test_auto_buyer_settlement_preserves_execution_time_unit_value(tmp_path: Path):
    """Changing future sizing must not restate historical P&L units."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    settled = {
        "order_id": "ORD_HISTORICAL_UNIT",
        "status": "settled",
        "result": "win",
        "event_start_utc": "2026-08-30T20:00:00Z",
        "sport": "CS2",
        "entry_price": 0.5,
        "shares": 1.0,
        "cost_usd": 0.5,
        "unit_value_usd": 1.25,
        "units": 0.4,
        "pnl_usd": 0.5,
        "pnl_units": 0.4,
    }
    j_path.write_text(json.dumps(settled) + "\n", encoding="utf-8")

    result = settle_auto_buyer_ledger(data_root=tmp_path, espn=MagicMock())

    record = read_auto_buyer_ledger(j_path)[0]
    assert result["changed"] == 0
    assert record["unit_value_usd"] == 1.25
    assert record["units"] == 0.4
    assert record["pnl_units"] == 0.4


def test_exchange_resolution_winning_side_repairs_false_push(tmp_path: Path):
    """Zero realized fields do not override the exchange's winning side."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_FALSE_PUSH",
            "pick_id": "PICK_FALSE_PUSH",
            "market_slug": "aec-cs2-home-away-2026-09-01",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.41,
            "cost_usd": 0.41,
            "shares": 1.0,
            "sport": "CS2",
            "event_start_utc": "2026-09-01T10:00:00Z",
        },
        pick_row={
            "away_team": "Away",
            "home_team": "Home",
            "market_type": "moneyline",
            "units": 1.25,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    row = read_auto_buyer_ledger(j_path)[0]
    row.update(status="settled", result="push", pnl_usd=0.0, pnl_units=0.0)
    j_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    executor = MagicMock()
    executor.portfolio_snapshot.return_value = {
        "activities": [
            {
                "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                "positionResolution": {
                    "marketSlug": "aec-cs2-home-away-2026-09-01",
                    "side": "POSITION_RESOLUTION_SIDE_SHORT",
                    "updateTime": "2026-09-01T12:30:00Z",
                    "beforePosition": {
                        "netPositionDecimal": "1.0000",
                        "cost": {"value": "0.42"},
                        "realized": {"value": "0.00"},
                    },
                    "afterPosition": {"realized": {"value": "0.00"}},
                },
            }
        ]
    }

    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=MagicMock(),
        polymarket_executor=executor,
    )

    repaired = read_auto_buyer_ledger(j_path)[0]
    assert repaired["status"] == "settled"
    assert repaired["result"] == "loss"
    assert repaired["pnl_usd"] == -0.42
    assert repaired["pnl_units"] == -0.84
    assert repaired["units"] == 0.82
    assert repaired["model_units"] == 1.25
    assert repaired["unit_value_usd"] == 0.50
    assert repaired["settled_at_utc"] == "2026-09-01T12:30:00Z"
    assert result["corrected"] == 1
    assert result["changed"] == 1


def test_neutral_resolution_side_settles_via_winning_outcome_team_name(tmp_path: Path):
    """POSITION_RESOLUTION_SIDE_NEUTRAL (observed on single-team esports tile
    markets) has no usable winning_side and a zero realized delta -- neither of
    the two existing grading checks can resolve it, so the row would stay
    pending forever without falling back to the resolution's named winning
    team (winning_outcome) compared against the row's own home/away teams."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_NEUTRAL",
            "pick_id": "PICK_NEUTRAL",
            "market_slug": "aec-cs2-ent-phtmac-2026-09-05",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.56,
            "cost_usd": 2.87,
            "shares": 5.0,
            "sport": "CS2",
            "event_start_utc": "2026-09-05T04:51:00Z",
        },
        pick_row={
            "away_team": "Phantom Academy",
            "home_team": "Entropy",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    executor = MagicMock()
    executor.portfolio_snapshot.return_value = {
        "activities": [
            {
                "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                "positionResolution": {
                    "marketSlug": "aec-cs2-ent-phtmac-2026-09-05",
                    "side": "POSITION_RESOLUTION_SIDE_NEUTRAL",
                    "updateTime": "2026-09-06T00:46:56Z",
                    "beforePosition": {
                        "netPositionDecimal": "5.0000",
                        "cost": {"value": "2.87"},
                        "realized": {"value": "0.00"},
                        "marketMetadata": {
                            "slug": "aec-cs2-ent-phtmac-2026-09-05",
                            "outcome": "Entropy",
                        },
                    },
                    "afterPosition": {"realized": {"value": "0.00"}},
                },
            }
        ]
    }

    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=MagicMock(),
        polymarket_executor=executor,
    )

    settled = read_auto_buyer_ledger(j_path)[0]
    assert settled["status"] == "settled"
    assert settled["result"] == "win"
    assert settled["pnl_usd"] == 2.13
    assert result["newly_settled"] == 1


def test_manual_sell_trade_settles_pending_position(tmp_path: Path):
    """A position closed by a manual SELL before the market resolves never
    produces a POSITION_RESOLUTION activity -- it just stops existing as an
    open position. Without reading the sell trade's own authoritative
    realizedPnl, the row would stay pending forever."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    future_event_start = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:00Z")
    record_auto_buy_execution(
        order_payload={
            "order_id": "CA2K6GXQPMVP",
            "pick_id": "PICK_SOLD",
            "market_slug": "tsc-mls-fcc-dcu-2026-09-05-2pt5",
            "selection": "over",
            "token_side": "long",
            "limit_price": 0.70,
            "cost_usd": 8.75,
            "shares": 12.5,
            "sport": "SOCCER",
            "event_start_utc": future_event_start,
        },
        pick_row={
            "away_team": "D.C. United",
            "home_team": "FC Cincinnati",
            "market_type": "total",
            "units": 1.75,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    executor = MagicMock()
    executor.portfolio_snapshot.return_value = {
        "activities": [
            {
                "type": "ACTIVITY_TYPE_TRADE",
                "trade": {
                    "marketSlug": "tsc-mls-fcc-dcu-2026-09-05-2pt5",
                    "updateTime": "2026-09-06T05:55:53Z",
                    "qtyDecimal": "12.5000",
                    "realizedPnl": {"value": "-4.73", "currency": "USD"},
                    "aggressorExecution": {
                        "order": {
                            "side": "ORDER_SIDE_SELL",
                            "price": {"value": "0.32"},
                            "marketMetadata": {"slug": "tsc-mls-fcc-dcu-2026-09-05-2pt5"},
                        }
                    },
                },
            }
        ]
    }

    # Not-yet-started (event_start is in the future relative to "now" in this
    # test), and no exchange market resolution exists either -- proving the
    # sell trade alone is enough to settle it, bypassing the usual
    # not-started pending gate.
    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=MagicMock(),
        polymarket_executor=executor,
    )

    settled = read_auto_buyer_ledger(j_path)[0]
    assert settled["status"] == "settled"
    assert settled["result"] == "loss"
    assert settled["pnl_usd"] == -4.73
    assert settled["settlement_source"] == "manual_sell"
    assert result["newly_settled"] == 1
    assert result["pending"] == 0


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
    mock_market_client = MagicMock()
    mock_market_client.market.return_value = {"status": "MARKET_STATUS_OPEN"}

    settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=mock_espn,
        polymarket_client=mock_market_client,
    )

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


def test_auto_buyer_esports_settlement_via_bo3(tmp_path: Path):
    """CS2/LOL paper positions settle from BO3 finished-match results.

    Polymarket US's own `outcomePrices` field does not reliably snap to 0/1
    for esports moneylines (observed live 2026-09-14: markets genuinely
    `MARKET_STATUS_RESOLVED` but still showing pre-close trading prices), and
    the exchange-portfolio resolution path never populates in PAPER mode --
    so before this fix, esports paper trades had no path to settle at all.
    """
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_CS2_WIN",
            "pick_id": "PICK_TEST_CS2_WIN",
            "market_slug": "aec-cs2-ent-ence-2026-09-09",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.46,
            "cost_usd": 4.60,
            "shares": 10.0,
            "sport": "CS2",
            "event_start_utc": "2026-09-09T08:00:00Z",
        },
        pick_row={
            "away_team": "ENCE",
            "home_team": "Entropy",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_CS2_LOSS",
            "pick_id": "PICK_TEST_CS2_LOSS",
            "market_slug": "aec-cs2-astra-nova-2026-09-10",
            "selection": "away",
            "token_side": "short",
            "limit_price": 0.50,
            "cost_usd": 5.00,
            "shares": 10.0,
            "sport": "CS2",
            "event_start_utc": "2026-09-10T09:00:00Z",
        },
        pick_row={
            "away_team": "Nova Squad",
            "home_team": "Astra",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    fake_teams = {
        "bo3:1:1": {"team_id": "bo3:1:1", "name": "Entropy", "slug": "entropy", "acronym": None},
        "bo3:1:2": {"team_id": "bo3:1:2", "name": "ENCE", "slug": "ence", "acronym": None},
        "bo3:1:3": {"team_id": "bo3:1:3", "name": "Astra", "slug": "astra", "acronym": None},
        "bo3:1:4": {"team_id": "bo3:1:4", "name": "Nova Squad", "slug": "nova-squad", "acronym": None},
    }
    fake_matches = [
        {
            "team1_id": "bo3:1:1",
            "team2_id": "bo3:1:2",
            "winner_id": "bo3:1:1",
            "team1_score": 2,
            "team2_score": 0,
        },
        {
            "team1_id": "bo3:1:3",
            "team2_id": "bo3:1:4",
            "winner_id": "bo3:1:3",
            "team1_score": 2,
            "team2_score": 0,
        },
    ]

    class FakeBo3Client:
        def teams(self, title):
            assert title == "cs2"
            return fake_teams, 1

        def finished_matches(self, title, from_date, to_date):
            assert title == "cs2"
            return fake_matches, 1

    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=MagicMock(),
        polymarket_client=MagicMock(market=MagicMock(return_value={"status": "MARKET_STATUS_OPEN"})),
        esports_client=FakeBo3Client(),
    )

    records = read_auto_buyer_ledger(j_path)
    r_win = next(r for r in records if r["order_id"] == "ORD_TEST_CS2_WIN")
    assert r_win["status"] == "settled"
    assert r_win["result"] == "win"
    assert r_win["home_score"] == 2
    assert r_win["away_score"] == 0
    assert r_win["pnl_usd"] == round(10.0 - 4.60, 4)

    r_loss = next(r for r in records if r["order_id"] == "ORD_TEST_CS2_LOSS")
    assert r_loss["status"] == "settled"
    assert r_loss["result"] == "loss"
    assert r_loss["pnl_usd"] == -5.00

    assert result["newly_settled"] == 2


def test_auto_buyer_settles_resolved_fair_price_market(tmp_path: Path):
    """Expired walkovers/cancellations settle at the gateway's fair price.

    Sports markets do not always resolve to binary 0/1 prices: their rules
    explicitly use the final fair price for a forfeit or cancellation. The
    paper ledger must close those rows and mark the actual mark-to-fair P&L.
    """
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_TEST_FAIR_PRICE",
            "pick_id": "PICK_TEST_FAIR_PRICE",
            "market_slug": "aec-cs2-ff-sinqu-2026-09-09",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.53,
            "cost_usd": 6.25,
            "shares": 11.79,
            "sport": "CS2",
            "event_start_utc": "2026-09-09T08:00:00Z",
        },
        pick_row={"away_team": "SINQU", "home_team": "Fire Flux Esports", "market_type": "moneyline"},
        jsonl_path=j_path,
    )

    class FakePolymarket:
        def market(self, slug):
            assert slug == "aec-cs2-ff-sinqu-2026-09-09"
            return {
                "status": "MARKET_STATUS_RESOLVED",
                "outcomePrices": '["0.54", "0.46"]',
                "description": "If the match does not begin due to a forfeit, the market will settle to the last fair market price.",
            }

    class NoMatchBo3:
        def teams(self, title):
            return {}, 0

        def finished_matches(self, title, from_date, to_date):
            return [], 0

    result = settle_auto_buyer_ledger(
        data_root=tmp_path,
        espn=MagicMock(),
        polymarket_client=FakePolymarket(),
        esports_client=NoMatchBo3(),
    )

    row = read_auto_buyer_ledger(j_path)[0]
    assert result["newly_settled"] == 1
    assert row["status"] == "settled"
    assert row["result"] == "push"
    assert row["settlement_type"] == "fair_value"
    assert row["settlement_price"] == 0.54
    assert row["settlement_source"] == "polymarket_resolved_fair_price"
    assert row["pnl_usd"] == pytest.approx(round((0.54 - 0.53) * 11.79, 4))


def test_manual_settlement_closes_ungradeable_tennis_position(tmp_path: Path):
    """An ITF/challenger-tier tennis match ESPN's scoreboard never carries
    stays `open` forever via the automated path -- confirmed live 2026-09-14
    against a real stuck position (neither player found in ESPN's WTA/ATP
    scoreboards for any nearby date). The manual override is the only path
    to close it out, and requires an explicit reason and non-settled state.
    """
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_ITF_TENNIS",
            "pick_id": "PICK_ITF_TENNIS",
            "market_slug": "aec-wta-alasmi-anatik-2026-09-12",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.39,
            "cost_usd": 8.75,
            "shares": 22.44,
            "sport": "TENNIS",
            "event_start_utc": "2026-09-12T16:00:00Z",
        },
        pick_row={
            "away_team": "Anastasia Tikhonova",
            "home_team": "Alana Smith",
            "market_type": "moneyline",
            "units": 1.75,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    result = manually_settle_auto_buyer_order(
        result="win",
        data_root=tmp_path,
        order_id="ORD_ITF_TENNIS",
        home_score=2,
        away_score=0,
        reason="Verified resolved on Polymarket's own market page: Alana Smith won.",
    )
    assert result["result"] == "win"
    assert result["pnl_usd"] == round(22.44 - 8.75, 4)

    records = read_auto_buyer_ledger(j_path)
    row = next(r for r in records if r["order_id"] == "ORD_ITF_TENNIS")
    assert row["status"] == "settled"
    assert row["result"] == "win"
    assert row["settlement_source"] == "manual_operator"
    assert row["manual_settlement_reason"]

    # Guardrails: no reason, no target, and already-settled all refuse.
    try:
        manually_settle_auto_buyer_order(
            result="win", data_root=tmp_path, order_id="ORD_ITF_TENNIS", reason=""
        )
        raise AssertionError("expected ValueError for missing reason")
    except ValueError:
        pass
    try:
        manually_settle_auto_buyer_order(result="win", data_root=tmp_path, order_id="NOPE", reason="x")
        raise AssertionError("expected KeyError for unknown order")
    except KeyError:
        pass
    try:
        manually_settle_auto_buyer_order(
            result="win", data_root=tmp_path, order_id="ORD_ITF_TENNIS", reason="already settled, try again"
        )
        raise AssertionError("expected ValueError for already-settled order")
    except ValueError:
        pass


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


def test_settlement_skips_rows_with_unreconciled_fallback_or_unknown_fill(tmp_path: Path):
    """Rows whose real fill hasn't been confirmed must not be settled on their
    provisional `shares`/`cost_usd`, even when the underlying game is complete --
    settling early would compute real win/loss P&L against a phantom position."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_UNRECONCILED_FALLBACK",
            "pick_id": "PICK_UNRECONCILED",
            "market_slug": "tsc-mlb-away-home-2026-08-30",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.50,
            "cost_usd": 0.44,
            "shares": 0.88,
            "sport": "MLB",
            "event_start_utc": "2026-08-30T20:07:00Z",
            "fallback_order_id": "RESTING_ORDER_1",
            "fallback_resting_shares": 0.12,
        },
        pick_row={"away_team": "Away", "home_team": "Home", "market_type": "moneyline", "units": 1.0},
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_UNKNOWN_FILL",
            "pick_id": "PICK_UNKNOWN",
            "market_slug": "tsc-mlb-away-home-2026-08-30-b",
            "selection": "away",
            "token_side": "short",
            "limit_price": 0.50,
            "cost_usd": 0.50,
            "shares": 1.0,
            "sport": "MLB",
            "event_start_utc": "2026-08-30T20:07:00Z",
            "fill_known": False,
        },
        pick_row={"away_team": "Away", "home_team": "Home", "market_type": "moneyline", "units": 1.0},
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    mock_espn = MagicMock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "1"},
                            {"homeAway": "home", "team": {"displayName": "Home"}, "score": "3"},
                        ],
                    }
                ]
            }
        ]
    }

    result = settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    records = {r["order_id"]: r for r in read_auto_buyer_ledger(j_path)}
    assert records["ORD_UNRECONCILED_FALLBACK"]["status"] == "open"
    assert records["ORD_UNRECONCILED_FALLBACK"]["result"] == "open"
    assert records["ORD_UNKNOWN_FILL"]["status"] == "open"
    assert records["ORD_UNKNOWN_FILL"]["result"] == "open"
    assert result["settled"] == 0
    assert result["pending"] == 2


def test_settlement_does_not_resurrect_voided_zero_fill_rows(tmp_path: Path):
    """A voided row (confirmed zero-fill primary) must never be re-graded from
    a completed game's score -- there was never a real position to grade, and
    the falsy-zero `shares`/`cost_usd` defaulting previously fabricated a
    phantom win/loss on the very next settle cycle."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_VOIDED",
            "pick_id": "PICK_VOIDED",
            "market_slug": "tsc-mlb-away-home-2026-08-30-c",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.42,
            "cost_usd": 0.0,
            "shares": 0.0,
            "sport": "MLB",
            "event_start_utc": "2026-08-30T20:07:00Z",
        },
        pick_row={"away_team": "Away", "home_team": "Home", "market_type": "moneyline", "units": 1.0},
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    records = read_auto_buyer_ledger(j_path)
    records[0]["status"] = "settled"
    records[0]["result"] = "void"
    with j_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    mock_espn = MagicMock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "1"},
                            {"homeAway": "home", "team": {"displayName": "Home"}, "score": "3"},
                        ],
                    }
                ]
            }
        ]
    }

    settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    record = read_auto_buyer_ledger(j_path)[0]
    assert record["result"] == "void"
    assert record["shares"] == 0.0
    assert record["cost_usd"] == 0.0
    assert record["pnl_usd"] == 0.0


def test_settlement_falsy_zero_shares_not_treated_as_default(tmp_path: Path):
    """`shares == 0.0` on a settled record must stay 0.0 in settlement math,
    not silently become 1.0 via `or`-chained defaulting."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"

    record_auto_buy_execution(
        order_payload={
            "order_id": "ORD_ZERO_SHARES",
            "pick_id": "PICK_ZERO",
            "market_slug": "tsc-mlb-away-home-2026-08-30-d",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.42,
            "cost_usd": 0.0,
            "shares": 0.0,
            "sport": "MLB",
            "event_start_utc": "2026-08-30T20:07:00Z",
        },
        pick_row={"away_team": "Away", "home_team": "Home", "market_type": "moneyline", "units": 1.0},
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    records = read_auto_buyer_ledger(j_path)
    records[0]["status"] = "settled"
    records[0]["result"] = "win"
    with j_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    mock_espn = MagicMock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "1"},
                            {"homeAway": "home", "team": {"displayName": "Home"}, "score": "3"},
                        ],
                    }
                ]
            }
        ]
    }

    settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)

    record = read_auto_buyer_ledger(j_path)[0]
    # With genuinely 0 shares, a "win" can only ever be worth $0 -- never the
    # $0.58 that `shares=1.0` (the falsy-zero default) would fabricate.
    assert record["pnl_usd"] == 0.0
