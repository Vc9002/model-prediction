"""Fold-wise defensive_trend_gap stability audit for WNBA rebuild v1.

Runs expanding chronological folds (not a single 60/20/20 split) and
compares 3-feature (elo_probability + trend_gap + defensive_trend_gap)
against 2-feature (elo_probability + trend_gap) on every fold.

Reports per-fold: LogLoss, Brier, ECE, defensive_trend_gap coefficient
and sign, N. Decides KEEP or DROP based on repeatable proper-score
improvement and coefficient sign stability across folds — not on a
single validation-block Brier difference of ~0.00019.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/audit_wnba_defensive_trend.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from model_prediction.rebuild.validation import (
    brier_score,
    directional_accuracy,
    ece,
    expanding_folds,
    log_loss,
)
from model_prediction.rebuild.wnba.elo_trend import build_dataset, rows_to_frame

FULL_FEATURES = ["elo_probability", "trend_gap", "defensive_trend_gap"]
REDUCED_FEATURES = ["elo_probability", "trend_gap"]
N_FOLDS = 5
VAL_DAYS = 60  # ~2 weeks of WNBA
GAP_DAYS = 1
TEST_DAYS = 0  # we just want fold-wise validation, no locked test here


def _metrics(labels: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = int(labels.shape[0])
    preds = (probs > 0.5).astype(int)
    return {
        "n": n,
        "log_loss": float(log_loss(labels, probs)),
        "brier": float(brier_score(labels, probs)),
        "ece": float(ece(labels, probs, n_bins=10)),
        "accuracy": float(directional_accuracy(
            labels.astype(int).tolist(), preds.tolist()
        )),
    }


def main() -> None:
    print("Loading WNBA walk-forward data...")
    result = build_dataset("data/rebuild", [2022, 2023, 2024, 2025],
                           minimum_history_games=30, minimum_team_games=3)
    frame = rows_to_frame(result.rows).sort("event_start_utc")
    print(f"  {frame.height} rows, {result.skipped_bootstrap} bootstrap skipped, "
          f"{result.skipped_cold_start_team} cold-start skipped")

    dates = frame["sports_event_date"].to_list()
    folds = expanding_folds(dates, n_splits=N_FOLDS, val_size=VAL_DAYS,
                            gap=GAP_DAYS, test_size=TEST_DAYS)
    print(f"\n{len(folds)} expanding chronological folds:\n")

    fold_reports: list[dict[str, Any]] = []
    full_wins = 0
    reduced_wins = 0
    sign_history: list[int] = []

    for fold in folds:
        train_mask = pl.col("sports_event_date") <= fold.train_end
        val_mask = (pl.col("sports_event_date") >= fold.val_start) & \
                   (pl.col("sports_event_date") <= fold.val_end)

        train_df = frame.filter(train_mask)
        val_df = frame.filter(val_mask)

        if val_df.height == 0:
            continue

        val_labels = val_df["home_win"].to_numpy().astype(int)

        # Fit 3-feature
        X3_train = train_df.select(FULL_FEATURES).to_numpy()
        lr3 = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
        lr3.fit(X3_train, train_df["home_win"].to_numpy().astype(int))
        probs3 = lr3.predict_proba(val_df.select(FULL_FEATURES).to_numpy())[:, 1]
        m3 = _metrics(val_labels, probs3)
        coef3 = dict(zip(FULL_FEATURES, lr3.coef_[0].tolist()))

        # Fit 2-feature
        X2_train = train_df.select(REDUCED_FEATURES).to_numpy()
        lr2 = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
        lr2.fit(X2_train, train_df["home_win"].to_numpy().astype(int))
        probs2 = lr2.predict_proba(val_df.select(REDUCED_FEATURES).to_numpy())[:, 1]
        m2 = _metrics(val_labels, probs2)
        dtg_coef = coef3.get("defensive_trend_gap", 0.0)
        dtg_sign = 1 if dtg_coef > 0 else (-1 if dtg_coef < 0 else 0)
        sign_history.append(dtg_sign)

        brier_delta = m3["brier"] - m2["brier"]  # negative = 3-feat better
        logloss_delta = m3["log_loss"] - m2["log_loss"]
        ece_delta = m3["ece"] - m2["ece"]

        full_better = brier_delta < 0  # 3-feature Brier is lower

        if full_better:
            full_wins += 1
        else:
            reduced_wins += 1

        report = {
            "fold": fold.fold_index,
            "train_end": fold.train_end,
            "val_start": fold.val_start,
            "val_end": fold.val_end,
            "n_train": train_df.height,
            "n_val": val_df.height,
            "defensive_trend_gap_coef": round(dtg_coef, 6),
            "dtg_sign": dtg_sign,
            "full_3_brier": round(m3["brier"], 6),
            "full_3_logloss": round(m3["log_loss"], 6),
            "full_3_ece": round(m3["ece"], 6),
            "full_3_acc": round(m3["accuracy"], 4),
            "reduced_2_brier": round(m2["brier"], 6),
            "reduced_2_logloss": round(m2["log_loss"], 6),
            "reduced_2_ece": round(m2["ece"], 6),
            "reduced_2_acc": round(m2["accuracy"], 4),
            "brier_delta": round(brier_delta, 6),
            "logloss_delta": round(logloss_delta, 6),
            "ece_delta": round(ece_delta, 6),
            "full_better": full_better,
        }
        fold_reports.append(report)

        marker = " <<< 3-feat better" if full_better else ""
        print(f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} "
              f"[{fold.val_start}..{fold.val_end}]")
        print(f"    3-feat: Brier={m3['brier']:.5f} LogLoss={m3['log_loss']:.5f} "
              f"ECE={m3['ece']:.5f} dtg_coef={dtg_coef:+.6f}")
        print(f"    2-feat: Brier={m2['brier']:.5f} LogLoss={m2['log_loss']:.5f} "
              f"ECE={m2['ece']:.5f}")
        print(f"    ΔBrier={brier_delta:+.6f} ΔLogLoss={logloss_delta:+.6f} "
              f"ΔECE={ece_delta:+.6f}{marker}\n")

    # ── Decision ──
    print("=" * 60)
    print("DECISION ANALYSIS")
    print(f"  Folds where 3-feature won: {full_wins}/{len(fold_reports)}")
    print(f"  Folds where 2-feature won: {reduced_wins}/{len(fold_reports)}")
    print(f"  dtg_coef signs: {sign_history}")
    print(f"  Mean Brier delta: {np.mean([r['brier_delta'] for r in fold_reports]):+.6f}")
    print(f"  Mean LogLoss delta: {np.mean([r['logloss_delta'] for r in fold_reports]):+.6f}")
    print(f"  Mean ECE delta: {np.mean([r['ece_delta'] for r in fold_reports]):+.6f}")

    signs_consistent = len(set(sign_history)) <= 1
    improvement_repeatable = full_wins >= len(fold_reports) * 0.6  # wins on ≥60% of folds
    improvement_meaningful = abs(np.mean([r['brier_delta'] for r in fold_reports])) >= 0.001

    if signs_consistent and improvement_repeatable and improvement_meaningful:
        verdict = "KEEP"
        reason = (
            f"defensive_trend_gap shows repeatable proper-score improvement "
            f"({full_wins}/{len(fold_reports)} folds) with stable sign "
            f"({sign_history}) and meaningful mean Brier delta "
            f"({np.mean([r['brier_delta'] for r in fold_reports]):+.6f})."
        )
    else:
        verdict = "DROP"
        reasons = []
        if not signs_consistent:
            reasons.append(f"unstable coefficient sign across folds: {sign_history}")
        if not improvement_repeatable:
            reasons.append(f"improvement not repeatable: {full_wins}/{len(fold_reports)} folds")
        if not improvement_meaningful:
            reasons.append(
                f"mean Brier delta too small to be meaningful "
                f"({np.mean([r['brier_delta'] for r in fold_reports]):+.6f})"
            )
        reason = "defensive_trend_gap does not show repeatable, meaningful improvement: " + "; ".join(reasons) + "."

    print(f"\n  VERDICT: {verdict}")
    print(f"  REASON: {reason}")

    # Persist report
    out_path = Path("outputs/rebuild/wnba/defensive_trend_audit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "verdict": verdict,
        "reason": reason,
        "n_folds": len(fold_reports),
        "full_wins": full_wins,
        "reduced_wins": reduced_wins,
        "sign_history": sign_history,
        "mean_brier_delta": round(np.mean([r['brier_delta'] for r in fold_reports]), 6),
        "mean_logloss_delta": round(np.mean([r['logloss_delta'] for r in fold_reports]), 6),
        "mean_ece_delta": round(np.mean([r['ece_delta'] for r in fold_reports]), 6),
        "per_fold": fold_reports,
    }, indent=2, default=str))
    print(f"\n  Report saved to {out_path}")


if __name__ == "__main__":
    main()
