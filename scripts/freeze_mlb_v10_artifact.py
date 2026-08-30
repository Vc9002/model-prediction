"""MLB Structural v10 Model Artifact Freezer & Confirmation Contract Builder.

Fits the final MLB Structural v10 model on the full 2024-2026 pre-freeze development dataset (N=5,427),
extracts frozen Ridge regression coefficients, computes genuine 5-fold chronological OOF unexplained errors,
computes cryptographic spec and schema hashes, and generates the immutable frozen artifact for prospective confirmation testing (F1C).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import parse_utc, utc_now
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.features.mlb_v10_features import MLBv10FeatureExtractor, MLBv10FeatureVector
from model_prediction.models.mlb_structural_v10 import MLBStructuralV10Model
from model_prediction.runtime_paths import RuntimePaths
from scripts.phase_f_runner import _fit_ols, build_mlb_slug_edt

FEATURE_SCHEMA_SPEC = """
SCHEMA: MLBv10FeatureVector_v1
FIELDS:
  home_sp_expected_ip: float [3.0, 7.5]
  home_sp_k_pct: float [0.05, 0.45]
  home_sp_bb_pct: float [0.01, 0.20]
  home_sp_k_minus_bb: float [-0.10, 0.35]
  home_sp_tto_penalty: float [1.0, 1.25]
  home_sp_rest_days: float [1.0, 10.0]
  away_sp_expected_ip: float [3.0, 7.5]
  away_sp_k_pct: float [0.05, 0.45]
  away_sp_bb_pct: float [0.01, 0.20]
  away_sp_k_minus_bb: float [-0.10, 0.35]
  away_sp_tto_penalty: float [1.0, 1.25]
  away_sp_rest_days: float [1.0, 10.0]
  away_lineup_xwoba_vs_sp: float [0.220, 0.420]
  away_lineup_k_pct: float [0.10, 0.35]
  away_lineup_bb_pct: float [0.03, 0.18]
  away_lineup_iso: float [0.05, 0.30]
  away_lineup_barrel_pct: float [0.01, 0.18]
  away_lineup_hard_hit_pct: float [0.20, 0.55]
  home_lineup_xwoba_vs_sp: float [0.220, 0.420]
  home_lineup_k_pct: float [0.10, 0.35]
  home_lineup_bb_pct: float [0.03, 0.18]
  home_lineup_iso: float [0.05, 0.30]
  home_lineup_barrel_pct: float [0.01, 0.18]
  home_lineup_hard_hit_pct: float [0.20, 0.55]
  away_matchup_k_interaction: float [home_sp_k * away_lineup_k]
  home_matchup_k_interaction: float [away_sp_k * home_lineup_k]
  away_matchup_bb_interaction: float [home_sp_bb * away_lineup_bb]
  home_matchup_bb_interaction: float [away_sp_bb * home_lineup_bb]
  away_platoon_edge: float [away_lineup_xwoba - 0.315]
  home_platoon_edge: float [home_lineup_xwoba - 0.315]
  home_bp_expected_ip: float [max(1.5, 9.0 - home_sp_exp_ip)]
  home_bp_effective_fip: float [2.5, 6.0]
  home_bp_freshness: float [0.0, 1.0]
  home_bp_hl_available: float [0.0, 2.0]
  home_bp_pitches_3d: int [0, 250]
  away_bp_expected_ip: float [max(1.5, 8.5 - away_sp_exp_ip)]
  away_bp_effective_fip: float [2.5, 6.0]
  away_bp_freshness: float [0.0, 1.0]
  away_bp_hl_available: float [0.0, 2.0]
  away_bp_pitches_3d: int [0, 250]
  park_factor: float [0.80, 1.35]
  is_dome: float {0.0, 1.0}
  temp_f: float [35.0, 105.0]
  air_density_ratio: float [0.85, 1.15]
  fly_ball_distance_factor: float [0.94, 1.08]
  wind_out_x_barrel: float [0.0, 0.10]
  temp_x_iso: float [-0.10, 0.10]
"""

MODEL_SPEC = """
MODEL_FAMILY: MLBStructuralV10
SCORING_DECOMPOSITION:
  E[Runs_away] = E[Runs_vsStarter] * (E[IP_H,SP] / 9.0) + E[Runs_vsBullpen] * ((9.0 - E[IP_H,SP]) / 9.0)
  E[Runs_home] = E[Runs_vsStarter] * (E[IP_A,SP] / 8.5) + E[Runs_vsBullpen] * ((8.5 - E[IP_A,SP]) / 8.5)
  E[Total] = E[Runs_away] + E[Runs_home]
  E[Margin] = E[Runs_home] - E[Runs_away]
REGULARIZATION: Ridge Regression with CV penalty selection
BOUNDS: Run expectancy clipped to [1.5, 11.0] per team
COEFFICIENT_POLICY: Fixed immutable weights fit on 2024-2026 development panel
"""

CONFIRMATION_PROTOCOL = """
PROTOCOL: F1C_V10_PROSPECTIVE_CONFIRMATION
BENCHMARK: M0b (Bias-Corrected Decision Market Consensus) vs M4-1(v10) (Structural Delta Calibrated)
PRIMARY_METRIC: G = MAE(M0b) - MAE(M4-1_v10) > 0 with date-clustered bootstrap P(G > 0) >= 0.90
GATES:
  Gate A (Structural Discrimination): beta_within_v10 > 0 with date-clustered 95% CI > 0
  Gate B (Continuous Incremental Edge): MAE(M4-1_v10) < MAE(M0b) and P(G > 0) >= 0.90
  Gate C (No Catastrophic Bias): |Bias(M4-1_v10)| < 0.25 runs per game
  Gate D (Probability Improvement): Brier(v10) < Brier(M0) or NLL(v10) < NLL(M0)
  Gate E (Calibration): Calibration slope of M4-1(v10) in [0.85, 1.15]
  Gate F (Temporal Stability): Consistent positive direction across temporal blocks
SAMPLE_MILESTONES:
  Milestone 1 (Interim Analysis): N_games >= 300, N_dates >= 30 (Descriptive report only, NO model/protocol changes, continue collection)
  Milestone 2 (Binding Qualification): N_games >= 500, N_dates >= 50 (Binding evaluation of Gates A-F)
STAGES:
  C1: PROSPECTIVE_REPLICATION_2026 (Remaining 2026 regular-season games after freeze)
  C2: PROSPECTIVE_CONTINUATION_2027 (If C1 sample N < 500 at season end, mark INSUFFICIENT_EVIDENCE and continue identical frozen v10 into 2027)
IMMUTABILITY: Pregame predictions hashed and persisted before first pitch; zero post-hoc regeneration
"""

PROBABILITY_MAPPING_SPEC = """
PROBABILITY_MODEL: OOFUnexplainedErrorShift_v1
EMPIRICAL_ERROR_SOURCE: 2024-2026 5-Fold Chronological Out-Of-Fold Unexplained Errors e_i = (Y_i - M_i) - mu_OOF_i (N=5,427)
CALCULATION_RULES:
  Given prospective conditional mean residual mu* = alpha + beta * Delta*
  Prospective continuous outcome: R* = mu* + e_i
  For integer market lines (X.0):
    P(Push) = P(-0.5 <= R* < 0.5) = mean(-0.5 <= mu* + e_i < 0.5)
    P(Over) = P(R* >= 0.5) = mean(mu* + e_i >= 0.5)
    P(Under) = P(R* < -0.5) = mean(mu* + e_i < -0.5)
  For half-point market lines (X.5):
    P(Push) = 0.0
    P(Over) = P(R* > 0.0) = mean(mu* + e_i > 0.0)
    P(Under) = P(R* < 0.0) = mean(mu* + e_i < 0.0)
BOUNDS: Clipped to [0.001, 0.999] with sum(P) normalized to 1.0
"""


def compute_hashes() -> tuple[str, str, str, str]:
    schema_h = hashlib.sha256(FEATURE_SCHEMA_SPEC.strip().encode("utf-8")).hexdigest()[:16]
    spec_h = hashlib.sha256(MODEL_SPEC.strip().encode("utf-8")).hexdigest()[:16]
    proto_h = hashlib.sha256(CONFIRMATION_PROTOCOL.strip().encode("utf-8")).hexdigest()[:16]
    prob_h = hashlib.sha256(PROBABILITY_MAPPING_SPEC.strip().encode("utf-8")).hexdigest()[:16]
    return schema_h, spec_h, proto_h, prob_h


def freeze_v10_artifact() -> Path:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
    vector_builder = MarketStateVectorBuilder(warehouse=warehouse, stale_cutoff_hours=24.0)

    # 1. Load All Historical & Snapshot Games
    all_game_sources: list[dict[str, Any]] = []
    for gpath in [
        data_dir / "historical/mlb_games_all.jsonl",
        data_dir / "mlb_statsapi/game_snapshots.jsonl",
    ]:
        if gpath.exists():
            with open(gpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            all_game_sources.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    deduped_games: dict[str, dict[str, Any]] = {}
    snapshot_map: dict[str, dict[str, Any]] = {}

    for g in all_game_sources:
        away = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        if not (away and home and start_utc):
            continue
        slug = build_mlb_slug_edt(away, home, start_utc)
        if slug not in deduped_games:
            deduped_games[slug] = g
        if "players" in g or "probable_pitcher_name" in (g.get("home") or {}):
            snapshot_map[slug] = g

    # 2. Extract Features on the full development set
    extractor = MLBv10FeatureExtractor(snapshot_path=data_dir / "mlb_statsapi/game_snapshots.jsonl")

    dev_features: list[MLBv10FeatureVector] = []
    dev_away_runs: list[float] = []
    dev_home_runs: list[float] = []
    dev_residuals: list[float] = []
    dev_deltas: list[float] = []
    dev_m_lines: list[float] = []

    sorted_slugs = sorted(
        deduped_games.keys(),
        key=lambda s: deduped_games[s].get("event_start_utc") or deduped_games[s].get("game_start_utc") or "",
    )

    for slug in sorted_slugs:
        g = deduped_games[slug]
        away_runs = (
            g.get("away_score") if g.get("away_score") is not None else (g.get("away") or {}).get("runs")
        )
        home_runs = (
            g.get("home_score") if g.get("home_score") is not None else (g.get("home") or {}).get("runs")
        )
        start_utc = g.get("event_start_utc") or g.get("game_start_utc") or ""
        away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
        home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""

        if away_runs is None or home_runs is None or not start_utc or not away_team or not home_team:
            continue

        start_dt = parse_utc(start_utc)
        dec_dt = start_dt - timedelta(minutes=30)

        vec = vector_builder.build_state_vector(
            event_id=slug,
            market_type="total",
            as_of_utc=dec_dt,
            primary_selection="Over",
        )

        if vec.consensus_line is not None:
            snap = snapshot_map.get(slug)
            feat = extractor.extract_features_for_matchup(
                event_id=slug,
                home_team=home_team,
                away_team=away_team,
                game_start_utc=start_utc,
                as_of_dt=dec_dt,
                snapshot=snap,
            )

            m_line = float(vec.consensus_line)
            actual_total = float(away_runs + home_runs)

            dev_features.append(feat)
            dev_away_runs.append(float(away_runs))
            dev_home_runs.append(float(home_runs))
            dev_m_lines.append(m_line)
            dev_residuals.append(actual_total - m_line)

    # 3. Fit Final Production Model on Full Development Set
    model = MLBStructuralV10Model()
    model.fit(dev_features, dev_away_runs, dev_home_runs)

    # 4. Compute M4-1 linear calibration parameters over development set
    dev_preds = [model.predict(f).projected_total_runs for f in dev_features]
    for i, p in enumerate(dev_preds):
        dev_deltas.append(p - dev_m_lines[i])

    alpha_cal, beta_cal, _, _, _ = _fit_ols(np.array(dev_deltas), np.array(dev_residuals))
    mean_bias_m0b = float(np.mean(dev_residuals))

    # 5. Compute genuine 5-fold chronological OOF unexplained errors e_i = (Actual - Market) - mu_OOF
    dates = sorted({f.game_start_utc[:10] for f in dev_features})
    n_dates = len(dates)
    n_folds = 5
    fold_size = max(1, n_dates // n_folds)
    date_to_fold = {d: min(idx // fold_size, n_folds - 1) for idx, d in enumerate(dates)}

    oof_unexplained_errors = np.zeros(len(dev_features), dtype=float)

    for test_fold in range(n_folds):
        train_idx = [
            i for i, f in enumerate(dev_features) if date_to_fold[f.game_start_utc[:10]] != test_fold
        ]
        test_idx = [i for i, f in enumerate(dev_features) if date_to_fold[f.game_start_utc[:10]] == test_fold]
        if not train_idx or not test_idx:
            continue

        train_feats = [dev_features[i] for i in train_idx]
        train_away = [dev_away_runs[i] for i in train_idx]
        train_home = [dev_home_runs[i] for i in train_idx]
        fold_model = MLBStructuralV10Model()
        fold_model.fit(train_feats, train_away, train_home)

        train_deltas_f = [
            fold_model.predict(dev_features[i]).projected_total_runs - dev_m_lines[i] for i in train_idx
        ]
        train_res_f = [dev_residuals[i] for i in train_idx]
        a_oof, b_oof, _, _, _ = _fit_ols(np.array(train_deltas_f), np.array(train_res_f))

        for i in test_idx:
            pred_tot = fold_model.predict(dev_features[i]).projected_total_runs
            delta_i = pred_tot - dev_m_lines[i]
            mu_oof_i = a_oof + (b_oof * delta_i)
            actual_res_i = dev_residuals[i]
            oof_unexplained_errors[i] = actual_res_i - mu_oof_i

    schema_hash, spec_hash, proto_hash, prob_hash = compute_hashes()

    artifact_dict: dict[str, Any] = {
        "schema_version": "1.0.0",
        "model_name": "MLB Structural v10",
        "model_version": "mlb-structural-v10-frozen",
        "created_at_utc": utc_now().isoformat(),
        "training_sample_size": len(dev_features),
        "development_seasons": ["2024", "2025", "2026"],
        "hashes": {
            "v10_feature_schema_hash": schema_hash,
            "v10_model_spec_hash": spec_hash,
            "v10_confirmation_protocol_hash": proto_hash,
            "v10_probability_model_hash": prob_hash,
        },
        "model_weights": {
            "away_intercept": float(model.model_away.intercept_),
            "away_coefficients": [float(c) for c in model.model_away.coef_],
            "away_alpha": float(model.model_away.alpha_),
            "home_intercept": float(model.model_home.intercept_),
            "home_coefficients": [float(c) for c in model.model_home.coef_],
            "home_alpha": float(model.model_home.alpha_),
        },
        "market_calibration": {
            "m0b_mean_residual": round(mean_bias_m0b, 4),
            "m4_1_alpha": round(alpha_cal, 4),
            "m4_1_beta": round(beta_cal, 4),
        },
        "empirical_oof_error_distribution": {
            "n_errors": len(oof_unexplained_errors),
            "mean": round(float(np.mean(oof_unexplained_errors)), 4),
            "std": round(float(np.std(oof_unexplained_errors)), 4),
            "median": round(float(np.median(oof_unexplained_errors)), 4),
            "p10": round(float(np.percentile(oof_unexplained_errors, 10)), 4),
            "p25": round(float(np.percentile(oof_unexplained_errors, 25)), 4),
            "p75": round(float(np.percentile(oof_unexplained_errors, 75)), 4),
            "p90": round(float(np.percentile(oof_unexplained_errors, 90)), 4),
            "oof_errors_sample": [round(float(e), 4) for e in oof_unexplained_errors],
        },
        "feature_names_away": [
            "sp_share",
            "bp_share",
            "sp_woba_edge",
            "sp_k_effect",
            "sp_bb_effect",
            "sp_k_interaction",
            "sp_tto",
            "sp_rest",
            "bp_woba_edge",
            "bp_fip_effect",
            "bp_fresh_effect",
            "bp_hl_effect",
            "bp_pitches",
            "iso_power",
            "barrel_power",
            "park_effect",
            "density_effect",
            "wind_barrel",
            "temp_iso",
        ],
        "feature_names_home": [
            "sp_share",
            "bp_share",
            "sp_woba_edge",
            "sp_k_effect",
            "sp_bb_effect",
            "sp_k_interaction",
            "sp_tto",
            "sp_rest",
            "bp_woba_edge",
            "bp_fip_effect",
            "bp_fresh_effect",
            "bp_hl_effect",
            "bp_pitches",
            "iso_power",
            "barrel_power",
            "park_effect",
            "density_effect",
            "wind_barrel",
            "temp_iso",
            "home_advantage",
        ],
    }

    out_path = runtime_paths.repo_root / "config/models/research/mlb_structural_v10_frozen.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact_dict, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = freeze_v10_artifact()
    print(f"Frozen v10 artifact saved to: {p}")
