"""Locked Holdout Evaluation and Statistical Significance Gate: NBA v5 vs v6.

Performs point-in-time walk-forward evaluation on the locked holdout set
(636 completed games, 129 calendar dates, 2026-02-13 to 2026-10-30)
comparing incumbent nba-spread-margin-v5 with challenger nba-spread-margin-v6.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from nba_v5_pit_walkforward import paired_date_bootstrap, split_complete_dates
from optimize_nba_v6 import extract_pit_features, simulate_model

from model_prediction.features.base import GameRecord


def run_holdout_evaluation():
    print("=" * 70)
    print("NBA MODEL OVERHAUL: LOCKED HOLDOUT EVALUATION (v5 vs v6)")
    print("=" * 70)

    all_games: list[GameRecord] = []
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

    _train_games, _val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print(f"Dataset cohorts: {meta}")
    print(
        f"Holdout date span: {holdout_games[0].event_start_utc[:10]} to {holdout_games[-1].event_start_utc[:10]}"
    )

    # Extract point-in-time features on locked holdout
    print("\nExtracting point-in-time walk-forward features on locked holdout...")
    holdout_features = extract_pit_features(all_games, holdout_games)
    print(f"Extracted {len(holdout_features)} holdout game features.")

    # 1. INCUMBENT CONTROL: nba-spread-margin-v5
    v5_params = {
        "elo_weight": 0.50,
        "hca_points": 1.40,
        "altitude_hca": True,
        "b2b_penalty_home": 0.60,
        "b2b_penalty_away": 1.20,
        "schedule_density_drag": True,
        "rest_weight": 0.20,
        "logistic_scale": 11.2,
        "margin_sd_base": 14.5,
        "dynamic_volatility": True,
        "use_pace_sd_scaling": False,
    }
    v5_metrics = simulate_model(holdout_features, **v5_params)

    # 2. CHALLENGER: nba-spread-margin-v6
    v6_params = {
        "elo_weight": 0.55,
        "hca_points": 1.80,
        "altitude_hca": False,
        "b2b_penalty_home": 3.20,
        "b2b_penalty_away": 0.80,
        "schedule_density_drag": True,
        "rest_weight": 0.10,
        "logistic_scale": 11.8,
        "margin_sd_base": 14.5,
        "dynamic_volatility": True,
        "use_pace_sd_scaling": True,
    }
    v6_metrics = simulate_model(holdout_features, **v6_params)

    print("\n" + "-" * 70)
    print("LOCKED HOLDOUT PERFORMANCE SUMMARY (n=636 games across 129 dates):")
    print("-" * 70)
    print(f"{'Metric':<25} {'Incumbent v5':<18} {'Challenger v6':<18} {'Delta (v6 - v5)':<15}")
    print("-" * 70)
    print(
        f"{'Margin MAE':<25} {v5_metrics['mae_margin']:<18.4f} {v6_metrics['mae_margin']:<18.4f} {v6_metrics['mae_margin'] - v5_metrics['mae_margin']:<+15.4f}"
    )
    print(
        f"{'Margin RMSE':<25} {v5_metrics['rmse_margin']:<18.4f} {v6_metrics['rmse_margin']:<18.4f} {v6_metrics['rmse_margin'] - v5_metrics['rmse_margin']:<+15.4f}"
    )
    print(
        f"{'Total MAE':<25} {v5_metrics['mae_total']:<18.4f} {v6_metrics['mae_total']:<18.4f} {v6_metrics['mae_total'] - v5_metrics['mae_total']:<+15.4f}"
    )
    print(
        f"{'Moneyline Accuracy':<25} {v5_metrics['accuracy']:<18.4f} {v6_metrics['accuracy']:<18.4f} {v6_metrics['accuracy'] - v5_metrics['accuracy']:<+15.4f}"
    )
    print(
        f"{'Moneyline Brier Score':<25} {v5_metrics['brier']:<18.4f} {v6_metrics['brier']:<18.4f} {v6_metrics['brier'] - v5_metrics['brier']:<+15.4f}"
    )
    print("-" * 70)

    # 3. STATISTICAL SIGNIFICANCE: Paired Date Bootstrap
    print("\nComputing Clustered Paired Date Bootstrap (1,000 iterations)...")
    bootstrap_rows = []
    trend_w_v5 = 1.0 - v5_params["elo_weight"]
    trend_w_v6 = 1.0 - v6_params["elo_weight"]

    for r in holdout_features:
        # Calculate v5 margin
        h_pts_5 = r.home_raw_points - (v5_params["b2b_penalty_home"] if r.home_b2b else 0.0)
        a_pts_5 = r.away_raw_points - (v5_params["b2b_penalty_away"] if r.away_b2b else 0.0)
        if r.home_3in4 and not r.home_b2b:
            h_pts_5 -= 0.8
        if r.away_3in4 and not r.away_b2b:
            a_pts_5 -= 1.2
        eff_hca_5 = v5_params["hca_points"]
        if r.is_denver_home:
            eff_hca_5 += 1.4
        elif r.is_utah_home:
            eff_hca_5 += 0.8
        if (r.is_denver_home or r.is_utah_home) and (r.away_b2b or r.away_3in4):
            eff_hca_5 += 1.0
        exp_m_5 = (
            v5_params["elo_weight"] * r.elo_margin
            + trend_w_v5 * (h_pts_5 - a_pts_5 + eff_hca_5)
            + v5_params["rest_weight"] * r.rest_diff
        )
        err_5 = abs(exp_m_5 - r.actual_margin)

        # Calculate v6 margin
        h_pts_6 = r.home_raw_points - (v6_params["b2b_penalty_home"] if r.home_b2b else 0.0)
        a_pts_6 = r.away_raw_points - (v6_params["b2b_penalty_away"] if r.away_b2b else 0.0)
        if r.home_3in4 and not r.home_b2b:
            h_pts_6 -= 0.8
        if r.away_3in4 and not r.away_b2b:
            a_pts_6 -= 1.2
        exp_m_6 = (
            v6_params["elo_weight"] * r.elo_margin
            + trend_w_v6 * (h_pts_6 - a_pts_6 + v6_params["hca_points"])
            + v6_params["rest_weight"] * r.rest_diff
        )
        err_6 = abs(exp_m_6 - r.actual_margin)

        bootstrap_rows.append(
            {
                "date": r.date,
                "event_id": r.event_id,
                "control_abs_error": err_5,
                "candidate_abs_error": err_6,
            }
        )

    bootstrap_res = paired_date_bootstrap(bootstrap_rows, iterations=2000, seed=42)
    print(f"Clustered Paired Gain (v5 MAE - v6 MAE): {bootstrap_res['mean_gain']:+.4f} pts")
    print(f"95% Bootstrap CI: [{bootstrap_res['ci_95'][0]:+.4f}, {bootstrap_res['ci_95'][1]:+.4f}]")
    print(f"Probability Gain > 0: {bootstrap_res['p_gain_positive'] * 100:.1f}%")

    # VERDICT
    is_improved = (
        v6_metrics["mae_margin"] < v5_metrics["mae_margin"]
        and v6_metrics["rmse_margin"] < v5_metrics["rmse_margin"]
        and v6_metrics["brier"] <= v5_metrics["brier"]
        and bootstrap_res["p_gain_positive"] >= 0.90
    )
    print(f"\nStatistical Superiority Verdict: {'QUALIFIED (PROMOTABLE)' if is_improved else 'FAILED'}")

    report = {
        "evaluation_cohort": "locked_holdout",
        "sample_size": len(holdout_features),
        "calendar_dates": len({r.date for r in holdout_features}),
        "date_range": [holdout_features[0].date, holdout_features[-1].date],
        "incumbent_v5": v5_metrics,
        "challenger_v6": v6_metrics,
        "deltas": {
            "mae_margin_reduction": round(v5_metrics["mae_margin"] - v6_metrics["mae_margin"], 4),
            "rmse_margin_reduction": round(v5_metrics["rmse_margin"] - v6_metrics["rmse_margin"], 4),
            "brier_reduction": round(v5_metrics["brier"] - v6_metrics["brier"], 4),
            "accuracy_increase": round(v6_metrics["accuracy"] - v5_metrics["accuracy"], 4),
        },
        "paired_bootstrap": bootstrap_res,
        "verdict": "QUALIFIED" if is_improved else "FAILED",
        "challenger_params": v6_params,
    }

    report_path = Path("outputs/analysis/nba_v6_locked_holdout_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Saved full evaluation report to {report_path}")


if __name__ == "__main__":
    run_holdout_evaluation()
