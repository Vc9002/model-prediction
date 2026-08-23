"""Standardized evaluator for binary MLB candidate models (immutable research tooling).

Operates directly from immutable dataset tables (``outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet``)
and hard-pinned manifest contracts, with strict SHA-256 validation aborting on contract mismatches.

Supports:
- ``--mode v9_research``: StandardScaler (train-fit only) -> LogisticRegression(max_iter=5000)
- ``--mode v8_reproduction``: Unscaled features -> Historical LogisticRegression
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import polars as pl
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from model_prediction.config import PROJECT_ROOT
from model_prediction.validation import (
    FEATURE_VARIANTS,
)

DEFAULT_BASELINE = "elo_trend_park_weather_starter_bullpen"
BOOTSTRAP_SEED = 20260815
N_BOOTSTRAP = 2000

V9_DATASET_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9"
V9_PARQUET_PATH = V9_DATASET_DIR / "tables" / "mlb_v9_feature_table_v1.parquet"
V9_MANIFEST_PATH = V9_DATASET_DIR / "manifests" / "mlb_v9_feature_table_v1.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_contract(manifest_path: Path, parquet_path: Path) -> dict:
    """Strictly verify dataset contract. Aborts with ABORT_DATASET_CONTRACT_MISMATCH on failure."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"ABORT_DATASET_CONTRACT_MISMATCH: manifest missing at {manifest_path}")
    if not parquet_path.exists():
        raise FileNotFoundError(f"ABORT_DATASET_CONTRACT_MISMATCH: parquet table missing at {parquet_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_hash = sha256_file(parquet_path)
    if manifest.get("dataset_sha256") != actual_hash:
        raise ValueError(
            f"ABORT_DATASET_CONTRACT_MISMATCH: Parquet sha256 mismatch! "
            f"expected={manifest.get('dataset_sha256')}, actual={actual_hash}"
        )
    return manifest


def _scores(probabilities: list[float], outcomes: list[int]) -> dict:
    probs = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(outcomes, dtype=int)
    log_loss = float(-np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs)))
    brier = float(np.mean((probs - y) ** 2))
    accuracy = float(np.mean((probs >= 0.5) == y))
    auc = float(roc_auc_score(y, probs)) if len(set(y)) > 1 else None
    return {
        "log_loss": round(log_loss, 6),
        "brier": round(brier, 6),
        "accuracy": round(accuracy, 4),
        "auc": round(auc, 4) if auc else None,
    }


def _date_cluster_bootstrap_paired(
    dates: list[str],
    p_base: list[float],
    p_cand: list[float],
    y: list[int],
    metric: str,
    *,
    seed: int = BOOTSTRAP_SEED,
    n_bootstrap: int = N_BOOTSTRAP,
) -> dict:
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, day in enumerate(dates):
        by_day[day].append(i)
    clusters = list(by_day.values())
    rng = np.random.default_rng(seed)
    base_arr = np.asarray(p_base)
    cand_arr = np.asarray(p_cand)
    y_arr = np.asarray(y)

    def metric_delta(indices: list[int]) -> float:
        base_m = _scores(base_arr[indices].tolist(), y_arr[indices].tolist())[metric]
        cand_m = _scores(cand_arr[indices].tolist(), y_arr[indices].tolist())[metric]
        return cand_m - base_m

    observed = metric_delta(list(range(len(p_base))))
    deltas = []
    for _ in range(n_bootstrap):
        sample: list[int] = []
        for _ in range(len(clusters)):
            sample.extend(clusters[int(rng.integers(0, len(clusters)))])
        deltas.append(metric_delta(sample))
    deltas_arr = np.asarray(deltas)
    p_better = float(np.mean(deltas_arr < 0))
    return {
        "observed_delta": round(observed, 6),
        "ci_95": [
            round(float(np.percentile(deltas_arr, 2.5)), 6),
            round(float(np.percentile(deltas_arr, 97.5)), 6),
        ],
        "P_challenger_better": round(p_better, 4),
        "n_bootstrap": n_bootstrap,
        "seed": seed,
    }


def v9_research_fit(df_train: pl.DataFrame, features: list[str]) -> Pipeline:
    """v9 Standardized Pipeline: StandardScaler fit ONLY on training -> LogisticRegression."""
    X = df_train.select(features).to_numpy()
    y = df_train["home_win"].to_numpy()
    pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(max_iter=5000, solver="lbfgs")),
        ]
    )
    pipe.fit(X, y)
    return pipe


def v8_reproduction_fit(df_train: pl.DataFrame, features: list[str]) -> LogisticRegression:
    """Historical v8 unscaled fitting."""
    X = df_train.select(features).to_numpy()
    y = df_train["home_win"].to_numpy()
    clf = LogisticRegression(fit_intercept=True, max_iter=1000, solver="lbfgs")
    clf.fit(X, y)
    return clf


def predict_model(model: Any, df_eval: pl.DataFrame, features: list[str]) -> list[float]:
    X = df_eval.select(features).to_numpy()
    probs = model.predict_proba(X)[:, 1]
    return [float(p) for p in probs]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variants",
        nargs="+",
        required=True,
        help="feature-variant names (validation.FEATURE_VARIANTS keys or list of column names)",
    )
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--mode", choices=["v9_research", "v8_reproduction"], default="v9_research")
    parser.add_argument("--bootstrap", type=int, default=N_BOOTSTRAP)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "outputs/research/mlb_evaluator/report.json"))
    args = parser.parse_args()

    # If v9 feature table is ready, use it with contract verification
    if V9_PARQUET_PATH.exists() and V9_MANIFEST_PATH.exists():
        manifest = verify_dataset_contract(V9_MANIFEST_PATH, V9_PARQUET_PATH)
        print(f"[evaluator] Contract verified against manifest (sha256={manifest['dataset_sha256'][:12]}...)")
        df = pl.read_parquet(V9_PARQUET_PATH)
        df_train = df.filter(pl.col("split") == "train")
        df_holdout = df.filter(pl.col("split") == "research_test")
    else:
        # Fallback to pinned_cohort until feature table build finishes
        from mlb_research_common import pinned_cohort

        cohort = pinned_cohort()
        # Convert cohort objects to DataFrame
        df_train = pl.DataFrame(
            [
                {
                    "home_win": r.outcome,
                    "date_et": r.date,
                    **{k: getattr(r, k, 0.0) for k in FEATURE_VARIANTS.get(args.baseline, [])},
                }
                for r in cohort["train"]
            ]
        )
        df_holdout = pl.DataFrame(
            [
                {
                    "home_win": r.outcome,
                    "date_et": r.date,
                    **{k: getattr(r, k, 0.0) for k in cohort["exact_holdout"]},
                }
                for r in cohort["exact_holdout"]
            ]
        )

    fitter = v9_research_fit if args.mode == "v9_research" else v8_reproduction_fit
    base_features = list(FEATURE_VARIANTS.get(args.baseline, [args.baseline]))

    base_model = fitter(df_train, base_features)
    base_probabilities = predict_model(base_model, df_holdout, base_features)
    holdout_outcomes = df_holdout["home_win"].to_list()
    holdout_dates = (
        df_holdout["date_et"].to_list()
        if "date_et" in df_holdout.columns
        else [str(i) for i in range(len(df_holdout))]
    )

    base_metrics = _scores(base_probabilities, holdout_outcomes)

    report: dict = {
        "schema": "mlb-standard-evaluator-v2",
        "mode": args.mode,
        "manifest": manifest,
        "holdout_rows": len(df_holdout),
        "baseline": {"name": args.baseline, "features": base_features, "metrics": base_metrics},
        "variants": {},
    }

    # Constant home rate baseline
    train_home_rate = float(df_train["home_win"].mean() or 0.5)
    const_probs = [train_home_rate] * len(df_holdout)
    const_metrics = _scores(const_probs, holdout_outcomes)
    report["constant_home_baseline"] = {
        "train_home_rate": round(train_home_rate, 4),
        "metrics": const_metrics,
    }

    print(
        f"\n==================== MLB Model Evaluator ({args.mode}) ====================\n"
        f"constant_home: LL={const_metrics['log_loss']:.4f} Brier={const_metrics['brier']:.4f} acc={const_metrics['accuracy']:.4f}\n"
        f"baseline ({args.baseline}): LL={base_metrics['log_loss']:.4f} Brier={base_metrics['brier']:.4f} "
        f"acc={base_metrics['accuracy']:.4f} AUC={base_metrics['auc']}\n"
        f"--------------------------------------------------------------------------------"
    )

    for variant in args.variants:
        var_features = list(FEATURE_VARIANTS.get(variant, [variant]))
        cand_model = fitter(df_train, var_features)
        cand_probs = predict_model(cand_model, df_holdout, var_features)
        cand_metrics = _scores(cand_probs, holdout_outcomes)

        paired_ll = _date_cluster_bootstrap_paired(
            holdout_dates,
            base_probabilities,
            cand_probs,
            holdout_outcomes,
            "log_loss",
            n_bootstrap=args.bootstrap,
        )
        paired_brier = _date_cluster_bootstrap_paired(
            holdout_dates,
            base_probabilities,
            cand_probs,
            holdout_outcomes,
            "brier",
            n_bootstrap=args.bootstrap,
        )

        verdict = "INCONCLUSIVE"
        if (
            paired_ll["observed_delta"] < 0
            and paired_brier["observed_delta"] <= 0
            and paired_ll["P_challenger_better"] >= 0.90
        ):
            verdict = "KEEP"
        elif paired_ll["observed_delta"] > 0 and paired_ll["P_challenger_better"] <= 0.10:
            verdict = "REJECT"

        report["variants"][variant] = {
            "features": var_features,
            "metrics": cand_metrics,
            "paired_delta_ll": paired_ll,
            "paired_delta_brier": paired_brier,
            "verdict": verdict,
        }

        print(
            f"{variant:<35} | LL={cand_metrics['log_loss']:.4f} (dLL={paired_ll['observed_delta']:+.5f}, P_better={paired_ll['P_challenger_better']:.2f}) | "
            f"Brier={cand_metrics['brier']:.4f} (dBr={paired_brier['observed_delta']:+.5f}) | AUC={cand_metrics['auc']} | Verdict={verdict}"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nReport written to: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
