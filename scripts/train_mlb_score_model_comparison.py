"""Real chronological OOF comparison: coherent score-distribution models
(MLBTwoHeadModel vs. the new XGBoostTwoHeadModel) vs. the direct XGBoost
binary classifier -- CLAUDE.md's next-phase Task 13.

Real gap this closes: the existing direct XGBoost challenger
(XGBoostChallenger, compared in train_mlb_xgboost_ensemble.py) is a real,
useful independent moneyline challenger, but it has no joint score
distribution behind it -- nothing tests whether nonlinear ML actually
improves the *score engine* itself (spread/total, not just moneyline).
XGBoostTwoHeadModel (models/__init__.py) is a coherent alternative: XGBoost
regression heads feeding the identical JointScoreDistribution reconciliation
MLBTwoHeadModel uses, so moneyline/spread/total all derive from one real
joint distribution either way.

This script reports moneyline log loss/Brier for all three, plus a real
totals-probability comparison between the two coherent score models (the
direct classifier has no totals probability at all -- that's exactly the
point being tested). Registry-safe: does not touch
test_consumption_registry.json.

Task 13.5 fix: the totals diagnostic previously chose its evaluation line
from the game's own realized total_runs ("a line the actual total legitimately
straddles") -- that is fine for a quick sanity check but cannot be used for
model selection, since the line itself is a function of the outcome being
scored. Real timestamp-valid pregame market total lines aren't wired into
this comparison yet (that's Task 19's prospective market capture), so this
uses a fixed, predeclared line grid instead -- chosen before looking at any
result, identical across every game and every model, never a function of
what actually happened.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_score_model_comparison.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import (
    MLB_DIFFERENTIAL_FEATURES,
    MLB_INTENSITY_FEATURES,
    dedupe_scoreboard,
)
from model_prediction.rebuild.models import MLBTwoHeadModel, XGBoostTwoHeadModel
from model_prediction.rebuild.validation import brier_score, expanding_folds, log_loss
from model_prediction.rebuild.xgboost_stress import nested_xgboost_fold

HORIZON = "late"
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES
XGB_DIRECT_FEATURES = list(dict.fromkeys(INTENSITY_FEATURES + DIFFERENTIAL_FEATURES))
# Predeclared, fixed before looking at any result -- never derived from a
# realized game outcome. Real MLB total-runs are always integers, so the
# half-integer lines here can never push in reality; the whole-integer
# lines (8.0, 9.0) real can, and that's reported explicitly, not folded
# silently into over/under.
TOTALS_LINE_GRID = [7.5, 8.0, 8.5, 9.0, 9.5]


def _home_win_labels(df: pl.DataFrame) -> list[int]:
    return [1 if r["home_score"] > r["away_score"] else 0 for r in df.iter_rows(named=True)]


def main() -> None:
    sb_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    if completed.height == 0:
        print("No completed games. Stopping honestly.")
        sys.exit(0)
    start_date = completed["event_start_utc"][0][:10]
    end_date = completed["event_start_utc"][-1][:10]

    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("event_start_utc") if dataset.features.height else dataset.features
    print(f"1. Feature rows: {dataset.matched_games} matched; dataset_hash={dataset.dataset_hash[:12]}")

    if features.height < 30:
        print("Not enough matched games. Stopping honestly.")
        sys.exit(0)

    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates)")

    oof: dict[str, list[float]] = {"two_head": [], "xgb_two_head": [], "xgb_direct": []}
    # Real totals-probability comparison between the two coherent score
    # models only -- the direct classifier has no totals probability to
    # compare, which is exactly what this task is checking for. One
    # record list per (model, predeclared line).
    totals_records: dict[str, dict[float, list[dict]]] = {
        "two_head": {line: [] for line in TOTALS_LINE_GRID},
        "xgb_two_head": {line: [] for line in TOTALS_LINE_GRID},
    }
    y_true: list[int] = []
    per_fold_report = []

    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue

        y_val_fold = _home_win_labels(val_df)
        val_rows = list(val_df.iter_rows(named=True))

        two_head = MLBTwoHeadModel(seed=42)
        two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        two_head_probs = [two_head.predict_row(r["event_id"], r).home_win_prob for r in val_rows]

        xgb_two_head = XGBoostTwoHeadModel(seed=42)
        xgb_two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        xgb_two_head_probs = [xgb_two_head.predict_row(r["event_id"], r).home_win_prob for r in val_rows]

        # Task 9's nested CV, reused as-is -- the direct classifier's own
        # early-stopping/hyperparameter selection must not see y_val_fold.
        X_train = train_df.select(XGB_DIRECT_FEATURES).to_numpy()
        y_train_arr = train_df.select(
            (pl.col("home_score") > pl.col("away_score")).cast(pl.Int8).alias("y")
        ).to_numpy().ravel()
        X_val = val_df.select(XGB_DIRECT_FEATURES).to_numpy()
        nested_result = nested_xgboost_fold(X_train, y_train_arr, X_val, y_val_fold, fold_index=fold.fold_index)
        xgb_direct_probs = nested_result.outer_probs

        oof["two_head"].extend(two_head_probs)
        oof["xgb_two_head"].extend(xgb_two_head_probs)
        oof["xgb_direct"].extend(xgb_direct_probs)
        y_true.extend(y_val_fold)

        # Task 13.5: every real game in this fold is priced against the
        # SAME predeclared line grid (TOTALS_LINE_GRID) -- never a line
        # chosen from that game's own realized total_runs. Both coherent
        # models price the identical real lines for the identical real
        # games.
        for r in val_rows:
            pred_th = two_head.predict_row(r["event_id"], r)
            pred_xgb = xgb_two_head.predict_row(r["event_id"], r)
            for line in TOTALS_LINE_GRID:
                breakdown_th = two_head.distribution.total_market_breakdown(pred_th, line)
                breakdown_xgb = xgb_two_head.distribution.total_market_breakdown(pred_xgb, line)
                is_push = float(r["total_runs"]) == line
                totals_records["two_head"][line].append({
                    "over": breakdown_th["over"], "under": breakdown_th["under"], "push": breakdown_th["push"],
                    "is_push": is_push, "label": None if is_push else (1 if r["total_runs"] > line else 0),
                })
                totals_records["xgb_two_head"][line].append({
                    "over": breakdown_xgb["over"], "under": breakdown_xgb["under"], "push": breakdown_xgb["push"],
                    "is_push": is_push, "label": None if is_push else (1 if r["total_runs"] > line else 0),
                })

        fold_report = {
            "fold": fold.fold_index, "train_n": train_df.height, "val_n": val_df.height,
            "two_head": {"log_loss": log_loss(y_val_fold, two_head_probs), "brier": brier_score(y_val_fold, two_head_probs)},
            "xgb_two_head": {"log_loss": log_loss(y_val_fold, xgb_two_head_probs), "brier": brier_score(y_val_fold, xgb_two_head_probs)},
            "xgb_direct": {"log_loss": nested_result.outer_log_loss, "brier": nested_result.outer_brier},
        }
        per_fold_report.append(fold_report)
        print(f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} "
              f"two_head_ll={fold_report['two_head']['log_loss']:.3f} "
              f"xgb_two_head_ll={fold_report['xgb_two_head']['log_loss']:.3f} "
              f"xgb_direct_ll={fold_report['xgb_direct']['log_loss']:.3f}")

    if len(y_true) < 10:
        print("Too few real OOF predictions. Stopping honestly.")
        sys.exit(0)

    print(f"\n3. Moneyline OOF comparison ({len(y_true)} real out-of-fold predictions):")
    ml_summary = {}
    for model_name, probs in oof.items():
        ml_summary[model_name] = {"log_loss": log_loss(y_true, probs), "brier": brier_score(y_true, probs)}
        print(f"   {model_name:15s}: log_loss={ml_summary[model_name]['log_loss']:.4f} "
              f"brier={ml_summary[model_name]['brier']:.4f}")

    print(f"\n4. Totals OOF comparison, predeclared line grid {TOTALS_LINE_GRID} "
          f"(coherent score models only -- xgb_direct has no totals probability):")
    totals_summary: dict[str, dict[str, dict]] = {"two_head": {}, "xgb_two_head": {}}
    for model_name, by_line in totals_records.items():
        for line, records in by_line.items():
            n_total = len(records)
            n_push = sum(1 for rec in records if rec["is_push"])
            non_push = [rec for rec in records if not rec["is_push"]]
            mean_over = sum(rec["over"] for rec in records) / n_total if n_total else float("nan")
            mean_under = sum(rec["under"] for rec in records) / n_total if n_total else float("nan")
            mean_push = sum(rec["push"] for rec in records) / n_total if n_total else float("nan")
            entry = {
                "line": line, "n": n_total, "n_push_real": n_push,
                "mean_over_probability": mean_over, "mean_under_probability": mean_under,
                "mean_push_probability": mean_push,
            }
            if len(non_push) >= 5:
                labels = [rec["label"] for rec in non_push]
                over_probs = [rec["over"] for rec in non_push]
                entry["log_loss"] = log_loss(labels, over_probs)
                entry["brier"] = brier_score(labels, over_probs)
            else:
                entry["log_loss"] = None
                entry["brier"] = None
            totals_summary[model_name][str(line)] = entry
            ll_str = f"{entry['log_loss']:.4f}" if entry["log_loss"] is not None else "n/a (too few non-push obs)"
            print(f"   {model_name:15s} line={line:4.1f}: n={n_total} push={n_push} "
                  f"mean_over={mean_over:.3f} log_loss={ll_str}")

    print(
        "\n   Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). No promotion decision is made here.\n"
        "   Totals lines above are a fixed, predeclared grid (Task 13.5) -- never\n"
        "   chosen from a game's own realized total_runs. Real timestamp-valid\n"
        "   pregame market lines are not wired into this comparison yet (Task 19's\n"
        "   prospective market capture); until then this measures relative model\n"
        "   calibration against a fixed grid, not real market-beating performance."
    )

    results_path = Path("outputs/rebuild/mlb_score_model_comparison.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "matched_games": dataset.matched_games,
        "n_moneyline_oof": len(y_true),
        "totals_line_grid": TOTALS_LINE_GRID,
        "per_fold": per_fold_report,
        "moneyline_oof_summary": ml_summary,
        "totals_oof_summary": totals_summary,
    }, indent=2, default=str))
    print(f"5. Results saved to {results_path}")


if __name__ == "__main__":
    main()
