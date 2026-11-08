"""Reproducible, Market-Relative Backtest: NFL v7 Challengers vs Incumbent v5.

Reuses the exact historical dataset, point-in-time Elo/trend/efficiency
construction, and market-relative gating methodology from
scripts/validate_nfl_models.py, swapping in NFLSpreadModelV7 /
NFLTotalModelV7 alongside the incumbent NFLStructuralV5Engine so both
sides are scored on the identical game-by-game feature snapshot (no
separate universe, no leakage risk from re-deriving features differently).

Read-only research script. Does not touch config/production.yaml or any
champion assignment.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_structural_v5 import NFLStructuralV5Engine
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"

MKT_ASK = 0.5238  # standard -110 breakeven implied prob


def _new_bucket() -> dict[str, Any]:
    return {"picks": 0, "wins": 0, "losses": 0, "pushes": 0, "pnl": 0.0}


def _settle(bucket: dict[str, Any], outcome: float) -> None:
    bucket["picks"] += 1
    if outcome == 1.0:
        bucket["wins"] += 1
        bucket["pnl"] += 0.9091
    elif outcome == 0.0:
        bucket["losses"] += 1
        bucket["pnl"] -= 1.0
    else:
        bucket["pushes"] += 1


def run_v7_backtest(min_season: int = 2023, min_edge: float = 0.035) -> dict[str, Any]:
    schedules = pl.read_parquet(
        "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
    )
    games = (
        schedules.filter(
            (pl.col("season") >= min_season)
            & pl.col("home_score").is_not_null()
            & pl.col("away_score").is_not_null()
            & pl.col("spread_line").is_not_null()
            & pl.col("total_line").is_not_null()
        )
        .sort(["gameday", "gametime", "game_id"])
        .to_dicts()
    )
    print(f"Loaded {len(games)} completed games with verified market lines.\n")

    eff_store = NFLEfficiencyStore.get_default(DATA_ROOT)
    v5_engine = NFLStructuralV5Engine()
    v7_spread_engine = NFLSpreadModelV7()
    v7_total_engine = NFLTotalModelV7()

    from model_prediction.features.base import GameRecord

    game_records: list[GameRecord] = []
    hist_file = DATA_ROOT / "historical/nfl_games_all.jsonl"
    if hist_file.exists():
        with open(hist_file, "r", encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                if item.get("home_score") is not None and item.get("season_year", 0) < min_season:
                    try:
                        game_records.append(
                            GameRecord(
                                event_id=str(item["event_id"]),
                                event_start_utc=str(item["event_start_utc"]),
                                league="nfl",
                                home_team=item["home_team"],
                                away_team=item["away_team"],
                                home_score=int(item["home_score"]),
                                away_score=int(item["away_score"]),
                                season_year=int(item.get("season_year", 0)),
                            )
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
    game_records.sort(key=lambda g: g.event_start_utc)
    print(f"Pre-seeded {len(game_records)} pre-{min_season} games for Elo prior stabilization.")

    team_map = {
        "ARI": "Arizona Cardinals",
        "ATL": "Atlanta Falcons",
        "BAL": "Baltimore Ravens",
        "BUF": "Buffalo Bills",
        "CAR": "Carolina Panthers",
        "CHI": "Chicago Bears",
        "CIN": "Cincinnati Bengals",
        "CLE": "Cleveland Browns",
        "DAL": "Dallas Cowboys",
        "DEN": "Denver Broncos",
        "DET": "Detroit Lions",
        "GB": "Green Bay Packers",
        "HOU": "Houston Texans",
        "IND": "Indianapolis Colts",
        "JAX": "Jacksonville Jaguars",
        "KC": "Kansas City Chiefs",
        "LA": "Los Angeles Rams",
        "LAC": "Los Angeles Chargers",
        "LV": "Las Vegas Raiders",
        "MIA": "Miami Dolphins",
        "MIN": "Minnesota Vikings",
        "NE": "New England Patriots",
        "NO": "New Orleans Saints",
        "NYG": "New York Giants",
        "NYJ": "New York Jets",
        "PHI": "Philadelphia Eagles",
        "PIT": "Pittsburgh Steelers",
        "SEA": "Seattle Seahawks",
        "SF": "San Francisco 49ers",
        "TB": "Tampa Bay Buccaneers",
        "TEN": "Tennessee Titans",
        "WAS": "Washington Commanders",
    }

    spread_v5 = _new_bucket()
    spread_v7 = _new_bucket()
    total_v5 = _new_bucket()
    total_v7 = _new_bucket()

    for g in games:
        gday = str(g["gameday"])
        h_code = str(g["home_team"])
        a_code = str(g["away_team"])
        h_team = team_map.get(h_code, h_code)
        a_team = team_map.get(a_code, a_code)
        h_score = int(g["home_score"])
        a_score = int(g["away_score"])
        margin = h_score - a_score
        total_pts = h_score + a_score

        raw_spread = float(g["spread_line"])
        spread_home_line = -raw_spread if raw_spread != 0 else 0.0
        tot_line = float(g["total_line"])

        roof = str(g.get("roof") or "outdoors").lower()
        is_dome = roof in ("dome", "closed")
        wind = float(g.get("wind") or 5.0) if not is_dome else 0.0
        temp = float(g.get("temp") or 65.0) if not is_dome else 70.0
        h_rest = int(g.get("home_rest") or 7)
        a_rest = int(g.get("away_rest") or 7)
        h_qb = str(g.get("home_qb_name") or "")
        a_qb = str(g.get("away_qb_name") or "")
        is_neutral = str(g.get("location") or "").lower() == "neutral"

        # Same point-in-time efficiency snapshot feeds both v5 and v7 — no separate universe.
        eff = eff_store.get_matchup_efficiency(
            home_team=h_team,
            away_team=a_team,
            as_of_date=gday,
            home_qb=h_qb,
            away_qb=a_qb,
        )

        home_cover = 1.0 if margin > -spread_home_line else (0.5 if margin == -spread_home_line else 0.0)
        over_won = 1.0 if total_pts > tot_line else (0.5 if total_pts == tot_line else 0.0)

        # --- v5 incumbent (identical call shape to validate_nfl_models.py) ---
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
            total_line=tot_line,
            is_neutral_site=is_neutral,
        )
        p_h_cov5, p_a_cov5 = fc5.prob_home_cover, fc5.prob_away_cover
        if (p_h_cov5 - MKT_ASK) >= min_edge:
            _settle(spread_v5, home_cover)
        elif (p_a_cov5 - MKT_ASK) >= min_edge:
            _settle(spread_v5, 1.0 - home_cover if home_cover != 0.5 else 0.5)

        p_over5, p_under5 = fc5.prob_over, fc5.prob_under
        if (p_over5 - MKT_ASK) >= min_edge:
            _settle(total_v5, over_won)
        elif (p_under5 - MKT_ASK) >= min_edge:
            _settle(total_v5, 1.0 - over_won if over_won != 0.5 else 0.5)

        # --- v7 challengers, fed the same eff snapshot via forecast_from_efficiency ---
        fc7s = v7_spread_engine.forecast_from_efficiency(
            eff,
            spread_home_line=spread_home_line,
            is_neutral_site=is_neutral,
            home_rest_days=h_rest,
            away_rest_days=a_rest,
        )
        p_h_cov7, p_a_cov7 = fc7s.prob_home_cover, fc7s.prob_away_cover
        if (p_h_cov7 - MKT_ASK) >= min_edge:
            _settle(spread_v7, home_cover)
        elif (p_a_cov7 - MKT_ASK) >= min_edge:
            _settle(spread_v7, 1.0 - home_cover if home_cover != 0.5 else 0.5)

        fc7t = v7_total_engine.forecast_from_efficiency(
            eff,
            total_line=tot_line,
            is_dome=is_dome,
            wind_mph=wind,
            temperature_f=temp,
        )
        p_over7, p_under7 = fc7t.prob_over, fc7t.prob_under
        if (p_over7 - MKT_ASK) >= min_edge:
            _settle(total_v7, over_won)
        elif (p_under7 - MKT_ASK) >= min_edge:
            _settle(total_v7, 1.0 - over_won if over_won != 0.5 else 0.5)

        try:
            dt = datetime.fromisoformat(f"{gday}T13:00:00+00:00")
            game_records.append(
                GameRecord(
                    event_id=str(g["game_id"]),
                    home_team=h_team,
                    away_team=a_team,
                    home_score=h_score,
                    away_score=a_score,
                    start=dt,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

    def _report(name: str, b: dict[str, Any]) -> None:
        wl = b["wins"] + b["losses"]
        wr = (b["wins"] / wl * 100) if wl else 0.0
        print(
            f"{name:34s} | {b['picks']:4d} picks | {b['wins']:3d}W-{b['losses']:3d}L-{b['pushes']:2d}P "
            f"({wr:5.1f}%) | Net: {b['pnl']:+8.2f}u"
        )

    print("\n" + "=" * 78)
    print(f"NFL v7 CHALLENGER BACKTEST  (seasons >= {min_season}, min_edge={min_edge:.1%})")
    print("=" * 78)
    print("\n-- SPREAD --")
    _report("nfl-spread-structural-v5 (incumbent)", spread_v5)
    _report("nfl-spread-structural-v7 (challenger)", spread_v7)
    print("\n-- TOTAL --")
    _report("nfl-total-structural-v5 (incumbent)", total_v5)
    _report("nfl-total-structural-v7 (challenger)", total_v7)

    report = {
        "games_evaluated": len(games),
        "min_season": min_season,
        "min_edge": min_edge,
        "spread": {"v5": spread_v5, "v7": spread_v7},
        "total": {"v5": total_v5, "v7": total_v7},
    }
    out_json = DATA_ROOT / "processed/nfl/v7_challenger_backtest.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote backtest results to {out_json}")
    return report


if __name__ == "__main__":
    run_v7_backtest()
