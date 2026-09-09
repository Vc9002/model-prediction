"""Unit tests for the manual bet ledger (portfolio/manual_bet_ledger.py)."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock

import pytest

from model_prediction.dashboard.routes import Handler
from model_prediction.portfolio.manual_bet_ledger import (
    get_manual_bankroll,
    mark_manual_bet_result,
    read_manual_bets,
    record_manual_bet,
    set_manual_bankroll,
    settle_manual_bets,
    sync_manual_bets_from_polymarket,
)

VALID_BET = {
    "league": "MLB",
    "away_team": "New York Yankees",
    "home_team": "Boston Red Sox",
    "market_type": "moneyline",
    "selection": "home",
    "entry_price": 0.55,
    "stake_usd": 15.0,
    "event_start_utc": "2026-08-20T17:00:00Z",
    "sportsbook": "polymarket",
    "note": "Value on the road dog price",
}


def test_record_manual_bet_writes_row(tmp_path):
    res = record_manual_bet(VALID_BET, data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["stake_usd"] == 15.0

    rows = read_manual_bets(data_root=tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["league"] == "MLB"
    assert row["status"] == "open"
    assert float(row["units"]) == 15.0
    assert row["model_probability"] == ""
    assert row["record_type"] == "manual_bet"


def test_record_manual_bet_rejects_missing_fields(tmp_path):
    bad = dict(VALID_BET)
    del bad["home_team"]
    with pytest.raises(ValueError, match="required"):
        record_manual_bet(bad, data_root=tmp_path)


def test_record_manual_bet_rejects_bad_price_and_stake(tmp_path):
    with pytest.raises(ValueError, match="entry_price"):
        record_manual_bet({**VALID_BET, "entry_price": 1.5}, data_root=tmp_path)
    with pytest.raises(ValueError, match="stake_usd"):
        record_manual_bet({**VALID_BET, "stake_usd": -5}, data_root=tmp_path)


def test_record_manual_bet_deduplicates(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    res = record_manual_bet(VALID_BET, data_root=tmp_path)
    assert res["status"] == "duplicate"
    assert len(read_manual_bets(data_root=tmp_path)) == 1


def test_settle_manual_bets_grades_against_espn(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)

    mock_espn = Mock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "status": {"type": {"completed": True}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Boston Red Sox"}, "score": 5},
                            {"homeAway": "away", "team": {"displayName": "New York Yankees"}, "score": 3},
                        ]
                    }
                ],
            }
        ]
    }

    res = settle_manual_bets(data_root=tmp_path, espn_client=mock_espn)
    assert res["status"] == "ok"
    assert res["settled_count"] == 1

    rows = read_manual_bets(data_root=tmp_path)
    assert rows[0]["status"] == "settled"
    assert rows[0]["result"] == "win"
    assert float(rows[0]["pnl_units"]) > 0


def test_mark_manual_bet_result_override(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    rows = read_manual_bets(data_root=tmp_path)
    pick_id = rows[0]["pick_id"]

    res = mark_manual_bet_result(pick_id, "loss", data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["pnl_usd"] == -15.0

    rows = read_manual_bets(data_root=tmp_path)
    assert rows[0]["status"] == "settled"
    assert rows[0]["result"] == "loss"
    assert rows[0]["void_reason"] == "manual_override"


def test_mark_manual_bet_result_rejects_bad_result(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    rows = read_manual_bets(data_root=tmp_path)
    with pytest.raises(ValueError, match="win/loss/push"):
        mark_manual_bet_result(rows[0]["pick_id"], "maybe", data_root=tmp_path)


def test_mark_manual_bet_result_unknown_pick_id(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    with pytest.raises(ValueError, match="no manual bet found"):
        mark_manual_bet_result("does-not-exist", "win", data_root=tmp_path)


def test_bankroll_defaults_and_round_trip(tmp_path):
    assert get_manual_bankroll(data_root=tmp_path) == 350.0

    res = set_manual_bankroll(500, data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["bankroll_usd"] == 500.0
    assert get_manual_bankroll(data_root=tmp_path) == 500.0


def test_set_manual_bankroll_rejects_invalid(tmp_path):
    with pytest.raises(ValueError):
        set_manual_bankroll(-10, data_root=tmp_path)
    with pytest.raises(ValueError):
        set_manual_bankroll("not-a-number", data_root=tmp_path)


def test_dashboard_manual_bets_get_endpoint():
    handler = Handler.__new__(Handler)
    handler.path = "/api/manual-bets"
    handler.headers = {}
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_GET()

    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "rows" in data
    assert "bankroll_usd" in data
    assert "open_exposure_pct" in data
    assert isinstance(data["rows"], list)


def test_dashboard_manual_bets_record_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "model_prediction.dashboard.routes.record_manual_bet",
        lambda bet: record_manual_bet(bet, data_root=tmp_path),
    )
    body = json.dumps({"confirm": True, "bet": VALID_BET}).encode()
    handler = Handler.__new__(Handler)
    handler.path = "/api/manual-bets/record"
    handler.headers = {"Content-Length": str(len(body)), "X-Dashboard-Token": _dashboard_token()}
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_POST()

    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert data["status"] == "ok"
    assert len(read_manual_bets(data_root=tmp_path)) == 1


def _dashboard_token() -> str:
    from model_prediction.dashboard.common import _DASHBOARD_TOKEN

    return _DASHBOARD_TOKEN


def test_sync_manual_bets_from_polymarket_imports_unmatched_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_buyer_ledger.read_auto_buyer_ledger",
        lambda jsonl_path=None: [{"market_slug": "aec-cs2-known-auto-2026-09-05"}],
    )

    class FakeExecutor:
        def portfolio_snapshot(self):
            return {
                "positions": {
                    "aec-cs2-known-auto-2026-09-05": {
                        "netPosition": "10",
                        "cost": {"value": "5.0"},
                        "marketMetadata": {"title": "A vs. B", "outcome": "A", "team": {"league": "cs2"}},
                        "updateTime": "2026-09-04T20:00:00Z",
                    },
                    "asc-cfb-liub-kan-2026-09-04-pos-40pt5": {
                        "netPosition": "-29",
                        "cost": {"value": "17.499"},
                        "marketMetadata": {
                            "title": "LIU vs. Kansas",
                            "outcome": "-40.50",
                            "eventSlug": "cfb-liub-kan-2026-09-04",
                            "team": {"league": "cfb"},
                        },
                        "updateTime": "2026-09-04T18:31:32Z",
                    },
                }
            }

    res = sync_manual_bets_from_polymarket(data_root=tmp_path, executor=FakeExecutor())
    assert res["status"] == "ok"
    assert res["synced_count"] == 1
    assert res["synced_slugs"] == ["asc-cfb-liub-kan-2026-09-04-pos-40pt5"]

    rows = read_manual_bets(data_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["league"] == "NCAAF"
    assert rows[0]["market_type"] == "spread"
    assert rows[0]["selection"] == "home"
    assert float(rows[0]["line"]) == -40.5
    assert rows[0]["event_id"] == "asc-cfb-liub-kan-2026-09-04-pos-40pt5"
    assert rows[0]["event_start_utc"] == "2026-09-04T12:00:00Z"

    # Re-running the sync must not duplicate the already-synced position.
    res2 = sync_manual_bets_from_polymarket(data_root=tmp_path, executor=FakeExecutor())
    assert res2["synced_count"] == 0
    assert res2["skipped_existing"] == 1
    assert len(read_manual_bets(data_root=tmp_path)) == 1
