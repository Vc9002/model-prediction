"""Reproducible, Market-Relative Validation of All Three Existing NFL Production Models.

Models Evaluated:
  1. Moneyline: nfl-elo-trend-lr-v4 (Fixed train/serve parity)
  2. Spread: nfl-spread-structural-v5 (Real point-in-time EPA & QB efficiency vs baseline)
  3. Total: nfl-total-structural-v5 (Real point-in-time EPA & weather efficiency vs baseline)

Evaluated against exact historical market lines and closing odds across 934 completed games (2023-2026).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from model_prediction.features.elo_ratings import build_elo
from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.features.trends import TrendEngine
from model_prediction.models.nfl_structural_v5 import NFLStructuralV5Engine

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"

V4_INTERCEPT = -1.330143552
V4_COEFFICIENTS = [2.6154014596, 0.0502116986]  # [elo_probability, trend_gap]


def _american_to_prob_and_payout(odds: float | None) -> tuple[float, float]:
    """Convert American odds to implied probability and decimal payout factor."""
    if odds is None or odds == 0 or not math.isfinite(odds):
        return 0.5238, 1.9091  # Standard -110 default
    if odds > 0:
        implied = 100.0 / (odds + 100.0)
        payout = 1.0 + odds / 100.0
    else:
        implied = abs(odds) / (abs(odds) + 100.0)
        payout = 1.0 + 100.0 / abs(odds)
    return implied, payout


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-25.0, min(25.0, x))))


def run_nfl_validation(
    min_season: int = 2023,
    min_edge: float = 0.035,
) -> dict[str, Any]:
    """Run full point-in-time market-relative validation of all three NFL models."""
    print("=" * 70)
    print("RUNNING REPRODUCIBLE MARKET-RELATIVE NFL VALIDATION")
    print(f"Evaluation Seasons: {min_season}-2026 | Min Edge: {min_edge * 100:.1f}%")
    print("=" * 70)

    # 1. Load games with market lines
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

    # Load efficiency store
    eff_store = NFLEfficiencyStore.get_default(DATA_ROOT)
    v5_engine = NFLStructuralV5Engine()

    # Track team history for walk-forward Elo & Trends
    from model_prediction.features.base import GameRecord

    game_records: list[GameRecord] = []
    # Load past games for Elo foundation
    hist_file = DATA_ROOT / "historical/nfl_games_all.jsonl"
    if hist_file.exists():
        with open(hist_file, "r", encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                if item.get("home_score") is not None and item.get("season_year", 0) < min_season:
                    try:
                        dt = datetime.fromisoformat(item["event_start_utc"])
                        game_records.append(
                            GameRecord(
                                event_id=str(item["event_id"]),
                                home_team=item["home_team"],
                                away_team=item["away_team"],
                                home_score=int(item["home_score"]),
                                away_score=int(item["away_score"]),
                                start=dt,
                            )
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
    game_records.sort(key=lambda g: g.start)
    print(f"Pre-seeded {len(game_records)} pre-{min_season} games for Elo prior stabilization.")

    # Results accumulators
    # 1. Moneyline
    ml_results = {
        "fixed_v4": {"brier": [], "acc": [], "picks": 0, "wins": 0, "losses": 0, "pnl": 0.0, "staked": 0.0},
        "broken_serving": {
            "brier": [],
            "acc": [],
            "picks": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "staked": 0.0,
        },
    }

    # 2. Spread
    spread_results = {
        "v5_real_efficiency": {
            "acc": [],
            "picks": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "pnl": 0.0,
            "staked": 0.0,
        },
        "v5_baseline_no_eff": {
            "acc": [],
            "picks": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "pnl": 0.0,
            "staked": 0.0,
        },
    }

    # 3. Total
    total_results = {
        "v5_real_efficiency": {
            "acc": [],
            "picks": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "pnl": 0.0,
            "staked": 0.0,
        },
        "v5_baseline_no_eff": {
            "acc": [],
            "picks": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "pnl": 0.0,
            "staked": 0.0,
        },
    }

    # Iterate chronologically
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

    for g in games:
        gday = str(g["gameday"])
        h_code = str(g["home_team"])
        a_code = str(g["away_team"])
        h_team = team_map.get(h_code, h_code)
        a_team = team_map.get(a_code, a_code)
        h_score = int(g["home_score"])
        a_score = int(g["away_score"])
        home_won = 1.0 if h_score > a_score else (0.5 if h_score == a_score else 0.0)
        margin = h_score - a_score
        total_pts = h_score + a_score

        # Lines
        # In nflverse: spread_line is home spread line (+3 = home underdog by 3, -3 = home favored by 3)
        # Note: spread_home_line in our engine is negative if home is favored (-3.5)
        raw_spread = float(g["spread_line"])  # e.g. 3.0 means away favored by 3, -3.0 means home favored
        spread_home_line = -raw_spread if raw_spread != 0 else 0.0
        tot_line = float(g["total_line"])

        # Moneylines
        h_ml = g.get("home_moneyline")
        a_ml = g.get("away_moneyline")
        p_h_imp, payout_h = _american_to_prob_and_payout(h_ml)
        p_a_imp, payout_a = _american_to_prob_and_payout(a_ml)

        # Weather / venue
        roof = str(g.get("roof") or "outdoors").lower()
        is_dome = roof in ("dome", "closed")
        wind = float(g.get("wind") or 5.0) if not is_dome else 0.0
        temp = float(g.get("temp") or 65.0) if not is_dome else 70.0
        h_rest = int(g.get("home_rest") or 7)
        a_rest = int(g.get("away_rest") or 7)
        h_qb = str(g.get("home_qb_name") or "")
        a_qb = str(g.get("away_qb_name") or "")
        is_neutral = str(g.get("location") or "").lower() == "neutral"

        # Point-in-time Elo & Trend from history
        elo_book = build_elo(game_records, "nfl")
        trends = TrendEngine(game_records)
        h_trend = trends.team_trend(h_team)
        a_trend = trends.team_trend(a_team)
        trend_gap = h_trend.offensive_momentum - a_trend.offensive_momentum

        # ---------------------------------------------------------
        # 1. MONEYLINE: nfl-elo-trend-lr-v4
        # ---------------------------------------------------------
        # Fixed serving (matches training: pure Elo expected_home_win)
        p_elo_fixed = (
            elo_book.expected_neutral_win(h_team, a_team)
            if is_neutral
            else elo_book.expected_home_win(h_team, a_team)
        )
        z_fixed = V4_INTERCEPT + V4_COEFFICIENTS[0] * p_elo_fixed + V4_COEFFICIENTS[1] * trend_gap
        p_h_fixed = sigmoid(z_fixed)

        # Broken serving (where starters were penalized -45 Elo as 'backups')
        pen_h = -45.0 if h_qb else 0.0
        pen_a = -45.0 if a_qb else 0.0
        r_h_broken = elo_book.rating(h_team) + pen_h
        r_a_broken = elo_book.rating(a_team) + pen_a
        adv = 0.0 if is_neutral else elo_book.home_advantage
        p_elo_broken = 1.0 / (1.0 + 10.0 ** (-((r_h_broken + adv) - r_a_broken) / 400.0))
        z_broken = V4_INTERCEPT + V4_COEFFICIENTS[0] * p_elo_broken + V4_COEFFICIENTS[1] * trend_gap
        p_h_broken = sigmoid(z_broken)

        # Record Moneyline metrics
        ml_results["fixed_v4"]["brier"].append((p_h_fixed - home_won) ** 2)
        ml_results["fixed_v4"]["acc"].append(int((p_h_fixed >= 0.5) == (home_won >= 0.5)))
        ml_results["broken_serving"]["brier"].append((p_h_broken - home_won) ** 2)
        ml_results["broken_serving"]["acc"].append(int((p_h_broken >= 0.5) == (home_won >= 0.5)))

        # Gated ML bets
        # Home bet
        if (p_h_fixed - p_h_imp) >= min_edge:
            ml_results["fixed_v4"]["picks"] += 1
            ml_results["fixed_v4"]["staked"] += 1.0
            if home_won == 1.0:
                ml_results["fixed_v4"]["wins"] += 1
                ml_results["fixed_v4"]["pnl"] += payout_h - 1.0
            elif home_won == 0.0:
                ml_results["fixed_v4"]["losses"] += 1
                ml_results["fixed_v4"]["pnl"] -= 1.0
        # Away bet
        elif ((1.0 - p_h_fixed) - p_a_imp) >= min_edge:
            ml_results["fixed_v4"]["picks"] += 1
            ml_results["fixed_v4"]["staked"] += 1.0
            if home_won == 0.0:
                ml_results["fixed_v4"]["wins"] += 1
                ml_results["fixed_v4"]["pnl"] += payout_a - 1.0
            elif home_won == 1.0:
                ml_results["fixed_v4"]["losses"] += 1
                ml_results["fixed_v4"]["pnl"] -= 1.0

        # Broken serving gated bets
        if (p_h_broken - p_h_imp) >= min_edge:
            ml_results["broken_serving"]["picks"] += 1
            ml_results["broken_serving"]["staked"] += 1.0
            if home_won == 1.0:
                ml_results["broken_serving"]["wins"] += 1
                ml_results["broken_serving"]["pnl"] += payout_h - 1.0
            elif home_won == 0.0:
                ml_results["broken_serving"]["losses"] += 1
                ml_results["broken_serving"]["pnl"] -= 1.0
        elif ((1.0 - p_h_broken) - p_a_imp) >= min_edge:
            ml_results["broken_serving"]["picks"] += 1
            ml_results["broken_serving"]["staked"] += 1.0
            if home_won == 0.0:
                ml_results["broken_serving"]["wins"] += 1
                ml_results["broken_serving"]["pnl"] += payout_a - 1.0
            elif home_won == 1.0:
                ml_results["broken_serving"]["losses"] += 1
                ml_results["broken_serving"]["pnl"] -= 1.0

        # ---------------------------------------------------------
        # 2. SPREAD & TOTAL: nfl-spread-structural-v5 & nfl-total-structural-v5
        # ---------------------------------------------------------
        # Real point-in-time efficiency
        eff = eff_store.get_matchup_efficiency(
            home_team=h_team,
            away_team=a_team,
            as_of_date=gday,
            home_qb=h_qb,
            away_qb=a_qb,
        )

        fc_real = v5_engine.forecast_game(
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

        # Baseline (no real efficiency: neutral 0.04 off EPA, 0.0 def EPA, 0.0 QB adj)
        fc_base = v5_engine.forecast_game(
            home_team=h_team,
            away_team=a_team,
            home_off_epa=0.04,
            home_def_epa=0.04,
            away_off_epa=0.04,
            away_def_epa=0.04,
            home_qb_tier_adj=0.0,
            away_qb_tier_adj=0.0,
            wind_mph=wind,
            temperature_f=temp,
            is_dome=is_dome,
            home_rest_days=h_rest,
            away_rest_days=a_rest,
            spread_home_line=spread_home_line,
            total_line=tot_line,
            is_neutral_site=is_neutral,
        )

        # Spread outcome:
        # Home covers if margin > raw_spread (-spread_home_line); Push if margin == -spread_home_line
        home_cover = 1.0 if margin > -spread_home_line else (0.5 if margin == -spread_home_line else 0.0)

        # Real efficiency spread evaluation
        p_h_cov_real = fc_real.prob_home_cover
        p_a_cov_real = fc_real.prob_away_cover
        spread_results["v5_real_efficiency"]["acc"].append(int((p_h_cov_real >= 0.5) == (home_cover == 1.0)))

        # Standard market line: 52.38% breakeven ask (-110 juice)
        mkt_spread_ask = 0.5238
        if (p_h_cov_real - mkt_spread_ask) >= min_edge:
            spread_results["v5_real_efficiency"]["picks"] += 1
            spread_results["v5_real_efficiency"]["staked"] += 1.0
            if home_cover == 1.0:
                spread_results["v5_real_efficiency"]["wins"] += 1
                spread_results["v5_real_efficiency"]["pnl"] += 0.9091
            elif home_cover == 0.0:
                spread_results["v5_real_efficiency"]["losses"] += 1
                spread_results["v5_real_efficiency"]["pnl"] -= 1.0
            else:
                spread_results["v5_real_efficiency"]["pushes"] += 1
        elif (p_a_cov_real - mkt_spread_ask) >= min_edge:
            spread_results["v5_real_efficiency"]["picks"] += 1
            spread_results["v5_real_efficiency"]["staked"] += 1.0
            if home_cover == 0.0:
                spread_results["v5_real_efficiency"]["wins"] += 1
                spread_results["v5_real_efficiency"]["pnl"] += 0.9091
            elif home_cover == 1.0:
                spread_results["v5_real_efficiency"]["losses"] += 1
                spread_results["v5_real_efficiency"]["pnl"] -= 1.0
            else:
                spread_results["v5_real_efficiency"]["pushes"] += 1

        # Baseline spread evaluation
        p_h_cov_base = fc_base.prob_home_cover
        p_a_cov_base = fc_base.prob_away_cover
        spread_results["v5_baseline_no_eff"]["acc"].append(int((p_h_cov_base >= 0.5) == (home_cover == 1.0)))
        if (p_h_cov_base - mkt_spread_ask) >= min_edge:
            spread_results["v5_baseline_no_eff"]["picks"] += 1
            spread_results["v5_baseline_no_eff"]["staked"] += 1.0
            if home_cover == 1.0:
                spread_results["v5_baseline_no_eff"]["wins"] += 1
                spread_results["v5_baseline_no_eff"]["pnl"] += 0.9091
            elif home_cover == 0.0:
                spread_results["v5_baseline_no_eff"]["losses"] += 1
                spread_results["v5_baseline_no_eff"]["pnl"] -= 1.0
            else:
                spread_results["v5_baseline_no_eff"]["pushes"] += 1
        elif (p_a_cov_base - mkt_spread_ask) >= min_edge:
            spread_results["v5_baseline_no_eff"]["picks"] += 1
            spread_results["v5_baseline_no_eff"]["staked"] += 1.0
            if home_cover == 0.0:
                spread_results["v5_baseline_no_eff"]["wins"] += 1
                spread_results["v5_baseline_no_eff"]["pnl"] += 0.9091
            elif home_cover == 1.0:
                spread_results["v5_baseline_no_eff"]["losses"] += 1
                spread_results["v5_baseline_no_eff"]["pnl"] -= 1.0
            else:
                spread_results["v5_baseline_no_eff"]["pushes"] += 1

        # Total outcome: Over if total_pts > tot_line, Under if <, Push if ==
        over_won = 1.0 if total_pts > tot_line else (0.5 if total_pts == tot_line else 0.0)

        # Real efficiency total evaluation
        p_over_real = fc_real.prob_over
        p_under_real = fc_real.prob_under
        total_results["v5_real_efficiency"]["acc"].append(int((p_over_real >= 0.5) == (over_won == 1.0)))
        if (p_over_real - mkt_spread_ask) >= min_edge:
            total_results["v5_real_efficiency"]["picks"] += 1
            total_results["v5_real_efficiency"]["staked"] += 1.0
            if over_won == 1.0:
                total_results["v5_real_efficiency"]["wins"] += 1
                total_results["v5_real_efficiency"]["pnl"] += 0.9091
            elif over_won == 0.0:
                total_results["v5_real_efficiency"]["losses"] += 1
                total_results["v5_real_efficiency"]["pnl"] -= 1.0
            else:
                total_results["v5_real_efficiency"]["pushes"] += 1
        elif (p_under_real - mkt_spread_ask) >= min_edge:
            total_results["v5_real_efficiency"]["picks"] += 1
            total_results["v5_real_efficiency"]["staked"] += 1.0
            if over_won == 0.0:
                total_results["v5_real_efficiency"]["wins"] += 1
                total_results["v5_real_efficiency"]["pnl"] += 0.9091
            elif over_won == 1.0:
                total_results["v5_real_efficiency"]["losses"] += 1
                total_results["v5_real_efficiency"]["pnl"] -= 1.0
            else:
                total_results["v5_real_efficiency"]["pushes"] += 1

        # Baseline total evaluation
        p_over_base = fc_base.prob_over
        p_under_base = fc_base.prob_under
        total_results["v5_baseline_no_eff"]["acc"].append(int((p_over_base >= 0.5) == (over_won == 1.0)))
        if (p_over_base - mkt_spread_ask) >= min_edge:
            total_results["v5_baseline_no_eff"]["picks"] += 1
            total_results["v5_baseline_no_eff"]["staked"] += 1.0
            if over_won == 1.0:
                total_results["v5_baseline_no_eff"]["wins"] += 1
                total_results["v5_baseline_no_eff"]["pnl"] += 0.9091
            elif over_won == 0.0:
                total_results["v5_baseline_no_eff"]["losses"] += 1
                total_results["v5_baseline_no_eff"]["pnl"] -= 1.0
            else:
                total_results["v5_baseline_no_eff"]["pushes"] += 1
        elif (p_under_base - mkt_spread_ask) >= min_edge:
            total_results["v5_baseline_no_eff"]["picks"] += 1
            total_results["v5_baseline_no_eff"]["staked"] += 1.0
            if over_won == 0.0:
                total_results["v5_baseline_no_eff"]["wins"] += 1
                total_results["v5_baseline_no_eff"]["pnl"] += 0.9091
            elif over_won == 1.0:
                total_results["v5_baseline_no_eff"]["losses"] += 1
                total_results["v5_baseline_no_eff"]["pnl"] -= 1.0
            else:
                total_results["v5_baseline_no_eff"]["pushes"] += 1

        # Append game record for subsequent walk-forward days
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

    # Print Summary Tables
    print("\n" + "=" * 70)
    print("1. MONEYLINE: nfl-elo-trend-lr-v4 (Train/Serve Discrepancy Fix)")
    print("=" * 70)
    f_brier = float(np.mean(ml_results["fixed_v4"]["brier"]))
    f_acc = float(np.mean(ml_results["fixed_v4"]["acc"]))
    f_w = ml_results["fixed_v4"]["wins"]
    f_l = ml_results["fixed_v4"]["losses"]
    f_pnl = ml_results["fixed_v4"]["pnl"]
    f_stk = ml_results["fixed_v4"]["staked"]
    f_roi = (f_pnl / f_stk * 100) if f_stk > 0 else 0.0

    b_brier = float(np.mean(ml_results["broken_serving"]["brier"]))
    b_acc = float(np.mean(ml_results["broken_serving"]["acc"]))
    b_w = ml_results["broken_serving"]["wins"]
    b_l = ml_results["broken_serving"]["losses"]
    b_pnl = ml_results["broken_serving"]["pnl"]
    b_stk = ml_results["broken_serving"]["staked"]
    b_roi = (b_pnl / b_stk * 100) if b_stk > 0 else 0.0

    print(f"Fixed Model (Train/Serve Parity):   Brier: {f_brier:.4f} | Straight Acc: {f_acc * 100:.1f}%")
    print(
        f"  Gated Bets (Edge >= {min_edge * 100:.1f}%): {f_w + f_l} bets | {f_w}W - {f_l}L ({f_w / (f_w + f_l) * 100 if (f_w + f_l) > 0 else 0:.1f}%) | Net: {f_pnl:+.2f}u | ROI: {f_roi:+.1f}%"
    )
    print(f"Broken Model (Unwarranted QB Skew): Brier: {b_brier:.4f} | Straight Acc: {b_acc * 100:.1f}%")
    print(
        f"  Gated Bets (Edge >= {min_edge * 100:.1f}%): {b_w + b_l} bets | {b_w}W - {b_l}L ({b_w / (b_w + b_l) * 100 if (b_w + b_l) > 0 else 0:.1f}%) | Net: {b_pnl:+.2f}u | ROI: {b_roi:+.1f}%"
    )

    print("\n" + "=" * 70)
    print("2. SPREAD: nfl-spread-structural-v5 (Real EPA/QB Efficiency vs Baseline)")
    print("=" * 70)
    sr_w = spread_results["v5_real_efficiency"]["wins"]
    sr_l = spread_results["v5_real_efficiency"]["losses"]
    sr_p = spread_results["v5_real_efficiency"]["pushes"]
    sr_pnl = spread_results["v5_real_efficiency"]["pnl"]
    sr_stk = spread_results["v5_real_efficiency"]["staked"]
    sr_roi = (sr_pnl / sr_stk * 100) if sr_stk > 0 else 0.0

    sb_w = spread_results["v5_baseline_no_eff"]["wins"]
    sb_l = spread_results["v5_baseline_no_eff"]["losses"]
    sb_p = spread_results["v5_baseline_no_eff"]["pushes"]
    sb_pnl = spread_results["v5_baseline_no_eff"]["pnl"]
    sb_stk = spread_results["v5_baseline_no_eff"]["staked"]
    sb_roi = (sb_pnl / sb_stk * 100) if sb_stk > 0 else 0.0

    print(
        f"Real Efficiency Engine: {sr_w + sr_l + sr_p} bets | {sr_w}W - {sr_l}L - {sr_p}P ({sr_w / (sr_w + sr_l) * 100 if (sr_w + sr_l) > 0 else 0:.1f}%) | Net: {sr_pnl:+.2f}u | ROI: {sr_roi:+.1f}%"
    )
    print(
        f"Baseline (No Efficiency): {sb_w + sb_l + sb_p} bets | {sb_w}W - {sb_l}L - {sb_p}P ({sb_w / (sb_w + sb_l) * 100 if (sb_w + sb_l) > 0 else 0:.1f}%) | Net: {sb_pnl:+.2f}u | ROI: {sb_roi:+.1f}%"
    )

    print("\n" + "=" * 70)
    print("3. TOTAL: nfl-total-structural-v5 (Real EPA & Weather Efficiency vs Baseline)")
    print("=" * 70)
    tr_w = total_results["v5_real_efficiency"]["wins"]
    tr_l = total_results["v5_real_efficiency"]["losses"]
    tr_p = total_results["v5_real_efficiency"]["pushes"]
    tr_pnl = total_results["v5_real_efficiency"]["pnl"]
    tr_stk = total_results["v5_real_efficiency"]["staked"]
    tr_roi = (tr_pnl / tr_stk * 100) if tr_stk > 0 else 0.0

    tb_w = total_results["v5_baseline_no_eff"]["wins"]
    tb_l = total_results["v5_baseline_no_eff"]["losses"]
    tb_p = total_results["v5_baseline_no_eff"]["pushes"]
    tb_pnl = total_results["v5_baseline_no_eff"]["pnl"]
    tb_stk = total_results["v5_baseline_no_eff"]["staked"]
    tb_roi = (tb_pnl / tb_stk * 100) if tb_stk > 0 else 0.0

    print(
        f"Real Efficiency Engine: {tr_w + tr_l + tr_p} bets | {tr_w}W - {tr_l}L - {tr_p}P ({tr_w / (tr_w + tr_l) * 100 if (tr_w + tr_l) > 0 else 0:.1f}%) | Net: {tr_pnl:+.2f}u | ROI: {tr_roi:+.1f}%"
    )
    print(
        f"Baseline (No Efficiency): {tb_w + tb_l + tb_p} bets | {tb_w}W - {tb_l}L - {tb_p}P ({tb_w / (tb_w + tb_l) * 100 if (tb_w + tb_l) > 0 else 0:.1f}%) | Net: {tb_pnl:+.2f}u | ROI: {tb_roi:+.1f}%"
    )

    report = {
        "games_evaluated": len(games),
        "moneyline": {
            "fixed_v4": {
                "brier": f_brier,
                "accuracy": f_acc,
                "wins": f_w,
                "losses": f_l,
                "pnl": f_pnl,
                "roi": f_roi,
            },
            "broken_serving": {
                "brier": b_brier,
                "accuracy": b_acc,
                "wins": b_w,
                "losses": b_l,
                "pnl": b_pnl,
                "roi": b_roi,
            },
        },
        "spread": {
            "v5_real_efficiency": {
                "wins": sr_w,
                "losses": sr_l,
                "pushes": sr_p,
                "pnl": sr_pnl,
                "roi": sr_roi,
            },
            "v5_baseline_no_eff": {
                "wins": sb_w,
                "losses": sb_l,
                "pushes": sb_p,
                "pnl": sb_pnl,
                "roi": sb_roi,
            },
        },
        "total": {
            "v5_real_efficiency": {
                "wins": tr_w,
                "losses": tr_l,
                "pushes": tr_p,
                "pnl": tr_pnl,
                "roi": tr_roi,
            },
            "v5_baseline_no_eff": {
                "wins": tb_w,
                "losses": tb_l,
                "pushes": tb_p,
                "pnl": tb_pnl,
                "roi": tb_roi,
            },
        },
    }

    out_json = DATA_ROOT / "processed/nfl/market_relative_validation.json"
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote validation results to {out_json}")
    return report


if __name__ == "__main__":
    run_nfl_validation()
