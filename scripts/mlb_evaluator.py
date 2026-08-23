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


def verify_dataset_contract(manifest_path: Path, parquet_path: Path) -> tuple[dict, pl.DataFrame]:
    """Strictly verify dataset contract. Aborts with ABORT_DATASET_CONTRACT_MISMATCH on failure."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"ABORT_DATASET_CONTRACT_MISMATCH: manifest missing at {manifest_path}")
    if not parquet_path.exists():
        raise FileNotFoundError(f"ABORT_DATASET_CONTRACT_MISMATCH: parquet table missing at {parquet_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_dataset_hash = sha256_file(parquet_path)
    if manifest.get("dataset_sha256") != actual_dataset_hash:
        raise ValueError(
            f"ABORT_DATASET_CONTRACT_MISMATCH: Parquet dataset_sha256 mismatch! "
            f"expected={manifest.get('dataset_sha256')}, actual={actual_dataset_hash}"
        )

    df = pl.read_parquet(parquet_path)

    # 1. Verify schema hash
    schema_str = json.dumps(sorted([(c, str(t)) for c, t in df.schema.items()]), sort_keys=True)
    actual_schema_hash = hashlib.sha256(schema_str.encode()).hexdigest()
    if manifest.get("schema_sha256") and manifest.get("schema_sha256") != actual_schema_hash:
        raise ValueError(
            f"ABORT_DATASET_CONTRACT_MISMATCH: Parquet schema_sha256 mismatch! "
            f"expected={manifest.get('schema_sha256')}, actual={actual_schema_hash}"
        )

    # 2. Verify split cohort event ID hashes and alignment
    cohorts_dir = manifest_path.parent.parent / "cohorts"
    for split_name, key, filename in [
        ("train", "train_event_ids_sha256", "train_event_ids_v1.json"),
        ("validation", "validation_event_ids_sha256", "validation_event_ids_v1.json"),
        ("research_test", "research_test_event_ids_sha256", "research_test_event_ids_v1.json"),
    ]:
        cohort_file = cohorts_dir / filename
        if cohort_file.exists():
            file_hash = sha256_file(cohort_file)
            if manifest.get(key) and manifest.get(key) != file_hash:
                raise ValueError(
                    f"ABORT_DATASET_CONTRACT_MISMATCH: Cohort file {filename} sha mismatch! "
                    f"expected={manifest.get(key)}, actual={file_hash}"
                )
            cohort_ids = json.loads(cohort_file.read_text(encoding="utf-8"))
            split_ids = df.filter(pl.col("split") == split_name)["event_id"].to_list()
            if cohort_ids != split_ids:
                raise ValueError(
                    f"ABORT_DATASET_CONTRACT_MISMATCH: Split {split_name} does not match {filename}!"
                )

    return manifest, df


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

    deltas = []
    base_scores = []
    cand_scores = []

    for _ in range(n_bootstrap):
        sampled = rng.choice(len(clusters), size=len(clusters), replace=True)
        idx = [i for s in sampled for i in clusters[s]]
        if not idx:
            continue
        sub_base = [p_base[i] for i in idx]
        sub_cand = [p_cand[i] for i in idx]
        sub_y = [y[i] for i in idx]

        s_b = _scores(sub_base, sub_y)[metric]
        s_c = _scores(sub_cand, sub_y)[metric]
        if s_b is not None and s_c is not None:
            base_scores.append(s_b)
            cand_scores.append(s_c)
            deltas.append(s_c - s_b)

    if not deltas:
        return {
            "mean_delta": None,
            "ci_95": None,
            "p_challenger_better": None,
            "p_base_mean": None,
            "p_cand_mean": None,
        }

    obs_base = _scores(p_base, y)[metric]
    obs_cand = _scores(p_cand, y)[metric]
    observed_delta = round(obs_cand - obs_base, 6) if obs_base is not None and obs_cand is not None else 0.0

    deltas.sort()
    lower = float(np.percentile(deltas, 2.5))
    upper = float(np.percentile(deltas, 97.5))
    p_better = float(np.mean([d < 0 for d in deltas]))

    return {
        "observed_delta": observed_delta,
        "mean_delta": round(float(np.mean(deltas)), 6),
        "ci_95": [round(lower, 6), round(upper, 6)],
        "p_challenger_better": round(p_better, 4),
        "P_challenger_better": round(p_better, 4),
        "p_base_mean": round(float(np.mean(base_scores)), 6),
        "p_cand_mean": round(float(np.mean(cand_scores)), 6),
    }


def v9_research_fit(df_train: pl.DataFrame, features: list[str]) -> Pipeline:
    X = df_train.select(features).to_numpy()
    y = df_train["home_win"].to_numpy()
    pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=5000, solver="lbfgs", C=0.01)),
        ]
    )
    pipe.fit(X, y)
    return pipe


def v8_reproduction_fit(df_train: pl.DataFrame, features: list[str]) -> LogisticRegression:
    X = df_train.select(features).to_numpy()
    y = df_train["home_win"].to_numpy()
    clf = LogisticRegression(fit_intercept=True, max_iter=1000, solver="lbfgs")
    clf.fit(X, y)
    return clf


def predict_model(model: Any, df_eval: pl.DataFrame, features: list[str]) -> list[float]:
    X = df_eval.select(features).to_numpy()
    probs = model.predict_proba(X)[:, 1]
    return [float(p) for p in probs]


V9_FEATURE_SETS: dict[str, list[str]] = {
    "mlb_v8_baseline": [
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ],
    "mlb_v9_full": [
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "starter_kbb_gap",
        "bullpen_weakness_gap",
        "bullpen_fatigue_gap",
        "rest_disparity",
    ],
    "starter_rate_kbb": [
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_kbb_gap",
        "bullpen_weakness_gap",
    ],
}


def _resolve_features(variant: str) -> list[str]:
    if variant in V9_FEATURE_SETS:
        return list(V9_FEATURE_SETS[variant])
    if variant in FEATURE_VARIANTS:
        return list(FEATURE_VARIANTS[variant])
    return [variant]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variants",
        nargs="+",
        required=True,
        help="feature-variant names (V9_FEATURE_SETS, FEATURE_VARIANTS keys, or list of column names)",
    )
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--mode", choices=["v9_research", "v8_reproduction"], default="v9_research")
    parser.add_argument("--bootstrap", type=int, default=N_BOOTSTRAP)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "outputs/research/mlb_evaluator/report.json"))
    args = parser.parse_args()

    # Immutable dataset contract verification (NO silent fallback)
    manifest, df = verify_dataset_contract(V9_MANIFEST_PATH, V9_PARQUET_PATH)
    print(
        f"[evaluator] Dataset contract verified against manifest (sha256={manifest['dataset_sha256'][:12]}...)"
    )
    df_train = df.filter(pl.col("split") == "train")
    df_holdout = df.filter(pl.col("split") == "research_test")

    fitter = v9_research_fit if args.mode == "v9_research" else v8_reproduction_fit
    base_features = _resolve_features(args.baseline)

    base_model = fitter(df_train, base_features)
    base_probabilities = predict_model(base_model, df_holdout, base_features)
    holdout_outcomes = df_holdout["home_win"].to_list()
    holdout_dates = (
        df_holdout["date_et"].to_list()
        if "date_et" in df_holdout.columns
        else [str(i) for i in range(len(df_holdout))]
    )

    base_metrics = _scores(base_probabilities, holdout_outcomes)
    train_home_rate = float(np.mean(df_train["home_win"].to_numpy()))
    const_probs = [train_home_rate] * len(holdout_outcomes)
    const_metrics = _scores(const_probs, holdout_outcomes)

    report: dict[str, Any] = {
        "mode": args.mode,
        "dataset_sha256": manifest["dataset_sha256"],
        "schema_sha256": manifest.get("schema_sha256"),
        "train_rows": len(df_train),
        "holdout_rows": len(df_holdout),
        "baseline": {"name": args.baseline, "features": base_features, "metrics": base_metrics},
        "variants": {},
    }
    report["constant_home_prior"] = {
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
        var_features = _resolve_features(variant)
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
