"""Comprehensive comparison of MLB NRFI v2 vs v3 on settled games and holdout data.

Compares:
- Incumbent / older model: mlb-nrfi-v2 (single logistic regression)
- Rework / newer model: mlb-nrfi-v3 (two-half-inning decomposed model)

Evaluated across:
1. All 478 settled games in the production ledger (actual live picks)
2. All 1,424 locked holdout test games from the reproduction harness
"""

from __future__ import annotations

import contextlib
import json
import math
import random
from typing import Any

import numpy as np
import pandas as pd

from model_prediction.calibration import calibration_metrics
from model_prediction.config import PROJECT_ROOT
from model_prediction.domain import parse_utc
from model_prediction.main_ledgers import MultiSportPickLedger
from model_prediction.models.mlb_first_inning import (
    DEFAULT_SNAPSHOT_PATH,
    FirstInningGameRow,
    MLBFirstInningModel,
    MLBHalfInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
)


def logloss(predictions: list[float], outcomes: list[int]) -> float:
    clipped = [min(1.0 - 1e-12, max(1e-12, p)) for p in predictions]
    return -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes, strict=True)
    ) / len(clipped)


def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(predictions, outcomes, strict=True)) / len(outcomes)


def paired_bootstrap_delta_ci(
    base_preds: list[float],
    cand_preds: list[float],
    outcomes: list[int],
    samples: int = 2000,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrap delta logloss (candidate minus base; negative = candidate is better)."""
    deltas = []
    for bp, cp, y in zip(base_preds, cand_preds, outcomes, strict=True):
        b_clip = min(1.0 - 1e-12, max(1e-12, bp))
        c_clip = min(1.0 - 1e-12, max(1e-12, cp))
        loss_b = -(y * math.log(b_clip) + (1 - y) * math.log(1 - b_clip))
        loss_c = -(y * math.log(c_clip) + (1 - y) * math.log(1 - c_clip))
        deltas.append(loss_c - loss_b)

    n = len(deltas)
    rng = random.Random(seed)
    boots = []
    for _ in range(samples):
        sample = [deltas[rng.randint(0, n - 1)] for _ in range(n)]
        boots.append(sum(sample) / n)
    boots.sort()

    return {
        "point_estimate": round(sum(deltas) / n, 6),
        "ci_95_low": round(boots[int(samples * 0.025)], 6),
        "ci_95_high": round(boots[int(samples * 0.975)], 6),
    }


def analyze_model_performance(
    name: str,
    predictions: list[float],
    outcomes: list[int],
    min_sample: int = 20,
) -> dict[str, Any]:
    ll = logloss(predictions, outcomes)
    bs = brier_score(predictions, outcomes)
    metrics = calibration_metrics(predictions, outcomes, minimum_sample=min_sample)

    p_arr = np.array(predictions)
    y_arr = np.array(outcomes)

    # Accuracy when predicting majority class (> 0.50)
    pred_class = (p_arr >= 0.50).astype(int)
    acc = float(np.mean(pred_class == y_arr))

    # Mean probability on actual NRFI=1 vs actual YRFI=0 (Discrimination separation)
    mean_p_when_nrfi = float(np.mean(p_arr[y_arr == 1])) if np.sum(y_arr == 1) > 0 else 0.0
    mean_p_when_yrfi = float(np.mean(p_arr[y_arr == 0])) if np.sum(y_arr == 0) > 0 else 0.0
    discrimination_gap = mean_p_when_nrfi - mean_p_when_yrfi

    # Check for probability clustering around 50%
    clustering_50 = float(np.mean((p_arr >= 0.48) & (p_arr <= 0.52)))

    return {
        "model": name,
        "sample_size": len(predictions),
        "log_loss": round(ll, 6),
        "brier_score": round(bs, 6),
        "expected_calibration_error": round(float(metrics.get("expected_calibration_error", 0.0)), 6),
        "calibration_slope": round(float(metrics.get("calibration_slope", 1.0)), 4),
        "calibration_intercept": round(float(metrics.get("calibration_intercept", 0.0)), 4),
        "binary_accuracy": round(acc, 4),
        "mean_p_when_nrfi": round(mean_p_when_nrfi, 4),
        "mean_p_when_yrfi": round(mean_p_when_yrfi, 4),
        "discrimination_gap": round(discrimination_gap, 4),
        "pct_clustered_near_50_pct": round(clustering_50 * 100, 2),
        "mean_predicted_prob": round(float(np.mean(p_arr)), 4),
        "actual_nrfi_rate": round(float(np.mean(y_arr)), 4),
    }


def evaluate_ledger_settled_games() -> dict[str, Any]:
    """Evaluate both models on all settled NRFI picks in the production ledger."""
    ml_main = MultiSportPickLedger(PROJECT_ROOT / "data", flat=False)
    main_rows = ml_main._ledgers["mlb"].rows()
    df = pd.DataFrame(main_rows)
    nrfi_df = df[(df["market_type"] == "nrfi") & (df["result"].isin(["win", "loss"]))].copy()

    # Load model definitions
    v2_payload = json.loads((PROJECT_ROOT / "config/models/mlb-nrfi-v2.json").read_text(encoding="utf-8"))
    v3_payload = json.loads((PROJECT_ROOT / "config/models/mlb-nrfi-v3.json").read_text(encoding="utf-8"))
    m_v2 = MLBFirstInningModel.from_dict(v2_payload)
    m_v3 = MLBHalfInningModel.from_dict(v3_payload)

    # Load snapshot lookup for fallback matching
    all_snaps = build_first_inning_ledger(DEFAULT_SNAPSHOT_PATH)
    snap_lookup = {}
    for r in all_snaps:
        d = r.game_start_utc[:10]
        snap_lookup[(d, r.home_team.lower(), r.away_team.lower())] = r

    v2_probs: list[float] = []
    v3_probs: list[float] = []
    actual_outcomes: list[int] = []  # 1 = NRFI (0 runs), 0 = YRFI (runs scored)
    market_probs: list[float] = []
    decimal_odds_list: list[float] = []

    for _, row in nrfi_df.iterrows():
        feat = None
        if pd.notna(row.get("model_input_snapshot_json")) and row.get("model_input_snapshot_json"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                s_dict = json.loads(row["model_input_snapshot_json"])
                if s_dict.get("features"):
                    feat = s_dict["features"]

        if feat is None:
            d = str(row.get("event_start_utc", ""))[:10]
            h = str(row.get("home_team", "")).lower()
            a = str(row.get("away_team", "")).lower()
            snap_r = snap_lookup.get((d, h, a))
            if snap_r is not None:
                feat = snap_r.features

        if feat is not None:
            fake_row = FirstInningGameRow(
                None,
                str(row.get("event_start_utc")),
                str(row.get("home_team")),
                str(row.get("away_team")),
                "",
                feat,
                0,
                0.0,
            )
            p_v2 = m_v2.predict_p_nrfi(fake_row)
            p_v3 = m_v3.predict_p_nrfi(fake_row)

            sel = str(row["selection"]).lower()
            res = str(row["result"]).lower()
            if sel == "nrfi":
                actual_nrfi = 1 if res == "win" else 0
            else:
                actual_nrfi = 0 if res == "win" else 1

            v2_probs.append(p_v2)
            v3_probs.append(p_v3)
            actual_outcomes.append(actual_nrfi)
            dec_odds = float(row.get("decimal_odds") or 1.909)
            decimal_odds_list.append(dec_odds)
            m_implied = float(
                row.get("market_implied_probability") or (1.0 / dec_odds if dec_odds > 0 else 0.5)
            )
            market_probs.append(m_implied)

    v2_analysis = analyze_model_performance(
        "mlb-nrfi-v2 (Incumbent Single Logistic)", v2_probs, actual_outcomes
    )
    v3_analysis = analyze_model_performance(
        "mlb-nrfi-v3 (New Decomposed Half-Inning)", v3_probs, actual_outcomes
    )
    delta_ci = paired_bootstrap_delta_ci(v2_probs, v3_probs, actual_outcomes)

    return {
        "dataset": "production_ledger_settled_games",
        "total_settled_games_evaluated": len(actual_outcomes),
        "v2_incumbent": v2_analysis,
        "v3_new_model": v3_analysis,
        "delta_logloss_v3_vs_v2": delta_ci,
    }


def evaluate_holdout_test_games() -> dict[str, Any]:
    """Evaluate both models on the locked 1,424 holdout test games from game snapshots."""
    probe_rows = build_first_inning_ledger(DEFAULT_SNAPSHOT_PATH)
    n = len(probe_rows)
    train_n = int(n * 0.60)
    val_n = int(n * 0.20)
    train_end = probe_rows[train_n - 1].game_start_utc
    priors = compute_first_inning_priors(DEFAULT_SNAPSHOT_PATH, end_utc=parse_utc(train_end))

    base_rows = build_first_inning_ledger(DEFAULT_SNAPSHOT_PATH, priors=priors)
    test = base_rows[train_n + val_n :]

    v2_payload = json.loads((PROJECT_ROOT / "config/models/mlb-nrfi-v2.json").read_text(encoding="utf-8"))
    v3_payload = json.loads((PROJECT_ROOT / "config/models/mlb-nrfi-v3.json").read_text(encoding="utf-8"))
    m_v2 = MLBFirstInningModel.from_dict(v2_payload)
    m_v3 = MLBHalfInningModel.from_dict(v3_payload)

    v2_probs = [m_v2.predict_p_nrfi(r) for r in test]
    v3_probs = [m_v3.predict_p_nrfi(r) for r in test]
    outcomes = [r.nrfi for r in test]

    v2_analysis = analyze_model_performance("mlb-nrfi-v2 (Incumbent Single Logistic)", v2_probs, outcomes)
    v3_analysis = analyze_model_performance("mlb-nrfi-v3 (New Decomposed Half-Inning)", v3_probs, outcomes)
    delta_ci = paired_bootstrap_delta_ci(v2_probs, v3_probs, outcomes)

    return {
        "dataset": "locked_holdout_test_games",
        "total_holdout_games_evaluated": len(outcomes),
        "v2_incumbent": v2_analysis,
        "v3_new_model": v3_analysis,
        "delta_logloss_v3_vs_v2": delta_ci,
    }


def main():
    print("Evaluating MLB NRFI v2 vs v3 on settled ledger games...")
    ledger_results = evaluate_ledger_settled_games()

    print("Evaluating MLB NRFI v2 vs v3 on locked holdout test games...")
    holdout_results = evaluate_holdout_test_games()

    full_report = {
        "evaluation_summary": {
            "purpose": "Compare incumbent mlb-nrfi-v2 against reworked mlb-nrfi-v3 across live settled games and holdout data.",
            "production_ledger_settled_games_count": ledger_results["total_settled_games_evaluated"],
            "holdout_test_games_count": holdout_results["total_holdout_games_evaluated"],
        },
        "live_settled_games": ledger_results,
        "holdout_test_games": holdout_results,
    }

    out_file = PROJECT_ROOT / "outputs/research/mlb_nrfi_v2_vs_v3_settled_comparison.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(full_report, indent=2) + "\n", encoding="utf-8")
    print(f"Report written to {out_file}")
    print(json.dumps(full_report, indent=2))


if __name__ == "__main__":
    main()
