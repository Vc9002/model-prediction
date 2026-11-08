"""Backtest NFL v7 challengers vs v5, restricted to our own ledger's settled games.

Unlike validate_nfl_v7_models.py (full 2023-2026 nflverse universe), this
script filters the nflverse schedule down to ONLY the games our own
SQLite ledger (ledger_records, sport='nfl', status='settled', market_type
in spread/total) has actually graded, matched by (home_team, away_team,
gameday). Read-only research; no production/config writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import polars as pl

from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_structural_v5 import NFLStructuralV5Engine
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"
LEDGER_DB = r"E:\model-prediction-runtime\ledgers\ledgers.db"
MKT_ASK = 0.5238
MIN_EDGE = 0.035

TEAM_CODE = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Los Angeles Rams": "LA",
    "Los Angeles Chargers": "LAC",
    "Las Vegas Raiders": "LV",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "Seattle Seahawks": "SEA",
    "San Francisco 49ers": "SF",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}


def _new_bucket():
    return {"picks": 0, "wins": 0, "losses": 0, "pushes": 0, "pnl": 0.0}


def _settle(bucket, outcome):
    bucket["picks"] += 1
    if outcome == 1.0:
        bucket["wins"] += 1
        bucket["pnl"] += 0.9091
    elif outcome == 0.0:
        bucket["losses"] += 1
        bucket["pnl"] -= 1.0
    else:
        bucket["pushes"] += 1


def load_ledger_games() -> dict[tuple[str, str, str], dict]:
    conn = sqlite3.connect(LEDGER_DB)
    c = conn.cursor()
    c.execute(
        """SELECT event_id, market_type, decision_payload_json, result, pnl_units
        FROM ledger_records
        WHERE sport='nfl' AND status='settled' AND ledger_tier='main'
        AND market_type IN ('spread','total')"""
    )
    rows = c.fetchall()
    games: dict[tuple[str, str, str], dict] = {}
    for event_id, market_type, payload_json, result, pnl in rows:
        d = json.loads(payload_json)
        gday = d["event_start_utc"][:10]
        key = (event_id, d["home_team"], d["away_team"])
        g = games.setdefault(
            key,
            {
                "event_id": event_id,
                "home_team": d["home_team"],
                "away_team": d["away_team"],
                "gameday": gday,
                "home_score": int(d["home_score"]),
                "away_score": int(d["away_score"]),
            },
        )
        if market_type == "spread":
            g["spread_line"] = float(d["decision_line"])
            g["ledger_spread_result"] = result
            g["ledger_spread_pnl"] = pnl
        elif market_type == "total":
            g["total_line"] = float(d["decision_line"])
            g["ledger_total_result"] = result
            g["ledger_total_pnl"] = pnl
    return games


def main() -> None:
    ledger_games = load_ledger_games()
    print(f"Ledger-settled NFL spread/total games (main tier): {len(ledger_games)}")

    schedules = pl.read_parquet(
        "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
    )
    sched_rows = schedules.to_dicts()
    sched_index = {}
    for s in sched_rows:
        key = (str(s["gameday"]), s["home_team"], s["away_team"])
        sched_index[key] = s

    eff_store = NFLEfficiencyStore.get_default(DATA_ROOT)
    v5_engine = NFLStructuralV5Engine()
    v7_spread_engine = NFLSpreadModelV7()
    v7_total_engine = NFLTotalModelV7()

    spread_v5, spread_v7 = _new_bucket(), _new_bucket()
    total_v5, total_v7 = _new_bucket(), _new_bucket()
    reconcile_mismatch = []
    matched = 0

    for key, g in ledger_games.items():
        h_team, a_team, gday = g["home_team"], g["away_team"], g["gameday"]
        h_code, a_code = TEAM_CODE.get(h_team), TEAM_CODE.get(a_team)
        sched = sched_index.get((gday, h_code, a_code))
        if sched is None:
            print(f"  [no schedule match] {gday} {h_team} vs {a_team}")
            continue
        matched += 1

        h_score, a_score = g["home_score"], g["away_score"]
        margin = h_score - a_score
        total_pts = h_score + a_score

        spread_home_line = g.get("spread_line")
        tot_line = g.get("total_line")

        roof = str(sched.get("roof") or "outdoors").lower()
        is_dome = roof in ("dome", "closed")
        wind = float(sched.get("wind") or 5.0) if not is_dome else 0.0
        temp = float(sched.get("temp") or 65.0) if not is_dome else 70.0
        h_rest = int(sched.get("home_rest") or 7)
        a_rest = int(sched.get("away_rest") or 7)
        h_qb = str(sched.get("home_qb_name") or "")
        a_qb = str(sched.get("away_qb_name") or "")
        is_neutral = str(sched.get("location") or "").lower() == "neutral"

        eff = eff_store.get_matchup_efficiency(
            home_team=h_team,
            away_team=a_team,
            as_of_date=gday,
            home_qb=h_qb,
            away_qb=a_qb,
        )

        if spread_home_line is not None:
            home_cover = 1.0 if margin > -spread_home_line else (0.5 if margin == -spread_home_line else 0.0)
            fc5 = v5_engine.forecast_game(
                home_team=h_team,
                away_team=a_team,
                home_off_epa=eff.home_off_epa,
                home_def_epa=eff.home_def_epa,
                away_off_epa=eff.away_off_epa,
                away_def_epa=eff.away_def_epa,
                home_qb_tier_adj=eff.home_qb_tier_adj,
                away_qb_tier_adj=eff.away_qb_tier_adj,
                wind_mph=wind,
                temperature_f=temp,
                is_dome=is_dome,
                home_rest_days=h_rest,
                away_rest_days=a_rest,
                spread_home_line=spread_home_line,
                total_line=tot_line or 44.0,
                is_neutral_site=is_neutral,
            )
            p_h_cov5, p_a_cov5 = fc5.prob_home_cover, fc5.prob_away_cover
            v5_called = False
            if (p_h_cov5 - MKT_ASK) >= MIN_EDGE:
                _settle(spread_v5, home_cover)
                v5_called = True
            elif (p_a_cov5 - MKT_ASK) >= MIN_EDGE:
                _settle(spread_v5, 1.0 - home_cover if home_cover != 0.5 else 0.5)
                v5_called = True
            if v5_called != (g.get("ledger_spread_result") is not None):
                reconcile_mismatch.append((key, "spread", v5_called, g.get("ledger_spread_result")))

            fc7s = v7_spread_engine.forecast_from_efficiency(
                eff,
                spread_home_line=spread_home_line,
                is_neutral_site=is_neutral,
                home_rest_days=h_rest,
                away_rest_days=a_rest,
            )
            p_h_cov7, p_a_cov7 = fc7s.prob_home_cover, fc7s.prob_away_cover
            if (p_h_cov7 - MKT_ASK) >= MIN_EDGE:
                _settle(spread_v7, home_cover)
            elif (p_a_cov7 - MKT_ASK) >= MIN_EDGE:
                _settle(spread_v7, 1.0 - home_cover if home_cover != 0.5 else 0.5)

        if tot_line is not None:
            over_won = 1.0 if total_pts > tot_line else (0.5 if total_pts == tot_line else 0.0)
            fc5t = v5_engine.forecast_game(
                home_team=h_team,
                away_team=a_team,
                home_off_epa=eff.home_off_epa,
                home_def_epa=eff.home_def_epa,
                away_off_epa=eff.away_off_epa,
                away_def_epa=eff.away_def_epa,
                home_qb_tier_adj=eff.home_qb_tier_adj,
                away_qb_tier_adj=eff.away_qb_tier_adj,
                wind_mph=wind,
                temperature_f=temp,
                is_dome=is_dome,
                home_rest_days=h_rest,
                away_rest_days=a_rest,
                spread_home_line=spread_home_line or 0.0,
                total_line=tot_line,
                is_neutral_site=is_neutral,
            )
            p_over5, p_under5 = fc5t.prob_over, fc5t.prob_under
            if (p_over5 - MKT_ASK) >= MIN_EDGE:
                _settle(total_v5, over_won)
            elif (p_under5 - MKT_ASK) >= MIN_EDGE:
                _settle(total_v5, 1.0 - over_won if over_won != 0.5 else 0.5)

            fc7t = v7_total_engine.forecast_from_efficiency(
                eff,
                total_line=tot_line,
                is_dome=is_dome,
                wind_mph=wind,
                temperature_f=temp,
            )
            p_over7, p_under7 = fc7t.prob_over, fc7t.prob_under
            if (p_over7 - MKT_ASK) >= MIN_EDGE:
                _settle(total_v7, over_won)
            elif (p_under7 - MKT_ASK) >= MIN_EDGE:
                _settle(total_v7, 1.0 - over_won if over_won != 0.5 else 0.5)

    print(f"\nMatched {matched}/{len(ledger_games)} ledger games to nflverse schedule rows.\n")

    def _report(name, b):
        wl = b["wins"] + b["losses"]
        wr = (b["wins"] / wl * 100) if wl else 0.0
        print(
            f"{name:34s} | {b['picks']:4d} picks | {b['wins']:3d}W-{b['losses']:3d}L-{b['pushes']:2d}P "
            f"({wr:5.1f}%) | Net: {b['pnl']:+8.2f}u"
        )

    print("-- SPREAD --")
    _report("v5 recomputed", spread_v5)
    _report("v7 challenger", spread_v7)
    print("-- TOTAL --")
    _report("v5 recomputed", total_v5)
    _report("v7 challenger", total_v7)

    print(f"\nv5 call/no-call vs ledger mismatches: {len(reconcile_mismatch)}")
    for m in reconcile_mismatch[:10]:
        print(" ", m)


if __name__ == "__main__":
    main()
