"""Real chronological cross-fit calibration comparison -- CLAUDE.md's
next-phase Task 14.

Real gap this closes: identity/Platt/isotonic/temperature calibrators
already exist (calibration.py) and are individually tested, but nothing
ever evaluated them correctly against real MLB predictions. The critical
rule this must not violate: fitting a calibrator on all real OOF
predictions and then reporting its performance on those same predictions
is calibration-set overfitting. Every real evaluation here uses
cross_fit_calibration_eval() (calibration.py) -- chronological
expanding-window cross-fitting, where a calibration method only ever
sees labels strictly earlier than the block it is scored on.

Real OOF moneyline predictions are generated once per model family
(two_head, xgb_direct via Task 9's nested CV, xgb_two_head) using the
identical real date-cluster-safe folds (Task 8) every other real
training script uses, then every calibration method is cross-fit
evaluated against that same real chronological sequence. Registry-safe:
does not touch test_consumption_registry.json.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_calibration_comparison.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import cross_fit_calibration_eval, fit_calibrator
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.mlb_model_comparison import build_mlb_moneyline_oof
from model_prediction.rebuild.validation import brier_score, calibration_curve, expanding_folds, log_loss

HORIZON = "late"
CALIBRATION_METHODS = ["identity", "platt", "temperature", "isotonic"]
N_CALIBRATION_BLOCKS = 4


def _cohort_calibration(probs: list[float], labels: list[int], cohorts: list[dict], key: str) -> dict:
    """Diagnostic-only cohort calibration (Task 14: 'do not select
    separate calibrators for tiny cohorts yet')."""
    by_value: dict[str, dict[str, list]] = {}
    for p, y, c in zip(probs, labels, cohorts, strict=True):
        v = c[key]
        by_value.setdefault(v, {"probs": [], "labels": []})
        by_value[v]["probs"].append(p)
        by_value[v]["labels"].append(y)
    report = {}
    for v, data in by_value.items():
        n = len(data["labels"])
        report[v] = {
            "n": n,
            "log_loss": log_loss(data["labels"], data["probs"]) if n >= 5 else None,
            "brier": brier_score(data["labels"], data["probs"]) if n >= 5 else None,
        }
    return report


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
    fold_manifest_hash = hashlib.sha256(
        json.dumps(
            [{"train_end": f.train_end, "val_start": f.val_start, "val_end": f.val_end} for f in folds],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    print(
        f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates); "
        f"oof_split_manifest_hash={fold_manifest_hash[:12]}"
    )

    oof = build_mlb_moneyline_oof(features, folds)
    for name, data in oof.items():
        print(f"3. {name}: {len(data['labels'])} real chronological OOF predictions")

    comparison: dict[str, dict] = {}
    md_lines: list[str] = ["# MLB Calibration Comparison\n"]
    md_lines.append(
        f"dataset_hash: `{dataset.dataset_hash}`  \noof_split_manifest_hash: `{fold_manifest_hash}`\n"
    )

    for model_name, data in oof.items():
        probs, labels, cohorts = data["probs"], data["labels"], data["cohorts"]
        n = len(labels)
        print(
            f"\n4. {model_name} ({n} real OOF predictions), chronological cross-fit "
            f"({N_CALIBRATION_BLOCKS} blocks, first block fit-only):"
        )
        md_lines.append(f"\n## {model_name} (n={n})\n")
        md_lines.append("| method | n_eval | log_loss | brier | ece | cal_intercept | cal_slope |")
        md_lines.append("|---|---|---|---|---|---|---|")

        method_results = {}
        for method in CALIBRATION_METHODS:
            if n < 2 * N_CALIBRATION_BLOCKS:
                print(
                    f"   {method:12s}: too few real OOF rows for {N_CALIBRATION_BLOCKS} cross-fit blocks, skipped"
                )
                continue
            result = cross_fit_calibration_eval(probs, labels, method, n_blocks=N_CALIBRATION_BLOCKS)
            method_results[method] = result
            ll = f"{result.log_loss:.4f}" if result.log_loss is not None else "n/a"
            br = f"{result.brier:.4f}" if result.brier is not None else "n/a"
            ec = f"{result.ece:.4f}" if result.ece is not None else "n/a"
            ci = f"{result.calibration_intercept:.3f}" if result.calibration_intercept is not None else "n/a"
            cs = f"{result.calibration_slope:.3f}" if result.calibration_slope is not None else "n/a"
            print(
                f"   {method:12s}: n_eval={result.n_eval_total} log_loss={ll} brier={br} ece={ec} "
                f"intercept={ci} slope={cs}"
            )
            md_lines.append(f"| {method} | {result.n_eval_total} | {ll} | {br} | {ec} | {ci} | {cs} |")

        # Task 14's explicit rule: identity is a valid winner. Select
        # purely by real cross-fit log loss among methods that produced a
        # real result -- never force a non-identity method.
        valid_methods = {m: r for m, r in method_results.items() if r.log_loss is not None}
        best_method = (
            min(valid_methods, key=lambda m: valid_methods[m].log_loss) if valid_methods else "identity"
        )
        print(f"   -> best by real cross-fit log loss: {best_method}")
        md_lines.append(
            f"\n**Selected: `{best_method}`** (lowest real cross-fit log loss; "
            f"identity is always a valid winner, never forced out.)\n"
        )

        # Reliability buckets on the RAW (uncalibrated) predictions --
        # the real, honest starting point regardless of which method wins.
        curve = calibration_curve(labels, probs, n_bins=10)
        md_lines.append("\n### Reliability buckets (raw, uncalibrated)\n")
        md_lines.append("| bucket | mean_predicted | observed_frequency | n |")
        md_lines.append("|---|---|---|---|")
        for i in range(len(curve["bin_centers"])):
            if curve["counts"][i] == 0:
                continue
            md_lines.append(
                f"| {curve['bin_centers'][i]:.2f} | {curve['bin_centers'][i]:.3f} | "
                f"{curve['actual_fraction'][i]:.3f} | {curve['counts'][i]} |"
            )

        # Cohort calibration -- diagnostic only.
        md_lines.append("\n### Cohort calibration (diagnostic only, no calibrator selection)\n")
        for key in ("starters", "weather"):
            cohort_report = _cohort_calibration(probs, labels, cohorts, key)
            md_lines.append(f"\n**By {key}:**\n")
            md_lines.append("| cohort | n | log_loss | brier |")
            md_lines.append("|---|---|---|---|")
            for v, r in cohort_report.items():
                ll = f"{r['log_loss']:.4f}" if r["log_loss"] is not None else "n/a (n<5)"
                br = f"{r['brier']:.4f}" if r["brier"] is not None else "n/a (n<5)"
                md_lines.append(f"| {v} | {r['n']} | {ll} | {br} |")

        # Real, persisted "production" calibrator: the winning method,
        # refit on the FULL real chronological OOF history now that
        # cross-fitting has already validated the method choice
        # out-of-sample. Stored separately from the base model, per
        # CLAUDE.md's "persist calibrator separately, bound by hash."
        final_calibrator = fit_calibrator(best_method, probs, labels)
        base_model_hash = hashlib.sha256(
            json.dumps(
                {"model": model_name, "dataset_hash": dataset.dataset_hash},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        calibrator_artifact = {
            "model_name": model_name,
            "method": best_method,
            "parameters": final_calibrator.parameters,
            "base_model_hash": base_model_hash,
            "dataset_hash": dataset.dataset_hash,
            "oof_split_manifest_hash": fold_manifest_hash,
            "training_range": {"start": start_date, "end": end_date},
            "n_training_oof": n,
        }
        calibrator_artifact["calibrator_hash"] = hashlib.sha256(
            json.dumps(calibrator_artifact, sort_keys=True, default=str).encode()
        ).hexdigest()
        artifact_dir = Path("config/models/challengers")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"mlb-{model_name}-calibrator-v1.json"
        artifact_path.write_text(json.dumps(calibrator_artifact, indent=2, default=str))
        print(f"   Calibrator artifact saved to {artifact_path}")

        comparison[model_name] = {
            "n_oof": n,
            "cross_fit_results": {
                m: {
                    "n_eval_total": r.n_eval_total,
                    "log_loss": r.log_loss,
                    "brier": r.brier,
                    "ece": r.ece,
                    "calibration_intercept": r.calibration_intercept,
                    "calibration_slope": r.calibration_slope,
                    "per_block": r.per_block,
                }
                for m, r in method_results.items()
            },
            "best_method": best_method,
            "reliability_curve_raw": curve,
            "cohort_calibration": {
                "starters": _cohort_calibration(probs, labels, cohorts, "starters"),
                "weather": _cohort_calibration(probs, labels, cohorts, "weather"),
            },
            "calibrator_artifact_path": str(artifact_path),
        }

    print(
        "\n5. Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). No promotion decision is made here.\n"
        "   Cohort calibration is diagnostic only -- no separate calibrators are\n"
        "   selected per cohort at this sample size."
    )

    results_path = Path("outputs/rebuild/mlb_calibration_comparison.json")
    results_path.write_text(
        json.dumps(
            {
                "dataset_hash": dataset.dataset_hash,
                "oof_split_manifest_hash": fold_manifest_hash,
                "matched_games": dataset.matched_games,
                "calibration_methods": CALIBRATION_METHODS,
                "n_calibration_blocks": N_CALIBRATION_BLOCKS,
                "models": comparison,
            },
            indent=2,
            default=str,
        )
    )
    print(f"6. Results saved to {results_path}")

    report_path = Path("outputs/rebuild/calibration_report.md")
    report_path.write_text("\n".join(md_lines) + "\n")
    print(f"7. Report saved to {report_path}")


if __name__ == "__main__":
    main()
