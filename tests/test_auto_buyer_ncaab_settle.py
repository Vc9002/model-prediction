import json
from pathlib import Path
from unittest.mock import MagicMock

from model_prediction.portfolio.auto_buyer_ledger import settle_auto_buyer_ledger


def test_settle_auto_buyer_ledger_grades_ncaab_from_espn(tmp_path: Path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"

    record = {
        "order_id": "PAPER-test-cbb-1",
        "market_slug": "tsc-cbb-cencon-dart-2026-11-08-140pt5",
        "sport": "NCAAB",
        "market_type": "total",
        "line": 140.5,
        "selection": "over",
        "token_side": "long",
        "away_team": "Central Connecticut Blue Devils",
        "home_team": "Dartmouth Big Green",
        "event_start_utc": "2026-11-08T19:00:00Z",
        "executed_at_utc": "2026-11-08T18:40:00Z",
        "shares": 10.0,
        "entry_price": 0.50,
        "cost_usd": 5.0,
        "unit_value_usd": 5.0,
        "units": 1.0,
        "status": "open",
        "result": "open",
        "fill_known": True,
    }
    j_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

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
                                "team": {"displayName": "Central Connecticut Blue Devils"},
                                "score": "78",
                            },
                            {
                                "homeAway": "home",
                                "team": {"displayName": "Dartmouth Big Green"},
                                "score": "71",
                            },
                        ],
                    }
                ]
            }
        ]
    }

    res = settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)
    assert res["newly_settled"] == 1
    assert res["changes"][0]["to_result"] == "win"
    assert res["changes"][0]["pnl_usd"] == 5.0
    settled_bytes = j_path.read_bytes()
    second = settle_auto_buyer_ledger(data_root=tmp_path, espn=mock_espn)
    assert second["newly_settled"] == 0
    assert second["changes"] == []
    assert j_path.read_bytes() == settled_bytes


def test_settlement_refuses_shared_words_and_multiple_games(tmp_path: Path):
    row = {
        "order_id": "PAPER-ambiguous",
        "market_slug": "test-cbb",
        "sport": "NCAAB",
        "market_type": "moneyline",
        "selection": "away",
        "away_team": "Kansas State Wildcats",
        "home_team": "Kansas Jayhawks",
        "event_start_utc": "2026-11-08T19:00:00Z",
        "shares": 1,
        "entry_price": 0.5,
        "cost_usd": 0.5,
        "status": "open",
        "result": "open",
        "fill_known": True,
    }
    path = tmp_path / "auto_buyer_ledger.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def event(event_id, away, home):
        return {
            "id": event_id,
            "competitions": [
                {
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"homeAway": "away", "team": {"displayName": away}, "score": "90"},
                        {"homeAway": "home", "team": {"displayName": home}, "score": "80"},
                    ],
                }
            ],
        }

    espn = MagicMock()
    espn.scoreboard.return_value = {
        "events": [event("wrong", "Oklahoma State Cowboys", "Kansas State Wildcats")]
    }
    assert settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)["newly_settled"] == 0

    espn.scoreboard.return_value = {
        "events": [
            event("one", "Kansas State Wildcats", "Kansas Jayhawks"),
            event("two", "Kansas State Wildcats", "Kansas Jayhawks"),
        ]
    }
    assert settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)["newly_settled"] == 0
