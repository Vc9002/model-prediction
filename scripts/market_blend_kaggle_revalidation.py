"""Re-run the NBA v7 market-blend ablation using real historical closing lines backfilled
from Kaggle (data/odds/nba/historical_closing_lines/), replacing the earlier n=92
ESPN-pickcenter-only sample with ~3,200+ games of real coverage. Reuses evaluate_engine/
load_nba_games from test_combo_features_v7.py and the same chronological 60/20/20 split
as every prior NBA v7 ablation round. Fit/select on validation only; holdout touched once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_v5_pit_walkforward import split_complete_dates
from test_combo_features_v7 import evaluate_engine, load_nba_games

from model_prediction.models.nba_v7 import NBAStructuralV7Engine

ODDS_PATH = Path("data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl")


def load_kaggle_odds() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with open(ODDS_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            try:
                spread = float(row["spread"])
                total = float(row["total"])
            except (TypeError, ValueError):
                continue
            # Kaggle `spread` is the home-favored-by magnitude when whos_favored == "home";
            # normalize to spread_away_line convention used by UpcomingGame (home margin line).
            home_margin_line = spread if row.get("whos_favored") == "home" else -spread
            out[str(row["event_id"])] = {
                "spread_away_line": -home_margin_line,
                "total_line": total,
            }
    return out


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    odds = load_kaggle_odds()
    print(f"Kaggle-backed real odds coverage: {len(odds)}/{len(all_games)} games")
    val_odds_n = sum(1 for g in val_games if g.event_id in odds)
    hold_odds_n = sum(1 for g in holdout_games if g.event_id in odds)
    print(
        f"Odds coverage in validation: {val_odds_n}/{len(val_games)}; in holdout: {hold_odds_n}/{len(holdout_games)}"
    )

    print("\n=== MARKET BLEND (Kaggle real odds, full-scale subset), VALIDATION ===")
    val_results = {}
    for bw in [0.0, 0.2, 0.35, 0.5, 0.65, 0.8]:
        eng = NBAStructuralV7Engine(market_blend_weight=bw)
        m = evaluate_engine(eng, all_games, val_games, odds=odds, use_real_odds=True)
        val_results[bw] = m
        print(f"blend_weight={bw}: {m}")

    best_bw = min(val_results, key=lambda k: val_results[k].moneyline_brier)
    print(f"\n=== SELECTED ON VALIDATION: blend_weight={best_bw} -> {val_results[best_bw]} ===")
    print(f"(baseline blend_weight=0.0 for reference: {val_results[0.0]})")

    baseline = NBAStructuralV7Engine(market_blend_weight=0.0)
    print("\n--- BASELINE v7 (blend=0.0) ON LOCKED HOLDOUT, Kaggle odds subset (touched once) ---")
    baseline_hold = evaluate_engine(baseline, all_games, holdout_games, odds=odds, use_real_odds=True)
    print(baseline_hold)

    best_eng = NBAStructuralV7Engine(market_blend_weight=best_bw)
    print(f"\n--- SELECTED blend_weight={best_bw} ON LOCKED HOLDOUT, Kaggle odds subset (touched once) ---")
    selected_hold = evaluate_engine(best_eng, all_games, holdout_games, odds=odds, use_real_odds=True)
    print(selected_hold)

    print("\n--- BASELINE v7 ON FULL LOCKED HOLDOUT (no odds restriction, for reference) ---")
    full_baseline_hold = evaluate_engine(baseline, all_games, holdout_games)
    print(full_baseline_hold)

    out = {
        "split_meta": meta,
        "odds_coverage_total": len(odds),
        "odds_coverage_validation": val_odds_n,
        "odds_coverage_holdout": hold_odds_n,
        "validation_results": {str(k): vars(v) for k, v in val_results.items()},
        "selected_blend_weight": best_bw,
        "baseline_holdout_odds_subset": vars(baseline_hold),
        "selected_holdout_odds_subset": vars(selected_hold),
        "baseline_holdout_full_set": vars(full_baseline_hold),
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_kaggle_market_blend_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_kaggle_market_blend_result.json")


if __name__ == "__main__":
    run()
