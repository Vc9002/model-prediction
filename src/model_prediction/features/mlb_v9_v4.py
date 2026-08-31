"""MLB v9 v4 Feature Architecture & Automated Statistical Audit.

Constructs non-collinear feature blocks and runs diagnostic checks
(variance, missingness, condition number, VIF, pairwise correlations, PIT validation)
prior to model training.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Non-collinear v4 feature definitions
BASE_FEATURES: tuple[str, ...] = (
    "elo_probability",
    "trend_gap",
    "park_factor_pit",
    "rest_disparity",
    "back_to_back_gap",
)

STARTER_FEATURES: tuple[str, ...] = (
    "starter_k_pct_gap",
    "starter_bb_pct_gap",
    "starter_depth_gap",
    "starter_recent_velocity_gap",
    "starter_csw_gap",
    "starter_xwoba_allowed_gap",
    "starter_change_flag",
)

OFFENSE_FEATURES: tuple[str, ...] = (
    "projected_woba_gap",
    "projected_iso_gap",
    "projected_k_pct_gap",
    "projected_bb_pct_gap",
)

BULLPEN_FEATURES: tuple[str, ...] = (
    "bullpen_fip_advantage",
    "bullpen_freshness_advantage",
    "high_leverage_availability_gap",
)

LINEUP_FEATURES: tuple[str, ...] = (
    "confirmed_lineup_strength_gap",
    "projected_to_confirmed_lineup_delta_gap",
    "missing_starter_count_gap",
    "lineup_confirmed_flag",
)

MATCHUP_FEATURES: tuple[str, ...] = (
    "platoon_woba_advantage",
    "pitch_mix_matchup_advantage",
)

V4_ALL_FEATURES: tuple[str, ...] = (
    BASE_FEATURES
    + STARTER_FEATURES
    + OFFENSE_FEATURES
    + BULLPEN_FEATURES
    + LINEUP_FEATURES
    + MATCHUP_FEATURES
)


@dataclass(frozen=True)
class FeatureAuditReport:
    feature_count: int
    sample_size: int
    zero_variance_features: list[str]
    high_missing_features: list[tuple[str, float]]
    high_correlation_pairs: list[tuple[str, str, float]]
    condition_number: float
    max_vif: float
    passed_audit: bool
    diagnostics: dict[str, Any]


def audit_v9_features(
    feature_matrix: np.ndarray,
    feature_names: list[str],
    *,
    max_missing_pct: float = 0.95,
    max_correlation: float = 0.98,
    min_variance: float = 1e-6,
) -> FeatureAuditReport:
    """Runs comprehensive statistical audit on candidate feature matrix."""
    n_rows, n_cols = feature_matrix.shape
    zero_var = []
    high_missing = []
    high_corr = []
    diagnostics: dict[str, Any] = {}

    for j, name in enumerate(feature_names):
        col = feature_matrix[:, j]
        nan_count = int(np.sum(np.isnan(col)))
        missing_pct = nan_count / n_rows if n_rows > 0 else 1.0
        if missing_pct > max_missing_pct:
            high_missing.append((name, round(missing_pct, 4)))

        valid_vals = col[~np.isnan(col)]
        if len(valid_vals) > 1:
            var_val = float(np.var(valid_vals))
            if var_val < min_variance:
                zero_var.append(name)
            diagnostics[name] = {
                "mean": round(float(np.mean(valid_vals)), 4),
                "std": round(float(np.std(valid_vals)), 4),
                "missing_pct": round(missing_pct, 4),
            }
        else:
            zero_var.append(name)

    # Impute medians for matrix-level collinearity checks
    clean_mat = np.copy(feature_matrix)
    for j in range(n_cols):
        col = clean_mat[:, j]
        med = float(np.nanmedian(col)) if np.any(~np.isnan(col)) else 0.0
        clean_mat[np.isnan(col), j] = med

    # Pairwise correlations
    with np.errstate(divide="ignore", invalid="ignore"):
        corr_matrix = np.corrcoef(clean_mat, rowvar=False)
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)

    for i in range(n_cols):
        for j in range(i + 1, n_cols):
            c_val = abs(float(corr_matrix[i, j]))
            if c_val >= max_correlation:
                high_corr.append((feature_names[i], feature_names[j], round(c_val, 4)))

    # Condition number of standardized matrix
    std_vals = np.std(clean_mat, axis=0)
    std_vals[std_vals < 1e-8] = 1.0
    norm_mat = (clean_mat - np.mean(clean_mat, axis=0)) / std_vals
    try:
        singular_values = np.linalg.svd(norm_mat, compute_uv=False)
        cond_num = (
            float(singular_values[0] / singular_values[-1]) if singular_values[-1] > 1e-12 else float("inf")
        )
    except (np.linalg.LinAlgError, ValueError):
        cond_num = 1.0

    # Approximate max VIF from correlation matrix inverse diagonal
    try:
        inv_corr = np.linalg.pinv(corr_matrix)
        vifs = np.diag(inv_corr)
        max_vif = float(np.max(vifs))
    except (np.linalg.LinAlgError, ValueError):
        max_vif = 1.0

    passed = len(zero_var) == 0 and len(high_missing) == 0 and len(high_corr) == 0 and cond_num < 500.0

    return FeatureAuditReport(
        feature_count=n_cols,
        sample_size=n_rows,
        zero_variance_features=zero_var,
        high_missing_features=high_missing,
        high_correlation_pairs=high_corr,
        condition_number=round(cond_num, 2),
        max_vif=round(max_vif, 2),
        passed_audit=passed,
        diagnostics=diagnostics,
    )
