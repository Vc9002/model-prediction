"""MLB Structural v10 Challenger Model.

Implements decomposed team run expectancy:
  E[Runs_away] = E[Runs_vsStarter] + E[Runs_vsBullpen]
  E[Runs_home] = E[Runs_vsStarter] + E[Runs_vsBullpen]
  E[Total]     = E[Runs_away] + E[Runs_home]
  E[Margin]    = E[Runs_home] - E[Runs_away]

Starter and bullpen interact through expected innings allocation E[IP_SP] and (9.0 - E[IP_SP]).
Fitted via regularized GLM (Ridge / Poisson / Log-linear) strictly on training data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import Ridge, RidgeCV
from sklearn.preprocessing import StandardScaler

from ..features.mlb_v10_features import (
    LEAGUE_BARREL_PCT,
    LEAGUE_BB_PCT,
    LEAGUE_FIP,
    LEAGUE_ISO,
    LEAGUE_K_PCT,
    LEAGUE_XWOBA,
    MLBv10FeatureVector,
)


@dataclass(slots=True)
class MLBv10Prediction:
    """Decomposed structural prediction for an MLB game."""

    event_id: str
    home_team: str
    away_team: str
    projected_away_runs: float
    projected_home_runs: float
    projected_total_runs: float
    projected_home_margin: float
    away_sp_innings_share: float
    home_sp_innings_share: float
    away_vs_sp_runs: float
    away_vs_bp_runs: float
    home_vs_sp_runs: float
    home_vs_bp_runs: float


class MLBStructuralV10Model:
    """MLB Structural v10 Model with Decomposed Starter + Bullpen Scoring."""

    def __init__(self, ridge_alpha: float = 10.0) -> None:
        self.ridge_alpha = ridge_alpha
        self.scaler = StandardScaler()
        self.model_away = Ridge(alpha=ridge_alpha)
        self.model_home = Ridge(alpha=ridge_alpha)
        self.fitted = False
        self.base_runs_per_inning = 4.40 / 9.0  # ~0.489 runs/inning

    def _build_feature_row_away(self, f: MLBv10FeatureVector) -> np.ndarray:
        """Construct feature vector for Away offense scoring rate components."""
        # Away offense faces Home SP for home_sp_expected_ip, then Home BP
        sp_ip = f.home_sp_expected_ip
        bp_ip = max(1.0, 9.0 - sp_ip)
        sp_share = sp_ip / 9.0
        bp_share = bp_ip / 9.0

        # Starter component features
        sp_woba_edge = (f.away_lineup_xwoba_vs_sp - LEAGUE_XWOBA) * sp_share
        sp_k_effect = (f.home_sp_k_pct - LEAGUE_K_PCT) * sp_share
        sp_bb_effect = (f.home_sp_bb_pct - LEAGUE_BB_PCT) * sp_share
        sp_k_interaction = f.away_matchup_k_interaction * sp_share
        sp_tto = (f.home_sp_tto_penalty - 1.0) * sp_share
        sp_rest = ((f.home_sp_rest_days - 5.0) / 5.0) * sp_share

        # Bullpen component features
        bp_woba_edge = (f.away_lineup_xwoba_vs_sp - LEAGUE_XWOBA) * bp_share
        bp_fip_effect = ((f.home_bp_effective_fip - LEAGUE_FIP) / 2.0) * bp_share
        bp_fresh_effect = (f.home_bp_freshness - 0.75) * bp_share
        bp_hl_effect = (f.home_bp_hl_available - 0.5) * bp_share
        bp_pitches = (f.home_bp_pitches_3d / 100.0) * bp_share

        # Environment & Offense Power
        iso_power = f.away_lineup_iso - LEAGUE_ISO
        barrel_power = f.away_lineup_barrel_pct - LEAGUE_BARREL_PCT
        park_effect = f.park_factor - 1.0
        density_effect = f.fly_ball_distance_factor - 1.0
        wind_barrel = f.wind_out_x_barrel
        temp_iso = f.temp_x_iso

        return np.array(
            [
                sp_share,
                bp_share,
                sp_woba_edge,
                sp_k_effect,
                sp_bb_effect,
                sp_k_interaction,
                sp_tto,
                sp_rest,
                bp_woba_edge,
                bp_fip_effect,
                bp_fresh_effect,
                bp_hl_effect,
                bp_pitches,
                iso_power,
                barrel_power,
                park_effect,
                density_effect,
                wind_barrel,
                temp_iso,
            ],
            dtype=np.float64,
        )

    def _build_feature_row_home(self, f: MLBv10FeatureVector) -> np.ndarray:
        """Construct feature vector for Home offense scoring rate components."""
        # Home offense faces Away SP for away_sp_expected_ip, then Away BP
        sp_ip = f.away_sp_expected_ip
        bp_ip = max(1.0, 8.5 - sp_ip)
        sp_share = sp_ip / 8.5
        bp_share = bp_ip / 8.5

        # Starter component features
        sp_woba_edge = (f.home_lineup_xwoba_vs_sp - LEAGUE_XWOBA) * sp_share
        sp_k_effect = (f.away_sp_k_pct - LEAGUE_K_PCT) * sp_share
        sp_bb_effect = (f.away_sp_bb_pct - LEAGUE_BB_PCT) * sp_share
        sp_k_interaction = f.home_matchup_k_interaction * sp_share
        sp_tto = (f.away_sp_tto_penalty - 1.0) * sp_share
        sp_rest = ((f.away_sp_rest_days - 5.0) / 5.0) * sp_share

        # Bullpen component features
        bp_woba_edge = (f.home_lineup_xwoba_vs_sp - LEAGUE_XWOBA) * bp_share
        bp_fip_effect = ((f.away_bp_effective_fip - LEAGUE_FIP) / 2.0) * bp_share
        bp_fresh_effect = (f.away_bp_freshness - 0.75) * bp_share
        bp_hl_effect = (f.away_bp_hl_available - 0.5) * bp_share
        bp_pitches = (f.away_bp_pitches_3d / 100.0) * bp_share

        # Environment & Offense Power
        iso_power = f.home_lineup_iso - LEAGUE_ISO
        barrel_power = f.home_lineup_barrel_pct - LEAGUE_BARREL_PCT
        park_effect = f.park_factor - 1.0
        density_effect = f.fly_ball_distance_factor - 1.0
        wind_barrel = f.wind_out_x_barrel
        temp_iso = f.temp_x_iso
        home_advantage = 0.15  # Home field advantage constant

        return np.array(
            [
                sp_share,
                bp_share,
                sp_woba_edge,
                sp_k_effect,
                sp_bb_effect,
                sp_k_interaction,
                sp_tto,
                sp_rest,
                bp_woba_edge,
                bp_fip_effect,
                bp_fresh_effect,
                bp_hl_effect,
                bp_pitches,
                iso_power,
                barrel_power,
                park_effect,
                density_effect,
                wind_barrel,
                temp_iso,
                home_advantage,
            ],
            dtype=np.float64,
        )

    def fit(
        self,
        features: list[MLBv10FeatureVector],
        actual_away_runs: list[float],
        actual_home_runs: list[float],
    ) -> MLBStructuralV10Model:
        """Fit Ridge regularized scoring parameters strictly on training sample."""
        if len(features) < 10:
            self.fitted = True
            return self

        X_away = np.array([self._build_feature_row_away(f) for f in features], dtype=np.float64)
        y_away = np.array(actual_away_runs, dtype=np.float64)

        X_home = np.array([self._build_feature_row_home(f) for f in features], dtype=np.float64)
        y_home = np.array(actual_home_runs, dtype=np.float64)

        # Cross-validated Ridge regression
        alphas = np.logspace(-1, 3, 20)
        cv_away = RidgeCV(alphas=alphas).fit(X_away, y_away)
        cv_home = RidgeCV(alphas=alphas).fit(X_home, y_home)

        self.model_away = cv_away
        self.model_home = cv_home
        self.fitted = True
        return self

    def predict(self, f: MLBv10FeatureVector) -> MLBv10Prediction:
        """Generate decomposed structural prediction for a matchup."""
        if not self.fitted:
            # Fallback to league baseline
            mu_a = 4.30 * f.park_factor
            mu_h = 4.55 * f.park_factor
            return MLBv10Prediction(
                event_id=f.event_id,
                home_team=f.home_team,
                away_team=f.away_team,
                projected_away_runs=round(mu_a, 2),
                projected_home_runs=round(mu_h, 2),
                projected_total_runs=round(mu_a + mu_h, 2),
                projected_home_margin=round(mu_h - mu_a, 2),
                away_sp_innings_share=f.home_sp_expected_ip / 9.0,
                home_sp_innings_share=f.away_sp_expected_ip / 8.5,
                away_vs_sp_runs=round(mu_a * 0.6, 2),
                away_vs_bp_runs=round(mu_a * 0.4, 2),
                home_vs_sp_runs=round(mu_h * 0.6, 2),
                home_vs_bp_runs=round(mu_h * 0.4, 2),
            )

        x_a = self._build_feature_row_away(f).reshape(1, -1)
        x_h = self._build_feature_row_home(f).reshape(1, -1)

        pred_away = float(self.model_away.predict(x_a)[0])
        pred_home = float(self.model_home.predict(x_h)[0])

        # Apply biological / physical bounds on expected runs [1.5, 11.0]
        mu_away = max(1.5, min(11.0, pred_away))
        mu_home = max(1.5, min(11.0, pred_home))
        total = round(mu_away + mu_home, 2)
        margin = round(mu_home - mu_away, 2)

        sp_share_a = f.home_sp_expected_ip / 9.0
        sp_share_h = f.away_sp_expected_ip / 8.5

        return MLBv10Prediction(
            event_id=f.event_id,
            home_team=f.home_team,
            away_team=f.away_team,
            projected_away_runs=round(mu_away, 2),
            projected_home_runs=round(mu_home, 2),
            projected_total_runs=total,
            projected_home_margin=margin,
            away_sp_innings_share=round(sp_share_a, 3),
            home_sp_innings_share=round(sp_share_h, 3),
            away_vs_sp_runs=round(mu_away * sp_share_a, 2),
            away_vs_bp_runs=round(mu_away * (1.0 - sp_share_a), 2),
            home_vs_sp_runs=round(mu_home * sp_share_h, 2),
            home_vs_bp_runs=round(mu_home * (1.0 - sp_share_h), 2),
        )
