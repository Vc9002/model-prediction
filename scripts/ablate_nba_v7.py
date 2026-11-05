"""Walk-forward ablation of NBA Structural v7 engine parameters (Phase 1 of the v8 overhaul).

Targets the engine actually used for spread/total pricing today
(NBAStructuralV7Engine), not the legacy BasketballModel class that
scripts/ablate_nba_parameters.py tests. Uses the same chronological
60/20/20 date-clustered split as nba_v5_pit_walkforward.split_complete_dates.
Fits/selects on train+validation only; holdout is touched once, at the end,
for the single selected candidate.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine


@dataclass
class EvalMetrics:
    sample_size: int
    mae_margin: float
    mae_total: float
    moneyline_accuracy: float
    moneyline_brier: float
    home_spread_acc: float
    away_spread_acc: float
    early_season_over_rate: float


def load_nba_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [
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
        for r in rows
    ]


def evaluate_engine(
    engine: NBAStructuralV7Engine,
    all_games: list[GameRecord],
    eval_games: list[GameRecord],
) -> EvalMetrics:
    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)
    sorted_dates = sorted(dates_map.keys())

    rows: list[dict[str, Any]] = []
    season_start_by_year: dict[int, str] = {}

    for d in sorted_dates:
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]
        upcoming = [
            UpcomingGame(
                event_id=g.event_id,
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                spread_away_line=0.0,
                total_line=228.5,
                league="NBA",
            )
            for g in games_today
        ]
        preds = engine.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        for g in games_today:
            actual_margin = g.home_score - g.away_score
            actual_total = g.home_score + g.away_score
            actual_home_win = 1.0 if actual_margin > 0 else 0.0

            ml_pred = by_event[g.event_id].get("moneyline")
            spread_pred = by_event[g.event_id].get("spread")
            total_pred = by_event[g.event_id].get("total")

            exp_margin = float(spread_pred.feature_basis.get("expected_margin", 0.0)) if spread_pred else 0.0
            exp_total = float(total_pred.feature_basis.get("expected_total", 228.5)) if total_pred else 228.5
            p_home = max(
                1e-4, min(1 - 1e-4, float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5)
            )

            # Pick-ATS(0) proxy: predicted side vs line=0 (pure margin sign) matching actual margin sign.
            pred_home_covers_zero = 1.0 if exp_margin > 0 else 0.0
            actual_home_covers_zero = 1.0 if actual_margin > 0 else 0.0
            spread_correct = 1.0 if pred_home_covers_zero == actual_home_covers_zero else 0.0

            if g.season_year is not None:
                season_start_by_year.setdefault(int(g.season_year), g.event_start_utc)
            days_into_season = None
            if g.season_year is not None and int(g.season_year) in season_start_by_year:
                from datetime import datetime

                try:
                    d0 = datetime.fromisoformat(season_start_by_year[int(g.season_year)])
                    d1 = datetime.fromisoformat(g.event_start_utc)
                    days_into_season = (d1 - d0).days
                except ValueError:
                    days_into_season = None

            rows.append(
                {
                    "abs_err_margin": abs(exp_margin - actual_margin),
                    "abs_err_total": abs(exp_total - actual_total),
                    "brier": (p_home - actual_home_win) ** 2,
                    "correct": 1.0 if (p_home >= 0.5) == bool(actual_home_win) else 0.0,
                    "home_spread_correct": spread_correct,
                    "is_home_side_pred": pred_home_covers_zero,
                    "early_season": bool(days_into_season is not None and days_into_season <= 7),
                    "pred_over": 1.0 if exp_total > 228.5 else 0.0,
                    "actual_over": 1.0 if actual_total > 228.5 else 0.0,
                }
            )

    n = len(rows)
    home_side = [r for r in rows if r["is_home_side_pred"] == 1.0]
    away_side = [r for r in rows if r["is_home_side_pred"] == 0.0]
    early = [r for r in rows if r["early_season"]]
    return EvalMetrics(
        sample_size=n,
        mae_margin=round(float(np.mean([r["abs_err_margin"] for r in rows])), 4),
        mae_total=round(float(np.mean([r["abs_err_total"] for r in rows])), 4),
        moneyline_accuracy=round(float(np.mean([r["correct"] for r in rows])), 4),
        moneyline_brier=round(float(np.mean([r["brier"] for r in rows])), 4),
        home_spread_acc=round(float(np.mean([r["home_spread_correct"] for r in home_side])), 4)
        if home_side
        else float("nan"),
        away_spread_acc=round(float(np.mean([r["home_spread_correct"] for r in away_side])), 4)
        if away_side
        else float("nan"),
        early_season_over_rate=round(float(np.mean([r["actual_over"] for r in early])), 4)
        if early
        else float("nan"),
    )


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    baseline = NBAStructuralV7Engine()  # v7 defaults, incl. elo_home_advantage=38.0, home_court_points=1.75
    print("\n--- BASELINE v7 (defaults) ON VALIDATION ---")
    baseline_val = evaluate_engine(baseline, all_games, val_games)
    print(baseline_val)

    candidates = {
        "lower_hca": {"elo_home_advantage": 25.0, "home_court_points": 1.1},
        "lower_hca_2": {"elo_home_advantage": 18.0, "home_court_points": 0.8},
        "zero_hca": {"elo_home_advantage": 10.0, "home_court_points": 0.4},
        "b2b_retune": {"b2b_penalty_home": 1.2, "b2b_penalty_away": 1.8},
        "combo": {
            "elo_home_advantage": 18.0,
            "home_court_points": 0.8,
            "b2b_penalty_home": 1.2,
            "b2b_penalty_away": 1.8,
        },
        "market_blend_20": {"market_blend_weight": 0.20},
        "market_blend_35": {"market_blend_weight": 0.35},
        "market_blend_50": {"market_blend_weight": 0.50},
        "ml_margin_weight_50": {"moneyline_margin_weight": 0.50},
        "ml_margin_weight_60": {"moneyline_margin_weight": 0.60},
        "v8_combo": {"market_blend_weight": 0.20, "moneyline_margin_weight": 0.45},
    }

    results: dict[str, EvalMetrics] = {"baseline": baseline_val}
    for name, overrides in candidates.items():
        eng = NBAStructuralV7Engine(**overrides)
        m = evaluate_engine(eng, all_games, val_games)
        results[name] = m
        print(f"\n--- {name} {overrides} ON VALIDATION ---")
        print(m)

    # Select by composite score: moneyline brier + 0.01*mae_margin + home/away spread-acc gap penalty
    def score(m: EvalMetrics) -> float:
        gap_penalty = 0.0
        if not (np.isnan(m.home_spread_acc) or np.isnan(m.away_spread_acc)):
            gap_penalty = abs(m.home_spread_acc - m.away_spread_acc)
        return m.moneyline_brier + 0.01 * m.mae_margin + 0.5 * gap_penalty

    best_name = min(results, key=lambda k: score(results[k]))
    print(f"\n=== SELECTED ON VALIDATION: {best_name} (score={score(results[best_name]):.4f}) ===")

    best_overrides = candidates.get(best_name, {})
    final_engine = NBAStructuralV7Engine(**best_overrides)
    print("\n--- FINAL CANDIDATE ON LOCKED HOLDOUT (touched once) ---")
    holdout_metrics = evaluate_engine(final_engine, all_games, holdout_games)
    print(holdout_metrics)

    print("\n--- BASELINE v7 ON LOCKED HOLDOUT (for comparison) ---")
    baseline_holdout = evaluate_engine(baseline, all_games, holdout_games)
    print(baseline_holdout)

    out = {
        "selected_candidate": best_name,
        "selected_overrides": best_overrides,
        "validation_results": {k: vars(v) for k, v in results.items()},
        "holdout_selected": vars(holdout_metrics),
        "holdout_baseline": vars(baseline_holdout),
        "split_meta": meta,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_ablation_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_ablation_result.json")


if __name__ == "__main__":
    run()
