"""Train MLB two-head model on real Statcast-derived pregame features.

Checkpoint 6 of the CLAUDE.md takeover plan. Replaces train_mlb_rebuild.py's
rolling-team-score baseline (its own docstring: "the full Statcast/weather/
lineup/pitcher feature set requires the corresponding collectors to be
completed first") now that Checkpoint 5 built and validated real per-starter,
per-bullpen, park, and weather features from actual Statcast pitch data.

Real starter/bullpen/park/weather features, computed strictly from data
before each game's date (see mlb_features.py's own point-in-time tests).
Chronological expanding-window folds — no random split.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_rebuild_real_features.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import PlattCalibrator
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import (
    MLB_DIFFERENTIAL_FEATURES,
    MLB_INTENSITY_FEATURES,
    dedupe_scoreboard,
)
from model_prediction.rebuild.models import MLBTwoHeadModel
from model_prediction.rebuild.validation import (
    brier_score,
    build_split_manifest,
    date_cluster_split,
    ece,
    expanding_folds,
    log_loss,
)

HORIZON = "late"

# Task 5: the one shared feature-list definition (mlb_features.py), also
# used by train_mlb_xgboost_ensemble.py and mlb_shadow_pipeline.py -- not
# redefined here.
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES


def main() -> None:
    sb_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    print(f"1. Scoreboard: {sb.height} total rows (deduped), {completed.height} completed games")

    if completed.height == 0:
        print("No completed games. Stopping honestly, not faking a result.")
        sys.exit(0)
    start_date = completed["event_start_utc"][0][:10]
    end_date = completed["event_start_utc"][-1][:10]

    # Task 4: the one authoritative historical dataset builder, replacing
    # this script's own copy of the feature-row loop (now shared with
    # train_mlb_xgboost_ensemble.py, train_mlb_feature_ablation.py, and
    # mlb_shadow_pipeline.py's walk-forward retraining).
    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("game_date") if dataset.features.height else dataset.features
    print(
        f"2. Dataset builder: {dataset.matched_games} matched games in [{start_date}, {end_date}] "
        f"({dataset.unmatched_games} team-unresolved, not fabricated); dataset_hash={dataset.dataset_hash[:12]}"
    )
    print(
        f"3. Feature rows: {dataset.matched_games} matched; "
        f"{dataset.starters_known_games}/{dataset.matched_games} have a point-in-time-valid probable "
        f"starter for both teams at horizon={HORIZON} "
        f"({dataset.matched_games - dataset.starters_known_games} flagged starters_known=0, "
        f"not silently filled with the actual starter)"
    )

    if features.height < 30:
        print(
            "Not enough matched games to train meaningfully (need >=30). Stopping honestly, not faking a result."
        )
        sys.exit(0)

    # Task 8 fix (see outputs/rebuild/takeover_status.md): this used to pass
    # the full event_start_utc timestamp (near-per-game granularity)
    # because passing the calendar-day game_date alone made val_size/
    # test_size (sized in games, not real days) too coarse and produced 0
    # folds. That was real, but it means two games on the identical
    # calendar date could land on opposite sides of a fold boundary --
    # exactly the same-day contamination CLAUDE.md's Part 2 SS2 requires
    # folds to prevent ("No game on the same calendar date may be placed
    # in training while another game from that date is in outer
    # validation"). Fixed by sizing val_size/test_size in real *date*
    # units (a fraction of the real distinct dates available), not game
    # count -- expanding_folds() already treats each unique value in its
    # `dates` argument as one indivisible cluster; passing real calendar
    # dates now makes that cluster a real calendar date, not a fold
    # boundary that can fall between two same-day games.
    features = features.sort("event_start_utc")
    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(
        f"4. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates, "
        f"val_size={val_size_days}d test_size={test_size_days}d gap=1d)"
    )

    fold_metrics = []
    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue

        model = MLBTwoHeadModel(seed=42)
        model.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        y_true, y_prob = [], []
        for row in val_df.iter_rows(named=True):
            pred = model.predict_row(row["event_id"], row)
            y_true.append(1 if row["home_score"] > row["away_score"] else 0)
            y_prob.append(pred.home_win_prob)

        fold_metrics.append(
            {
                "fold": fold.fold_index,
                "train_n": train_df.height,
                "val_n": val_df.height,
                "log_loss": log_loss(y_true, y_prob),
                "brier": brier_score(y_true, y_prob),
                "ece": ece(y_true, y_prob),
            }
        )
        print(
            f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} "
            f"ll={fold_metrics[-1]['log_loss']:.3f} brier={fold_metrics[-1]['brier']:.3f}"
        )

    print(f"5. Validation complete: {len(fold_metrics)} folds evaluated")

    # ── Final model: train / calibration / untouched final test ──────────────
    # Real bug fixed here (see outputs/rebuild/takeover_status.md Checkpoint
    # 9): this used to fit the Platt calibrator on test_final and then
    # evaluate the calibrated model on that same test_final — the calibrator
    # is specifically optimized to improve log loss/Brier on whatever data
    # it's fit on, so evaluating it there biases the "held-out" metrics
    # favorably. A genuinely untouched final test needs its own separate
    # chunk that neither the model nor the calibrator ever sees before the
    # single final evaluation below.
    #
    # Task 8 fix: this previously sliced by real game *count*
    # (`features[n - test_size:]`), the identical same-day-contamination
    # bug the fold construction above had -- two games sharing the
    # identical calendar date could land in different buckets.
    # date_cluster_split() makes every one of a selected date's real
    # games move together as one indivisible cluster.
    test_size_dates = max(1, n_unique_dates // 6)
    calib_size_dates = max(1, n_unique_dates // 6)
    train_dates, calib_dates, test_dates = date_cluster_split(
        game_dates,
        test_size=test_size_dates,
        calib_size=calib_size_dates,
    )
    train_final = features.filter(pl.col("game_date").is_in(train_dates))
    calib_final = features.filter(pl.col("game_date").is_in(calib_dates))
    test_final = features.filter(pl.col("game_date").is_in(test_dates))
    print(
        f"5b. Split: train={train_final.height} ({len(train_dates)} dates), "
        f"calibration={calib_final.height} ({len(calib_dates)} dates, fits Platt only), "
        f"test={test_final.height} ({len(test_dates)} dates, untouched until final evaluation)"
    )

    final_model = MLBTwoHeadModel(seed=42)
    final_model.fit(train_final, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

    calib_raw_probs, calib_labels = [], []
    for row in calib_final.iter_rows(named=True):
        pred = final_model.predict_row(row["event_id"], row)
        calib_raw_probs.append(pred.home_win_prob)
        calib_labels.append(1 if row["home_score"] > row["away_score"] else 0)
    cal = PlattCalibrator().fit(calib_raw_probs, calib_labels)

    raw_probs, labels = [], []
    for row in test_final.iter_rows(named=True):
        pred = final_model.predict_row(row["event_id"], row)
        raw_probs.append(pred.home_win_prob)
        labels.append(1 if row["home_score"] > row["away_score"] else 0)
    calibrated = [cal.transform(p) for p in raw_probs]

    final_metrics = {
        "log_loss": log_loss(labels, calibrated),
        "brier": brier_score(labels, calibrated),
        "ece": ece(labels, calibrated),
        "raw_brier": brier_score(labels, raw_probs),
        "accuracy": sum(1 for p, y in zip(calibrated, labels) if (p >= 0.5) == (y == 1)) / len(labels),
    }
    print(
        f"6. Held-out test ({len(labels)} games): "
        f"brier={final_metrics['brier']:.4f} (raw {final_metrics['raw_brier']:.4f}) "
        f"ll={final_metrics['log_loss']:.4f} ece={final_metrics['ece']:.4f} "
        f"acc={final_metrics['accuracy']:.3f}"
    )

    # ── Diagnostic: cold-start missingness composition, train vs test ────────
    # A short real backfill window (10 days) means many early rows have zero
    # prior starts for a pitcher -> availability=0 -> zeroed features fed to
    # the model as if they were real signal. That composition can differ
    # sharply between train and test purely from the backfill window's
    # shape, not from anything the model "learned" wrong. Reporting this
    # explicitly rather than only the headline metric above, per this
    # project's own "missingness is data" principle (CLAUDE.md Part 1 S11).
    train_avail = (
        train_final["home_sp_availability"].mean() + train_final["away_sp_availability"].mean()
    ) / 2
    test_avail = (test_final["home_sp_availability"].mean() + test_final["away_sp_availability"].mean()) / 2
    print(
        f"7. Cold-start composition: train mean starter-availability={train_avail:.3f}, "
        f"test mean starter-availability={test_avail:.3f} "
        f"({'MISMATCHED — interpret the headline metric with caution' if abs(train_avail - test_avail) > 0.2 else 'comparable'})"
    )

    # ── Quality-filtered comparison: both starters have real prior history ──
    both_avail_test = test_final.filter(
        (pl.col("home_sp_availability") == 1) & (pl.col("away_sp_availability") == 1)
    )
    quality_metrics = None
    if both_avail_test.height >= 10:
        q_raw, q_labels = [], []
        for row in both_avail_test.iter_rows(named=True):
            pred = final_model.predict_row(row["event_id"], row)
            q_raw.append(pred.home_win_prob)
            q_labels.append(1 if row["home_score"] > row["away_score"] else 0)
        q_cal = [cal.transform(p) for p in q_raw]
        quality_metrics = {
            "n": both_avail_test.height,
            "brier": brier_score(q_labels, q_cal),
            "log_loss": log_loss(q_labels, q_cal),
            "accuracy": sum(1 for p, y in zip(q_cal, q_labels) if (p >= 0.5) == (y == 1)) / len(q_labels),
        }
        print(
            f"8. Quality-filtered test (both starters have real history, "
            f"n={quality_metrics['n']}): brier={quality_metrics['brier']:.4f} "
            f"ll={quality_metrics['log_loss']:.4f} acc={quality_metrics['accuracy']:.3f}"
        )
    else:
        print(
            f"8. Quality-filtered test: only {both_avail_test.height} rows have both starters "
            f"with real history — too few to report separately"
        )

    artifact = final_model.to_artifact()
    artifact.update(
        {
            "feature_set": "real_statcast_v1",
            "intensity_features": INTENSITY_FEATURES,
            "differential_features": DIFFERENTIAL_FEATURES,
            "calibration": {"intercept": cal.intercept, "slope": cal.slope},
            "fold_metrics": fold_metrics,
            "final_metrics": final_metrics,
            "quality_filtered_metrics": quality_metrics,
            "cold_start_composition": {
                "train_mean_availability": train_avail,
                "test_mean_availability": test_avail,
            },
            "train_games": train_final.height,
            "calibration_games": calib_final.height,
            "test_games": test_final.height,
            "total_completed_games": completed.height,
            "matched_games": features.height,
            "unmatched_games": dataset.unmatched_games,
        }
    )

    artifact_dir = Path("config/models/challengers")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "mlb-two-head-real-features-v1.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"\n9. Artifact saved to {artifact_path}")

    results_path = Path("outputs/rebuild/mlb_training_results_real_features.json")
    results_path.write_text(
        json.dumps(
            {
                "model_version": artifact["model_id"],
                "feature_set": "real_statcast_v1",
                "matched_games": features.height,
                "unmatched_games": dataset.unmatched_games,
                "fold_metrics": fold_metrics,
                "final_metrics": final_metrics,
                "quality_filtered_metrics": quality_metrics,
                "cold_start_composition": {
                    "train_mean_availability": train_avail,
                    "test_mean_availability": test_avail,
                },
                "artifact_hash": artifact.get("artifact_hash", ""),
            },
            indent=2,
        )
    )
    print(f"10. Results saved to {results_path}")

    # Real, persisted split manifest (CLAUDE.md Part 2 SS2 "Persist a split
    # manifest" / Definition-of-Done "Final-test registry contains real date
    # ranges"). Real gap fixed here: train/calib/test boundaries previously
    # only existed as positional slice indices inside this script's own
    # memory -- nothing recorded the real date ranges anywhere, so that
    # checklist item wasn't actually satisfiable by inspecting any artifact.
    # Real CLAUDE.md Part 2 SS2-exact per-fold date-range provenance
    # (train_start/train_end/embargo_start/embargo_end/validation_start/
    # validation_end) -- additive to fold_metrics below (real per-fold
    # predictive scores), not a replacement for it. See
    # build_split_manifest()'s own docstring for the real gap this closes.
    claude_fold_ranges = build_split_manifest(
        sport="mlb",
        horizon="late",
        dataset_hash=artifact.get("artifact_hash", ""),
        folds=folds,
        final_test_start=str(test_final["event_start_utc"].min()),
        final_test_end=str(test_final["event_start_utc"].max()),
        final_test_consumed=True,
    )["folds"]

    split_manifest = {
        "sport": "mlb",
        "horizon": "late",
        "dataset_hash": artifact.get("artifact_hash", ""),
        "folds": claude_fold_ranges,
        "fold_metrics": fold_metrics,
        "train_start": str(train_final["event_start_utc"].min()),
        "train_end": str(train_final["event_start_utc"].max()),
        "train_n": train_final.height,
        "calibration_start": str(calib_final["event_start_utc"].min()),
        "calibration_end": str(calib_final["event_start_utc"].max()),
        "calibration_n": calib_final.height,
        "final_test_start": str(test_final["event_start_utc"].min()),
        "final_test_end": str(test_final["event_start_utc"].max()),
        "final_test_n": test_final.height,
        "final_test_consumed": True,
        "final_test_consumed_at_utc": datetime.now(UTC).isoformat(),
        "final_metrics": final_metrics,
    }
    split_manifest_path = Path("outputs/rebuild/mlb_split_manifest.json")
    split_manifest_path.write_text(json.dumps(split_manifest, indent=2, default=str))
    print(f"11. Split manifest (real date ranges, final test marked consumed) saved to {split_manifest_path}")

    # Real test_consumption_registry.json update -- the registry exists
    # precisely so a later session can tell mlb_moneyline's final test has
    # already been spent and must not be silently reused (CLAUDE.md: "Do
    # not inspect the final test while selecting features/model family/
    # hyperparameters/ensemble weights/calibrator/threshold"). Previously
    # left at its stale placeholder (test_start=null, consumed=false, "Not
    # yet trained") from before real-feature training existed.
    registry_path = Path("outputs/rebuild/test_consumption_registry.json")
    registry = json.loads(registry_path.read_text())
    registry["active_tests"]["mlb_moneyline"] = {
        "test_start": split_manifest["final_test_start"],
        "test_end": split_manifest["final_test_end"],
        "consumed": True,
        "consumed_at_utc": split_manifest["final_test_consumed_at_utc"],
        "note": (
            f"real held-out evaluation, n={test_final.height}, "
            f"accuracy={final_metrics['accuracy']:.3f}, brier={final_metrics['brier']:.4f} "
            f"-- small-sample, RESEARCH_ONLY per outputs/rebuild/model_cards/mlb-two-head-v1.md"
        ),
    }
    registry_path.write_text(json.dumps(registry, indent=2))
    print("12. test_consumption_registry.json updated: mlb_moneyline marked consumed")


if __name__ == "__main__":
    main()
