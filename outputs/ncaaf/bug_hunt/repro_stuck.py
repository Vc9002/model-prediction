"""Dry-run settlement's ESPN match + grade + PnL for given open pick_ids. Read-only (no ledger writes)."""

import json
import sqlite3
import sys

sys.path.insert(0, "E:/MP/src")
from model_prediction.cli.settle import _find_espn_result  # noqa: E402
from model_prediction.cli.state import _LEDGER_LEAGUE_TO_ESPN  # noqa: E402
from model_prediction.data_sources.espn import ESPNClient  # noqa: E402
from model_prediction.domain import EASTERN, MarketType, PickResult, parse_utc  # noqa: E402
from model_prediction.ledger import _settlement_pnl  # noqa: E402
from model_prediction.pricing import grade_pick  # noqa: E402

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
espn = ESPNClient()
cache: dict = {}
for pid in sys.argv[1:]:
    st, payload = con.execute(
        "select status,decision_payload_json from ledger_records where pick_id=? and status!='superseded'", (pid,)
    ).fetchone()
    row = json.loads(payload)
    start = parse_utc(row["event_start_utc"])
    game_day = start.astimezone(EASTERN).date().isoformat()
    leagues = _LEDGER_LEAGUE_TO_ESPN.get(row["league"], ())
    match = _find_espn_result(espn, leagues, game_day, row, scoreboard_cache=cache)
    print(pid, st, row["league"], leagues, game_day, "match:", match)
    if match and match.get("completed"):
        line = float(row["decision_line"] or row["line"]) if (row["decision_line"] or row["line"]) else None
        res = grade_pick(MarketType(row["market_type"]), row["selection"], line,
                         int(match["away_score"]), int(match["home_score"]), league=row["league"])
        raw_entry = (row.get("market_probability_at_decision") or row["decision_raw_implied_probability"]
                     or row["market_implied_probability"])
        try:
            pnl = _settlement_pnl(result=res, units=float(row["units"]), sportsbook=row.get("sportsbook"),
                                  decimal_odds=float(row["decision_decimal_odds"] or 0),
                                  entry_probability=float(raw_entry), binary_contract_settlement_value=None)
        except Exception as e:  # noqa: BLE001
            pnl = f"ERR {e}"
        print("   grade", res, "pnl", pnl, res is PickResult.WIN)
