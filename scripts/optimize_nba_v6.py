"""Point-in-Time NBA Feature Extraction and Vectorized Optimization.

Precomputes exact point-in-time features on Validation set (n=886 games)
and optimizes mathematical parameters instantaneously without data leakage.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.domain import parse_utc
from model_prediction.features.base import GameRecord
from model_prediction.features.elo_ratings import build_elo
from model_prediction.features.trends import TrendEngine


@dataclass
class PITGameFeature:
    event_id: str
    date: str
    home_team: str
    away_team: str
    actual_home_score: int
    actual_away_score: int
    actual_margin: int
    actual_total: int
    actual_home_win: float
    elo_margin: float
    elo_home_win: float
    home_raw_points: float
    away_raw_points: float
    trend_margin_raw: float
    rest_diff: float
    home_b2b: bool
    away_b2b: bool
    home_3in4: bool
    away_3in4: bool
    home_consistency: float
    away_consistency: float
    is_denver_home: bool
    is_utah_home: bool


def extract_pit_features(
    all_games: list[GameRecord],
    target_games: list[GameRecord],
    offseason_gap_days: float = 60.0,
    offseason_retention: float = 0.40,
    isolate_postseason: bool = True,
    elo_home_advantage: float = 38.0,
) -> list[PITGameFeature]:
    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in target_games:
        dates_map[g.event_start_utc[:10]].append(g)

    sorted_dates = sorted(dates_map.keys())
    features: list[PITGameFeature] = []

    print(f"Extracting PIT features across {len(sorted_dates)} dates ({len(target_games)} games)...")

    for d in sorted_dates:
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]

        elo = build_elo(
            history,
            "nba",
            offseason_gap_days=int(offseason_gap_days),
            home_advantage=elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=offseason_gap_days,
            offseason_retention=offseason_retention,
            isolate_postseason=isolate_postseason,
        )
        baseline = trend.league_baseline or 114.2

        for g in games_today:
            h_win = elo.expected_home_win(g.home_team, g.away_team)
            a_trend = trend.team_trend(g.away_team)
            h_trend = trend.team_trend(g.home_team)

            away_points = (a_trend.offense["hl10"] + h_trend.defense["hl10"]) / 2 or baseline
            home_points = (h_trend.offense["hl10"] + a_trend.defense["hl10"]) / 2 or baseline

            r_home = elo.rating(g.home_team) + elo.home_advantage
            r_away = elo.rating(g.away_team)
            elo_margin = (r_home - r_away) / 28.0

            # Rest disparity & B2B
            game_start_dt = parse_utc(g.event_start_utc)
            home_prev = [p for p in history if p.home_team == g.home_team or p.away_team == g.home_team]
            away_prev = [p for p in history if p.home_team == g.away_team or p.away_team == g.away_team]
            home_prev.sort(key=lambda p: p.start)
            away_prev.sort(key=lambda p: p.start)

            home_rest = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0 if home_prev else 3.0
            away_rest = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0 if away_prev else 3.0

            rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
            home_b2b = home_rest <= 1.25
            away_b2b = away_rest <= 1.25

            h_4d = [p for p in home_prev if 0 < (game_start_dt - p.start).total_seconds() <= 4.2 * 86400]
            a_4d = [p for p in away_prev if 0 < (game_start_dt - p.start).total_seconds() <= 4.2 * 86400]
            home_3in4 = len(h_4d) >= 2
            away_3in4 = len(a_4d) >= 2

            act_m = g.home_score - g.away_score
            act_tot = g.home_score + g.away_score

            features.append(
                PITGameFeature(
                    event_id=g.event_id,
                    date=d,
                    home_team=g.home_team,
                    away_team=g.away_team,
                    actual_home_score=g.home_score,
                    actual_away_score=g.away_score,
                    actual_margin=act_m,
                    actual_total=act_tot,
                    actual_home_win=1.0 if act_m > 0 else 0.0,
                    elo_margin=elo_margin,
                    elo_home_win=h_win,
                    home_raw_points=home_points,
                    away_raw_points=away_points,
                    trend_margin_raw=home_points - away_points,
                    rest_diff=rest_diff,
                    home_b2b=home_b2b,
                    away_b2b=away_b2b,
                    home_3in4=home_3in4,
                    away_3in4=away_3in4,
                    home_consistency=h_trend.consistency or 0.05,
                    away_consistency=a_trend.consistency or 0.05,
                    is_denver_home=(g.home_team == "Denver Nuggets"),
                    is_utah_home=(g.home_team == "Utah Jazz"),
                )
            )

    return features


def simulate_model(
    rows: list[PITGameFeature],
    elo_weight: float = 0.50,
    hca_points: float = 1.40,
    altitude_hca: bool = False,
    b2b_penalty_home: float = 0.60,
    b2b_penalty_away: float = 1.20,
    schedule_density_drag: bool = True,
    rest_weight: float = 0.20,
    logistic_scale: float = 11.2,
    margin_sd_base: float = 14.5,
    dynamic_volatility: bool = True,
    use_pace_sd_scaling: bool = False,
    b2b_pace_drag: float = 0.8,
) -> dict[str, float]:
    margin_errors = []
    sq_margin_errors = []
    total_errors = []
    briers = []
    accuracies = []

    trend_weight = 1.0 - elo_weight

    for r in rows:
        eff_hca = hca_points
        if altitude_hca:
            if r.is_denver_home:
                eff_hca += 1.40
            elif r.is_utah_home:
                eff_hca += 0.80

        h_pts = r.home_raw_points
        a_pts = r.away_raw_points

        if r.home_b2b and b2b_penalty_home > 0:
            h_pts -= b2b_penalty_home
        if r.away_b2b and b2b_penalty_away > 0:
            a_pts -= b2b_penalty_away
        if schedule_density_drag:
            if r.home_3in4 and not r.home_b2b:
                h_pts -= 0.80
            if r.away_3in4 and not r.away_b2b:
                a_pts -= 1.20

        if altitude_hca and (r.is_denver_home or r.is_utah_home) and (r.away_b2b or r.away_3in4):
            eff_hca += 1.0

        t_margin = (h_pts - a_pts) + eff_hca
        exp_margin = elo_weight * r.elo_margin + trend_weight * t_margin + rest_weight * r.rest_diff

        # Total model
        league_pace = 99.2
        pace_adj = 0.0
        if r.rest_diff > 2.0:
            pace_adj += 0.5
        elif r.rest_diff < -2.0:
            pace_adj -= 0.5
        if (r.home_b2b or r.away_b2b) and b2b_pace_drag > 0:
            pace_adj -= b2b_pace_drag
        if (r.home_3in4 or r.away_3in4) and schedule_density_drag:
            pace_adj -= 0.6

        est_possessions = league_pace + pace_adj
        ortg_a = (a_pts / league_pace) * 100.0
        ortg_h = (h_pts / league_pace) * 100.0
        exp_total = est_possessions * (ortg_a + ortg_h) / 100.0

        # Margin SD
        eff_margin_sd = margin_sd_base
        if dynamic_volatility:
            avg_c = (r.away_consistency + r.home_consistency) / 2.0
            eff_margin_sd = max(12.0, min(17.5, margin_sd_base * (1.0 + 0.25 * (1.0 - avg_c / 0.055))))

        if use_pace_sd_scaling:
            eff_margin_sd *= math.sqrt(est_possessions / league_pace)

        # Moneyline
        p_margin_home = 1.0 / (1.0 + 10.0 ** (-exp_margin / logistic_scale))
        p_home = elo_weight * r.elo_home_win + (1.0 - elo_weight) * p_margin_home
        p_home_clamped = max(1e-4, min(1.0 - 1e-4, p_home))

        m_err = abs(exp_margin - r.actual_margin)
        margin_errors.append(m_err)
        sq_margin_errors.append(m_err**2)
        total_errors.append(abs(exp_total - r.actual_total))

        brier = (p_home_clamped - r.actual_home_win) ** 2
        briers.append(brier)

        pred_win = 1.0 if p_home_clamped >= 0.50 else 0.0
        accuracies.append(1.0 if pred_win == r.actual_home_win else 0.0)

    return {
        "mae_margin": round(float(np.mean(margin_errors)), 4),
        "rmse_margin": round(float(np.sqrt(np.mean(sq_margin_errors))), 4),
        "mae_total": round(float(np.mean(total_errors)), 4),
        "brier": round(float(np.mean(briers)), 4),
        "accuracy": round(float(np.mean(accuracies)), 4),
    }


def main():
    all_games = []
    with open("data/processed/nba/games.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            all_games.append(
                GameRecord(
                    event_id=str(r["event_id"]),
                    event_start_utc=r["event_start_utc"],
                    league="NBA",
                    away_team=r["away_team"],
                    home_team=r["home_team"],
                    away_score=int(r["away_score"]),
                    home_score=int(r["home_score"]),
                    season_type=r.get("season_type", "unknown"),
                    season_year=r.get("season_year"),
                )
            )
    all_games.sort(key=lambda g: g.event_start_utc)

    _train_games, val_games, _holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Cohorts:", meta)

    # 1. Extract PIT features on validation
    val_features = extract_pit_features(all_games, val_games)
    print(f"Extracted {len(val_features)} validation feature rows.")

    # 2. Benchmark Incumbent v5
    v5_res = simulate_model(
        val_features,
        elo_weight=0.50,
        hca_points=1.40,
        altitude_hca=True,
        b2b_penalty_home=0.60,
        b2b_penalty_away=1.20,
        rest_weight=0.20,
    )
    print("\n--- VALIDATION BENCHMARK (Incumbent v5) ---")
    print(v5_res)

    # 3. Vectorized Grid Search across plausible parameters
    param_grid = {
        "elo_weight": [0.40, 0.50, 0.55, 0.60, 0.65],
        "hca_points": [1.10, 1.30, 1.40, 1.60, 1.80],
        "altitude_hca": [False, True],
        "b2b_penalty_home": [0.60, 1.50, 2.20, 2.80, 3.20],
        "b2b_penalty_away": [0.80, 1.20, 1.60, 2.00],
        "rest_weight": [0.10, 0.15, 0.20, 0.25],
        "use_pace_sd_scaling": [False, True],
        "logistic_scale": [10.5, 11.2, 11.8],
    }

    print("\nRunning instant combinatorial optimization on Validation...")
    keys, values = zip(*param_grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    print(f"Testing {len(permutations)} combinations...")

    scored = []
    for p in permutations:
        res = simulate_model(val_features, **p)
        score = res["mae_margin"] + res["brier"] * 10.0
        scored.append((score, res, p))

    scored.sort(key=lambda x: x[0])

    print("\n--- TOP 5 OPTIMAL CONFIGURATIONS ON VALIDATION ---")
    for s, res, p in scored[:5]:
        print(
            f"Score {s:.4f} | Margin MAE: {res['mae_margin']} (RMSE: {res['rmse_margin']}) | Brier: {res['brier']} | Acc: {res['accuracy']}"
        )
        print(f"  Params: {p}")

    best_p = scored[0][2]
    best_res = scored[0][1]

    # Save best candidate config
    out_path = Path("outputs/analysis/nba_v6_optimal_params.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"params": best_p, "validation_metrics": best_res, "v5_metrics": v5_res}, indent=2)
    )
    print(f"\nSaved optimal parameters to {out_path}")


if __name__ == "__main__":
    main()
