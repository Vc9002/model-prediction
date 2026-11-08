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
