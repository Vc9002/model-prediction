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
from model_prediction.rebuild.mlb_features import (
    build_game_feature_row,
    dedupe_scoreboard,
    identify_starters,
    load_probable_starter_records,
    load_raw_statcast_dates,
    normalize_statcast_pitches,
)

HORIZON = "late"
from model_prediction.rebuild.models import MLBTwoHeadModel
from model_prediction.rebuild.validation import (
    brier_score,
    build_split_manifest,
    ece,
    expanding_folds,
    log_loss,
)

INTENSITY_FEATURES = [
    "home_sp_avg_velocity", "away_sp_avg_velocity",
    "home_sp_csw_pct", "away_sp_csw_pct",
    "home_bp_bullpen_pitches", "away_bp_bullpen_pitches",
    "park_factor", "temp_f_mean",
]
DIFFERENTIAL_FEATURES = [
    "home_sp_k_pct", "away_sp_k_pct",
    "home_sp_bb_pct", "away_sp_bb_pct",
    "home_sp_days_rest", "away_sp_days_rest",
    "home_bp_bullpen_avg_velocity", "away_bp_bullpen_avg_velocity",
]


def main() -> None:
    sb_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    print(f"1. Scoreboard: {sb.height} total rows (deduped), {completed.height} completed games")

    backfill_dates = sorted({row["event_start_utc"][:10] for row in completed.iter_rows(named=True)})
    raw = load_raw_statcast_dates("data/rebuild", backfill_dates)
    pitches = normalize_statcast_pitches(raw)
    starters = identify_starters(pitches)
    probable_records = load_probable_starter_records()
    print(f"2. Statcast: {pitches.height} real pitches, {starters.height} real starter-game entries; "
          f"{len(probable_records)} real archived probable-starter observations")

    rows = []
    unmatched = 0
    for g in completed.iter_rows(named=True):
        row = build_game_feature_row(g, pitches, starters, "data/rebuild", HORIZON, probable_records)
        if row is None:
            unmatched += 1
            continue
        rows.append(row)
    features = pl.DataFrame(rows).sort("game_date")
    starters_known = int(features["starters_known"].sum()) if features.height else 0
    print(f"3. Feature rows: {features.height} matched ({unmatched} team-unresolved, not fabricated); "
          f"{starters_known}/{features.height} have a point-in-time-valid probable starter for both "
          f"teams at horizon={HORIZON} ({features.height - starters_known} flagged starters_known=0, "
          f"not silently filled with the actual starter)")

    if features.height < 30:
        print("Not enough matched games to train meaningfully (need >=30). Stopping honestly, not faking a result.")
        sys.exit(0)

    # expanding_folds() dedupes on its `dates` argument (sorted(set(dates))),
    # so passing the calendar-day game_date collapses 173 games into only
    # ~10 unique values here — too coarse for val_size/test_size sized in
    # days against this small a real dataset (this genuinely produced 0
    # folds on the first run). Passing the full event_start_utc timestamp
    # instead keeps chronological order but gives near-per-game granularity,
    # matching what pipeline_mlb_e2e.py already did for the same reason.
    features = features.sort("event_start_utc")
    dates = features["event_start_utc"].to_list()
    n = features.height
    folds = expanding_folds(dates, n_splits=3, val_size=max(10, n // 6), test_size=max(15, n // 5))
    print(f"4. Chronological folds: {len(folds)}")

    fold_metrics = []
    for fold in folds:
        train_df = features.filter(pl.col("event_start_utc") <= fold.train_end)
        val_df = features.filter(
            (pl.col("event_start_utc") >= fold.val_start) & (pl.col("event_start_utc") <= fold.val_end)
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

        fold_metrics.append({
            "fold": fold.fold_index, "train_n": train_df.height, "val_n": val_df.height,
            "log_loss": log_loss(y_true, y_prob),
            "brier": brier_score(y_true, y_prob),
            "ece": ece(y_true, y_prob),
        })
        print(f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} "
              f"ll={fold_metrics[-1]['log_loss']:.3f} brier={fold_metrics[-1]['brier']:.3f}")

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
    test_size = max(10, n // 6)
    calib_size = max(10, n // 6)
    train_final = features[: n - test_size - calib_size]
    calib_final = features[n - test_size - calib_size: n - test_size]
    test_final = features[n - test_size:]
    print(f"5b. Split: train={train_final.height}, calibration={calib_final.height} "
          f"(fits Platt only), test={test_final.height} (untouched until final evaluation)")

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
    print(f"6. Held-out test ({len(labels)} games): "
          f"brier={final_metrics['brier']:.4f} (raw {final_metrics['raw_brier']:.4f}) "
          f"ll={final_metrics['log_loss']:.4f} ece={final_metrics['ece']:.4f} "
          f"acc={final_metrics['accuracy']:.3f}")

    # ── Diagnostic: cold-start missingness composition, train vs test ────────
    # A short real backfill window (10 days) means many early rows have zero
    # prior starts for a pitcher -> availability=0 -> zeroed features fed to
    # the model as if they were real signal. That composition can differ
    # sharply between train and test purely from the backfill window's
    # shape, not from anything the model "learned" wrong. Reporting this
    # explicitly rather than only the headline metric above, per this
    # project's own "missingness is data" principle (CLAUDE.md Part 1 S11).
    train_avail = (train_final["home_sp_availability"].mean() + train_final["away_sp_availability"].mean()) / 2
    test_avail = (test_final["home_sp_availability"].mean() + test_final["away_sp_availability"].mean()) / 2
    print(f"7. Cold-start composition: train mean starter-availability={train_avail:.3f}, "
          f"test mean starter-availability={test_avail:.3f} "
          f"({'MISMATCHED — interpret the headline metric with caution' if abs(train_avail - test_avail) > 0.2 else 'comparable'})")

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
        print(f"8. Quality-filtered test (both starters have real history, "
              f"n={quality_metrics['n']}): brier={quality_metrics['brier']:.4f} "
              f"ll={quality_metrics['log_loss']:.4f} acc={quality_metrics['accuracy']:.3f}")
    else:
        print(f"8. Quality-filtered test: only {both_avail_test.height} rows have both starters "
              f"with real history — too few to report separately")

    artifact = final_model.to_artifact()
    artifact.update({
        "feature_set": "real_statcast_v1",
        "intensity_features": INTENSITY_FEATURES,
        "differential_features": DIFFERENTIAL_FEATURES,
        "calibration": {"intercept": cal.intercept, "slope": cal.slope},
        "fold_metrics": fold_metrics,
        "final_metrics": final_metrics,
        "quality_filtered_metrics": quality_metrics,
        "cold_start_composition": {"train_mean_availability": train_avail, "test_mean_availability": test_avail},
        "train_games": train_final.height,
        "calibration_games": calib_final.height,
        "test_games": test_final.height,
        "total_completed_games": completed.height,
        "matched_games": features.height,
        "unmatched_games": unmatched,
    })

    artifact_dir = Path("config/models/challengers")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "mlb-two-head-real-features-v1.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"\n9. Artifact saved to {artifact_path}")

    results_path = Path("outputs/rebuild/mlb_training_results_real_features.json")
    results_path.write_text(json.dumps({
        "model_version": artifact["model_id"], "feature_set": "real_statcast_v1",
        "matched_games": features.height, "unmatched_games": unmatched,
        "fold_metrics": fold_metrics, "final_metrics": final_metrics,
        "quality_filtered_metrics": quality_metrics,
        "cold_start_composition": {"train_mean_availability": train_avail, "test_mean_availability": test_avail},
        "artifact_hash": artifact.get("artifact_hash", ""),
    }, indent=2))
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
        sport="mlb", horizon="late", dataset_hash=artifact.get("artifact_hash", ""),
        folds=folds, final_test_start=str(test_final["event_start_utc"].min()),
        final_test_end=str(test_final["event_start_utc"].max()), final_test_consumed=True,
    )["folds"]

    split_manifest = {
        "sport": "mlb", "horizon": "late", "dataset_hash": artifact.get("artifact_hash", ""),
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
