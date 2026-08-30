"""MLB Phase F1 Scientific Diagnostics & Identification Suite.

Executes the essential identification tests on the paired M0 vs M2 sample:
1. M0 vs Bias-Corrected M0b vs Out-of-Fold M4-1:
   - Level correction vs. matchup discrimination decomposition.
   - Paired date-clustered bootstrap 95% CI for incremental MAE gain (MAE_M0b - MAE_M4-1).
   - Empirical P(MAE_M0b > MAE_M4-1).
2. Within-Date Fixed Effects Regression: R_{i,d} - mean(R_d) = beta_{within} * (Delta_{i,d} - mean(Delta_d)) + eps.
3. Within-Date Delta Permutation Placebo Test (preserving date-level totals and marginals, using (k+1)/(B+1) correction).
4. Strict Sample Size Gate Enforcement: n >= 100 required for QUALIFIED status (otherwise INSUFFICIENT_EVIDENCE).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import parse_utc
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.runtime_paths import RuntimePaths
from scripts.backfill_mlb_market_quotes import build_mlb_slug
from scripts.mlb_m2_discrepancy_analysis import DiscrepancyRow


def _date_clustered_bootstrap_paired_mae_gain(
    by_date_eval: dict[str, list[tuple[float, float, float]]],  # date -> [(actual, pred_m0b, pred_m4_1)]
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute date-clustered bootstrap 95% CI for incremental MAE gain G = MAE_M0b - MAE_M4-1."""
    dates = list(by_date_eval.keys())
    if len(dates) < 3:
        return {
            "mean_gain": 0.0,
            "bootstrap_95ci": [0.0, 0.0],
            "p_gain_positive": 0.5,
        }

    rng = random.Random(seed)
    gains: list[float] = []

    for _ in range(resamples):
        sampled_dates = [rng.choice(dates) for _ in range(len(dates))]
        batch_actuals = []
        batch_m0b = []
        batch_m4_1 = []

        for d in sampled_dates:
            for act, p_m0b, p_m4_1 in by_date_eval[d]:
                batch_actuals.append(act)
                batch_m0b.append(p_m0b)
                batch_m4_1.append(p_m4_1)

        act_arr = np.array(batch_actuals, dtype=np.float64)
        mae_m0b = float(np.mean(np.abs(act_arr - np.array(batch_m0b, dtype=np.float64))))
        mae_m4_1 = float(np.mean(np.abs(act_arr - np.array(batch_m4_1, dtype=np.float64))))
        gains.append(mae_m0b - mae_m4_1)

    gains.sort()
    low_idx = int(0.025 * len(gains))
    high_idx = int(0.975 * len(gains))
    mean_g = float(np.mean(gains))
    p_positive = float(np.mean([1.0 if g > 0 else 0.0 for g in gains]))

    return {
        "mean_incremental_mae_gain": round(mean_g, 4),
        "gain_date_clustered_bootstrap_95ci": [round(gains[low_idx], 4), round(gains[high_idx], 4)],
        "p_incremental_gain_positive": round(p_positive, 4),
        "is_gain_statistically_favorable": bool(gains[low_idx] > 0 or p_positive >= 0.90),
    }


def run_scientific_diagnostics(
    permutation_resamples: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    # 1. Load games from mlb_statsapi/game_snapshots.jsonl
    mlb_games_file = data_dir / "mlb_statsapi/game_snapshots.jsonl"
    all_games = []
    if mlb_games_file.exists():
        with open(mlb_games_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        all_games.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    discrepancy_rows: list[DiscrepancyRow] = []

    for g in all_games:
        away = g.get("away") or {}
        home = g.get("home") or {}
        away_runs = away.get("runs")
        home_runs = home.get("runs")
        start_utc = g.get("game_start_utc")
        if away_runs is None or home_runs is None or not start_utc:
            continue

        actual_total = float(away_runs + home_runs)
        away_name = away.get("team_name", "")
        home_name = home.get("team_name", "")
        date_str = start_utc[:10]
        slug = build_mlb_slug(away_name, home_name, date_str)

        start_dt = parse_utc(start_utc)
        decision_dt = start_dt - np.timedelta64(30, "m").astype("timedelta64[s]").item()

        vec_total = vector_builder.build_state_vector(
            event_id=slug,
            market_type="total",
            as_of_utc=decision_dt,
            primary_selection="Over",
        )

        if vec_total.consensus_line is not None and vec_total.consensus_price_no_vig is not None:
            m_line = vec_total.consensus_line
            m_prob = vec_total.consensus_price_no_vig

            park_adj = float((g.get("venue_id") or 0) % 5 - 2) * 0.15
            temp_f = (g.get("weather") or {}).get("temperature_f") or 70.0
            temp_adj = (float(temp_f) - 70.0) * 0.02
            structural_total = round(8.60 + park_adj + temp_adj, 2)

            discrepancy = round(structural_total - m_line, 2)
            realized_res = round(actual_total - m_line, 2)

            model_prob = 1.0 / (1.0 + np.exp(-0.40 * discrepancy))
            bet_side_won = 1 if actual_total > m_line else 0
            bet_price = (
                vec_total.consensus_price_no_vig
                if discrepancy > 0
                else (1.0 - vec_total.consensus_price_no_vig)
            )
            eff_model_prob = model_prob if discrepancy > 0 else (1.0 - model_prob)
            eff_market_prob = m_prob if discrepancy > 0 else (1.0 - m_prob)

            r_row = DiscrepancyRow(
                event_id=slug,
                decision_utc=start_utc,
                market_type="total",
                market_line=m_line,
                structural_pred=structural_total,
                discrepancy=discrepancy,
                actual_outcome=actual_total,
                realized_residual=realized_res,
                market_prob=eff_market_prob,
                model_prob=eff_model_prob,
                bet_price=bet_price,
                bet_side_won=bet_side_won,
                is_favorite=(m_prob > 0.50),
                sharp_soft_gap=vec_total.sharp_soft_gap,
            )
            discrepancy_rows.append(r_row)

    n_total = len(discrepancy_rows)
    if n_total < 10:
        return {"status": "INSUFFICIENT_DATA", "n": n_total}

    by_date: dict[str, list[DiscrepancyRow]] = defaultdict(list)
    for r in discrepancy_rows:
        by_date[r.decision_utc[:10]].append(r)
    unique_dates = sorted(by_date.keys())

    # =========================================================================
    # DIAGNOSTIC 1: M0 vs Bias-Corrected M0b vs Out-of-Fold (OOF) M4-1
    # =========================================================================
    k_folds = min(5, len(unique_dates))
    date_folds = np.array_split(unique_dates, k_folds)

    oof_preds_m0 = []
    oof_preds_m0b = []
    oof_preds_m4_1 = []
    oof_actuals = []
    by_date_eval: dict[str, list[tuple[float, float, float]]] = defaultdict(list)

    for fold_idx in range(k_folds):
        val_dates = set(date_folds[fold_idx])
        train_dates = set(unique_dates) - val_dates

        train_rows = [r for d in train_dates for r in by_date[d]]
        val_rows = [r for d in val_dates for r in by_date[d]]

        if not train_rows or not val_rows:
            continue

        train_residuals = [r.realized_residual for r in train_rows]
        c_hat = float(np.mean(train_residuals))

        train_deltas = np.array([r.discrepancy for r in train_rows], dtype=np.float64)
        train_res_arr = np.array([r.realized_residual for r in train_rows], dtype=np.float64)
        lr_train = stats.linregress(train_deltas, train_res_arr)
        alpha_hat = float(lr_train.intercept)
        beta_hat = float(lr_train.slope)

        for r in val_rows:
            pred_m0 = r.market_line
            pred_m0b = r.market_line + c_hat
            pred_m4_1 = r.market_line + alpha_hat + beta_hat * r.discrepancy

            oof_actuals.append(r.actual_outcome)
            oof_preds_m0.append(pred_m0)
            oof_preds_m0b.append(pred_m0b)
            oof_preds_m4_1.append(pred_m4_1)

            by_date_eval[r.decision_utc[:10]].append((r.actual_outcome, pred_m0b, pred_m4_1))

    y_act = np.array(oof_actuals)
    mae_m0 = float(np.mean(np.abs(y_act - np.array(oof_preds_m0))))
    mae_m0b = float(np.mean(np.abs(y_act - np.array(oof_preds_m0b))))
    mae_m4_1 = float(np.mean(np.abs(y_act - np.array(oof_preds_m4_1))))

    rmse_m0 = float(np.sqrt(np.mean(np.square(y_act - np.array(oof_preds_m0)))))
    rmse_m0b = float(np.sqrt(np.mean(np.square(y_act - np.array(oof_preds_m0b)))))
    rmse_m4_1 = float(np.sqrt(np.mean(np.square(y_act - np.array(oof_preds_m4_1)))))

    bias_m0 = float(np.mean(y_act - np.array(oof_preds_m0)))
    bias_m0b = float(np.mean(y_act - np.array(oof_preds_m0b)))
    bias_m4_1 = float(np.mean(y_act - np.array(oof_preds_m4_1)))

    # Paired Date-Clustered Bootstrap CI on Incremental MAE Gain (M0b vs M4-1)
    gain_bootstrap_res = _date_clustered_bootstrap_paired_mae_gain(by_date_eval)

    diagnostic_1 = {
        "description": "M0 (Raw) vs M0b (Bias-Corrected) vs M4-1 (OOF Structural Delta)",
        "OOF_folds": k_folds,
        "M0_raw_market": {
            "MAE": round(mae_m0, 4),
            "RMSE": round(rmse_m0, 4),
            "Bias": round(bias_m0, 4),
        },
        "M0b_bias_corrected_market": {
            "MAE": round(mae_m0b, 4),
            "RMSE": round(rmse_m0b, 4),
            "Bias": round(bias_m0b, 4),
        },
        "M4_1_structural_delta_oof": {
            "MAE": round(mae_m4_1, 4),
            "RMSE": round(rmse_m4_1, 4),
            "Bias": round(bias_m4_1, 4),
        },
        "incremental_gain_m4_1_over_m0b": round(mae_m0b - mae_m4_1, 4),
        "paired_gain_date_clustered_bootstrap": gain_bootstrap_res,
        "fraction_of_edge_from_level_correction": round((mae_m0 - mae_m0b) / (mae_m0 - mae_m4_1 + 1e-9), 4)
        if (mae_m0 - mae_m4_1) > 0
        else 0.0,
    }

    # =========================================================================
    # DIAGNOSTIC 2: Within-Date Fixed Effects Regression
    # =========================================================================
    demeaned_deltas = []
    demeaned_residuals = []
    for d_rows in by_date.values():
        if len(d_rows) >= 2:
            mean_d_delta = statistics.mean(r.discrepancy for r in d_rows)
            mean_d_res = statistics.mean(r.realized_residual for r in d_rows)
            for r in d_rows:
                demeaned_deltas.append(r.discrepancy - mean_d_delta)
                demeaned_residuals.append(r.realized_residual - mean_d_res)

    if len(demeaned_deltas) >= 10:
        dm_deltas_arr = np.array(demeaned_deltas, dtype=np.float64)
        dm_res_arr = np.array(demeaned_residuals, dtype=np.float64)
        lr_within = stats.linregress(dm_deltas_arr, dm_res_arr)
        beta_within = float(lr_within.slope)
        se_within = float(lr_within.stderr) if lr_within.stderr is not None else 0.0
        p_within = float(lr_within.pvalue)
        r2_within = float(lr_within.rvalue**2)
    else:
        beta_within, se_within, p_within, r2_within = 0.0, 0.0, 1.0, 0.0

    all_deltas = np.array([r.discrepancy for r in discrepancy_rows], dtype=np.float64)
    all_residuals = np.array([r.realized_residual for r in discrepancy_rows], dtype=np.float64)
    lr_raw = stats.linregress(all_deltas, all_residuals)
    beta_raw = float(lr_raw.slope)
    p_raw = float(lr_raw.pvalue)

    diagnostic_2 = {
        "description": "Within-Date Fixed Effects vs Raw Calibration Slope",
        "beta_raw_overall": round(beta_raw, 4),
        "p_value_raw": p_raw,
        "beta_within_date": round(beta_within, 4),
        "beta_within_std_err": round(se_within, 4),
        "beta_within_95ci": [
            round(beta_within - 1.96 * se_within, 4),
            round(beta_within + 1.96 * se_within, 4),
        ],
        "p_value_within": p_within,
        "r_squared_within": round(r2_within, 4),
        "has_game_level_discrimination": bool(beta_within > 0 and p_within < 0.10),
    }

    # =========================================================================
    # DIAGNOSTIC 3: Within-Date Delta Permutation Placebo Null Test
    # =========================================================================
    rng = random.Random(seed)
    permuted_betas: list[float] = []
    permuted_mae_gains: list[float] = []
    actual_mae_gain = mae_m0 - mae_m4_1

    for _ in range(permutation_resamples):
        perm_deltas: list[float] = []
        perm_residuals: list[float] = []

        for d_rows in by_date.values():
            d_res = [r.realized_residual for r in d_rows]
            d_del = [r.discrepancy for r in d_rows]
            rng.shuffle(d_del)
            perm_deltas.extend(d_del)
            perm_residuals.extend(d_res)

        p_d_arr = np.array(perm_deltas, dtype=np.float64)
        p_r_arr = np.array(perm_residuals, dtype=np.float64)
        if np.var(p_d_arr) > 1e-6:
            lr_p = stats.linregress(p_d_arr, p_r_arr)
            permuted_betas.append(float(lr_p.slope))

            perm_pred = (
                np.array([r.market_line for r in discrepancy_rows]) + lr_p.intercept + lr_p.slope * p_d_arr
            )
            perm_mae = float(np.mean(np.abs(y_act - perm_pred)))
            permuted_mae_gains.append(mae_m0 - perm_mae)

    k_extreme_beta = sum(1 for b in permuted_betas if b >= beta_raw)
    p_val_beta_corrected = (k_extreme_beta + 1) / (len(permuted_betas) + 1) if permuted_betas else 1.0

    k_extreme_mae = sum(1 for g in permuted_mae_gains if g >= actual_mae_gain)
    p_val_mae_corrected = (k_extreme_mae + 1) / (len(permuted_mae_gains) + 1) if permuted_mae_gains else 1.0

    diagnostic_3 = {
        "description": "Within-Date Permutation Placebo Null (Preserving Date-Level Marginals)",
        "methodological_note": "Permutation deliberately preserves date-level market/run-environment marginals while destroying game-to-game matchup delta assignment.",
        "permutation_resamples": len(permuted_betas),
        "actual_beta": round(beta_raw, 4),
        "mean_permuted_null_beta": round(float(np.mean(permuted_betas)), 4) if permuted_betas else 0.0,
        "k_permuted_extreme_beta": k_extreme_beta,
        "empirical_p_value_beta_corrected": round(p_val_beta_corrected, 4),
        "actual_mae_gain": round(actual_mae_gain, 4),
        "mean_permuted_null_mae_gain": round(float(np.mean(permuted_mae_gains)), 4)
        if permuted_mae_gains
        else 0.0,
        "k_permuted_extreme_mae": k_extreme_mae,
        "empirical_p_value_mae_gain_corrected": round(p_val_mae_corrected, 4),
        "rejects_null_placebo": bool(p_val_beta_corrected < 0.05),
    }

    # =========================================================================
    # DIAGNOSTIC 4: Strict Sample Size Gate Enforcement on Buckets
    # =========================================================================
    bucket_bounds = [
        ("-inf_to_-3.0", float("-inf"), -3.0),
        ("-3.0_to_-2.0", -3.0, -2.0),
        ("-2.0_to_-1.0", -2.0, -1.0),
        ("-1.0_to_0.0", -1.0, 0.0),
        ("0.0_to_+1.0", 0.0, 1.0),
        ("+1.0_to_+2.0", 1.0, 2.0),
        ("+2.0_to_+3.0", 2.0, 3.0),
        ("+3.0_to_+inf", 3.0, float("inf")),
    ]
    bucket_groups: dict[str, list[DiscrepancyRow]] = defaultdict(list)
    for r in discrepancy_rows:
        for name, low, high in bucket_bounds:
            if low <= r.discrepancy < high:
                bucket_groups[name].append(r)
                break

    audited_buckets = {}
    for name, _low, _high in bucket_bounds:
        b_rows = bucket_groups.get(name, [])
        n = len(b_rows)
        if n == 0:
            audited_buckets[name] = {"sample_size": 0, "status": "NO_DATA"}
            continue

        mean_delta = statistics.mean(r.discrepancy for r in b_rows)
        mean_residual = statistics.mean(r.realized_residual for r in b_rows)
        win_rate = statistics.mean(r.bet_side_won for r in b_rows)
        status = "QUALIFIED" if n >= 100 else "INSUFFICIENT_EVIDENCE"

        audited_buckets[name] = {
            "sample_size": n,
            "mean_discrepancy": round(mean_delta, 3),
            "mean_realized_residual": round(mean_residual, 3),
            "win_rate": round(win_rate, 4),
            "status": status,
        }

    # =========================================================================
    # PREREGISTERED REPLICATION MILESTONE SPECIFICATION
    # =========================================================================
    replication_milestone = {
        "target_sample": {
            "minimum_unique_games": 1000,
            "minimum_unique_dates": 100,
            "minimum_seasons": 2,
        },
        "gates": [
            "1. beta_within > 0",
            "2. date_clustered_bootstrap_95ci(beta_within) strictly excludes 0",
            "3. within_date_permutation_placebo rejects matchup null (p < 0.05)",
            "4. OOF M4-1 MAE beats M0b bias-corrected market MAE",
            "5. paired_date_bootstrap P(MAE_M0b > MAE_M4-1) >= 0.90",
            "6. effect sign survives temporal season partitions",
            "7. M4-1 improves at least one probabilistic calibration metric vs M0 (Brier / ECE / NLL)",
        ],
    }

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "headline_metrics": {
            "total_games": len({r.event_id for r in discrepancy_rows}),
            "total_dates": len(unique_dates),
            "total_decisions": len(discrepancy_rows),
        },
        "diagnostic_1_m0_vs_m0b_vs_m4_1": diagnostic_1,
        "diagnostic_2_within_date_fixed_effects": diagnostic_2,
        "diagnostic_3_permutation_placebo": diagnostic_3,
        "diagnostic_4_audited_bucket_gates": audited_buckets,
        "preregistered_replication_milestone": replication_milestone,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Scientific Diagnostics for M0 vs M2")
    parser.add_argument("--out", type=str, default="outputs/latest/mlb_m2_scientific_diagnostics.json")
    args = parser.parse_args()

    report = run_scientific_diagnostics()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Scientific diagnostics complete. Saved to {out_path}")
    print(json.dumps(report["diagnostic_1_m0_vs_m0b_vs_m4_1"], indent=2))
    print(json.dumps(report["diagnostic_3_permutation_placebo"], indent=2))
    print(json.dumps(report["preregistered_replication_milestone"], indent=2))
