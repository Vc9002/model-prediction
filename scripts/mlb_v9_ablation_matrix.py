"""MLB v9 ablation matrix harness (post-burn-in Phase 2, docs/V9_RESEARCH_PLAN.md §4).

Evaluates ONE feature-set variant at a time against the v8-reproduced
control, on the SAME frozen point-in-time feature table
(``outputs/research/mlb_v9_ablation/pit_mlb_v9.jsonl``, produced by
``model_prediction.feature_freezer``), using:

  - 5-fold expanding-window walk-forward CV (never a random split --
    each fold trains only on dates strictly before its evaluation window,
    per the project's point-in-time invariant)
  - date-cluster bootstrap P(candidate Brier < incumbent Brier) on the
    pooled out-of-fold predictions (2,000 resamples, clustered by date so
    within-day correlation isn't treated as independent evidence)
  - coverage = fraction of OOF rows where every candidate feature is
    "available" per its own *_available flag (features with no such flag
    are always available by construction)

Verdict follows the pre-registered §0.5 rule literally:

    KEEP         ΔBrier < -0.002 AND >=4/5 folds agree in sign AND
                 bootstrap P(better) >= 0.90 AND coverage >= 90%
    REJECT       ΔBrier >= 0 AND >=3/5 folds agree in sign (wrong direction)
    INCONCLUSIVE anything else

Usage:
    python scripts/mlb_v9_ablation_matrix.py --variant B --features starter_fip_gap \
        --swap starter_era_gap --seed 20260817

``--features`` are the ADDED/SWAPPED-IN feature names; ``--swap`` (optional,
repeatable) names control features REMOVED for this variant. The full
feature set is computed as (control - swap) + features, in control order
with new features appended -- this keeps every variant a single, auditable
diff against control A rather than a hand-typed tuple that could silently
drift from the registered control.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.calibration import calibration_metrics
from model_prediction.roadmap_challenger import (
    _cluster_bootstrap_brier_delta,
    _fit,
    _predict,
)
from model_prediction.validation import ValidationRow, chronological_split

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FROZEN_TABLE = PROJECT_ROOT / "outputs/research/mlb_v9_ablation/pit_mlb_v9.jsonl"

# v8's exact shipped feature order (verified 2026-08-17 gate, mlb-v8-reproduction-final).
CONTROL_FEATURES: tuple[str, ...] = (
    "elo_probability",
    "trend_gap",
    "park_factor",
    "weather_factor",
    "starter_era_gap",
    "bullpen_weakness_gap",
)

AVAILABILITY_FLAGS = {
    "park_factor": "park_available",
    "weather_factor": "weather_available",
    "park_factor_pit": "park_available",
    "probable_starter_era_gap": "probable_starter_available",
    "bullpen_weakness_gap": "bullpen_available",
    "bullpen_fatigue_gap": "bullpen_fatigue_available",
}


def load_frozen_rows(path: Path) -> list[ValidationRow]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(ValidationRow(**json.loads(line)))
    rows.sort(key=lambda r: (r.date, r.event_id))
    return rows


def make_feature_set(features: list[str], swap: list[str]) -> tuple[str, ...]:
    base = [f for f in CONTROL_FEATURES if f not in swap]
    for feature in features:
        if feature not in base:
            base.append(feature)
    return tuple(base)


def walk_forward_folds(
    rows: list[ValidationRow], n_folds: int = 5
) -> list[tuple[list[ValidationRow], list[ValidationRow]]]:
    """Expanding-window folds: fold i trains on dates strictly before its
    eval window and evaluates on the next 1/(n_folds+1) chunk of dates.
    Fold 0's train window is never empty (first two chunks seed it)."""
    dates = sorted({row.date for row in rows})
    n_chunks = n_folds + 1
    chunk_size = max(1, len(dates) // n_chunks)
    chunks = [dates[i * chunk_size : (i + 1) * chunk_size] for i in range(n_chunks - 1)]
    chunks.append(dates[(n_chunks - 1) * chunk_size :])
    folds = []
    for i in range(1, n_chunks):
        train_dates = {d for chunk in chunks[:i] for d in chunk}
        eval_dates = set(chunks[i])
        train = [r for r in rows if r.date in train_dates]
        eval_rows = [r for r in rows if r.date in eval_dates]
        if train and eval_rows:
            folds.append((train, eval_rows))
    return folds[-n_folds:] if len(folds) > n_folds else folds


def coverage_fraction(rows: list[ValidationRow], features: tuple[str, ...]) -> float:
    flags = {AVAILABILITY_FLAGS[f] for f in features if f in AVAILABILITY_FLAGS}
    if not flags:
        return 1.0
    covered = sum(1 for r in rows if all(getattr(r, flag) for flag in flags))
    return covered / len(rows) if rows else 0.0


def run_variant(
    *,
    variant: str,
    features: tuple[str, ...],
    seed: int,
    description: str,
    min_date: str | None = None,
) -> dict:
    all_rows = load_frozen_rows(FROZEN_TABLE)
    _, _, locked_holdout, _ = chronological_split(all_rows)
    holdout_dates = {r.date for r in locked_holdout}
    cv_rows = [r for r in all_rows if r.date not in holdout_dates]
    if min_date is not None:
        cv_rows = [r for r in cv_rows if r.date >= min_date]

    folds = walk_forward_folds(cv_rows, n_folds=5)
    fold_results = []
    pooled_incumbent: list[float] = []
    pooled_candidate: list[float] = []
    pooled_rows: list[ValidationRow] = []

    for fold_index, (train, eval_rows) in enumerate(folds):
        incumbent_model = _fit(train, CONTROL_FEATURES)
        candidate_model = _fit(train, features)
        incumbent_probs = _predict(incumbent_model, eval_rows, CONTROL_FEATURES)
        candidate_probs = _predict(candidate_model, eval_rows, features)
        outcomes = [r.outcome for r in eval_rows]
        incumbent_brier = calibration_metrics(incumbent_probs, outcomes, minimum_sample=1)
        candidate_brier = calibration_metrics(candidate_probs, outcomes, minimum_sample=1)
        delta = float(candidate_brier["brier_score"]) - float(incumbent_brier["brier_score"])
        fold_results.append(
            {
                "fold": fold_index,
                "eval_dates": sorted({r.date for r in eval_rows})[:1]
                + ["..."]
                + sorted({r.date for r in eval_rows})[-1:],
                "n": len(eval_rows),
                "incumbent_brier": incumbent_brier["brier_score"],
                "candidate_brier": candidate_brier["brier_score"],
                "delta_brier": round(delta, 6),
            }
        )
        pooled_incumbent.extend(incumbent_probs)
        pooled_candidate.extend(candidate_probs)
        pooled_rows.extend(eval_rows)

    folds_better = sum(1 for f in fold_results if f["delta_brier"] < 0)
    folds_worse = sum(1 for f in fold_results if f["delta_brier"] >= 0)
    mean_delta = round(mean(f["delta_brier"] for f in fold_results), 6)

    bootstrap = _cluster_bootstrap_brier_delta(pooled_incumbent, pooled_candidate, pooled_rows, seed=seed)
    # P(better) uses the same date-cluster resample procedure as the CI
    # above (candidate - incumbent < 0 means candidate won that resample).
    by_date: dict[str, list[float]] = defaultdict(list)
    for incumbent, candidate, row in zip(pooled_incumbent, pooled_candidate, pooled_rows, strict=True):
        by_date[row.date].append((candidate - row.outcome) ** 2 - (incumbent - row.outcome) ** 2)
    dates = sorted(by_date)
    rng = random.Random(seed)
    n_resamples = 2000
    better_count = 0
    for _ in range(n_resamples):
        sampled_days = [rng.choice(dates) for _ in dates]
        values = [v for day in sampled_days for v in by_date[day]]
        if mean(values) < 0:
            better_count += 1
    p_better = round(better_count / n_resamples, 4)

    coverage = coverage_fraction(pooled_rows, features)

    if mean_delta < -0.002 and folds_better >= 4 and p_better >= 0.90 and coverage >= 0.90:
        verdict = "KEEP"
    elif mean_delta >= 0 and folds_worse >= 3:
        verdict = "REJECT"
    else:
        verdict = "INCONCLUSIVE"

    report = {
        "variant": variant,
        "description": description,
        "features": list(features),
        "control_features": list(CONTROL_FEATURES),
        "n_folds": len(fold_results),
        "fold_results": fold_results,
        "folds_better": folds_better,
        "folds_worse": folds_worse,
        "mean_delta_brier": mean_delta,
        "bootstrap_ci": bootstrap,
        "bootstrap_p_better": p_better,
        "coverage": round(coverage, 4),
        "pooled_n": len(pooled_rows),
        "verdict": verdict,
    }
    out_dir = PROJECT_ROOT / "outputs/research/mlb_v9_ablation"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_from{min_date}" if min_date else ""
    out_path = out_dir / f"variant_{variant}{suffix}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, help="Letter A-N")
    parser.add_argument("--features", nargs="*", default=[], help="Features to add/swap in")
    parser.add_argument("--swap", nargs="*", default=[], help="Control features to remove")
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--description", default="")
    parser.add_argument("--min-date", default=None, help="Restrict CV rows to date >= this ISO date")
    args = parser.parse_args()

    features = make_feature_set(args.features, args.swap)
    report = run_variant(
        variant=args.variant,
        features=features,
        seed=args.seed,
        description=args.description,
        min_date=args.min_date,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "fold_results"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
