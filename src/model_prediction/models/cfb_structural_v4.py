"""College Football Structural v4 Unified Scoring Model (cfb-structural-v4).

Overhauled score distribution engine for NCAAF:
1. Predicts HomePoints and AwayPoints independently from opponent-adjusted efficiency (EPA/play),
   pace, situational travel/altitude fatigue, weather mechanisms, and QB mixture uncertainty.
2. Derives coherent Moneyline, Spread (-3, -7, key numbers), and Total markets from the
   joint bivariate Negative Binomial distribution:
   - P(HomeWin) = sum_{h > a} P(H=h, A=a)
   - P(Cover) = sum_{h - a > line} P(H=h, A=a)
   - P(Over) = sum_{h + a > line} P(H=h, A=a)
3. Addresses historical CFB model failure modes:
   - Full Board Edge Optimization: Markets select sides based on maximum predictive edge
     against the executable ask, eliminating negative-edge chalk selection.
   - Restored Collegiate Margin SD: Calibrated margin SD to empirical collegiate variance
     (16.2 pts), preventing artificial cover overconfidence on mid-range spreads.
   - Calibrated Modern Clock Pace: Accommodates NCAA running first-down clock rules.
   - Blowout Disparity Tail Expansion: Expands effective margin SD on spreads > 21.5 pts
     to account for fourth-quarter starter benching, garbage-time volatility, and depth disparity.
   - Dynamic Edge-Scaled Sizing: Sizing dynamically scales between 1.00U and 2.00U based on model edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain import parse_utc
from ..features.cfb_features import (
    CFBFeatureExtractor,
    CFBMatchupFeatures,
)
from ..pricing import implied_probability
from .base import GamePrediction
from .cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
)

CFB_V4_MODEL_VERSION = "cfb-structural-v4"
CFB_V4_DEFAULT_HOME_ADVANTAGE = 2.5
CFB_V4_BASELINE_MARGIN_SD = 16.2
CFB_V4_BASELINE_TOTAL_SD = 16.0
CFB_BLOWOUT_SPREAD_THRESHOLD = 21.5
CFB_BLOWOUT_MARGIN_SCALE = 0.025
STANDARD_LAY_ASK = round(implied_probability(-110), 6)


@dataclass(frozen=True)
class CFBStructuralForecastV4:
    """Probabilistic score forecast from CFB Structural v4."""

    home_expected_points: float
    away_expected_points: float
    projected_margin_home: float
    projected_total: float
    prob_home_win: float
    prob_away_win: float
    spread_home_line: float
    prob_home_cover: float
    prob_away_cover: float
    prob_push_spread: float
    total_line: float
    prob_over: float
    prob_under: float
    prob_push_total: float
    home_qb_uncertainty: float
    away_qb_uncertainty: float
    altitude_travel_adj: float
    weather_points_adj: float
    effective_margin_sd: float


class CFBStructuralV4Model:
    """NCAAF Structural v4 Game & Market Engine."""

    version: str = CFB_V4_MODEL_VERSION

    def __init__(
        self,
        home_advantage_points: float = CFB_V4_DEFAULT_HOME_ADVANTAGE,
        margin_sd: float = CFB_V4_BASELINE_MARGIN_SD,
        total_sd: float = CFB_V4_BASELINE_TOTAL_SD,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
    ) -> None:
        self.home_advantage_points = home_advantage_points
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.distribution_type = distribution_type
        self.extractor = CFBFeatureExtractor(
            home_advantage_points=home_advantage_points,
            margin_sd=margin_sd,
            total_sd=total_sd,
        )
        self.distribution_engine = CFBJointDistributionEngine(
            distribution_type=distribution_type,
            margin_sd=margin_sd,
            total_sd=total_sd,
        )

    def compute_effective_margin_sd(
        self,
        spread_home_line: float,
        uncertainty: float = 0.04,
    ) -> float:
        """Compute effective margin standard deviation with blowout and uncertainty scaling."""
        sd = self.margin_sd * (1.0 + 2.0 * max(0.0, uncertainty - 0.04))
        abs_spread = abs(spread_home_line)
        if abs_spread > CFB_BLOWOUT_SPREAD_THRESHOLD:
            excess = min(15.0, abs_spread - CFB_BLOWOUT_SPREAD_THRESHOLD)
            sd *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * excess
        return round(sd, 3)

    def forecast_game(
        self,
        matchup_features: CFBMatchupFeatures,
        spread_home_line: float = -3.5,
        total_line: float = 52.5,
    ) -> CFBStructuralForecastV4:
        """Compute structural home/away point expectations and joint market probabilities."""
        mu_home = matchup_features.projected_home_points
        mu_away = matchup_features.projected_away_points

        effective_margin_sd = self.compute_effective_margin_sd(
            spread_home_line=spread_home_line,
            uncertainty=matchup_features.uncertainty,
        )

        orig_margin_sd = self.distribution_engine.margin_sd
        self.distribution_engine.margin_sd = effective_margin_sd
        try:
            joint_probs = self.distribution_engine.compute_market_probabilities(
                mu_away=mu_away,
                mu_home=mu_home,
                spread_home_line=spread_home_line,
                total_line=total_line,
            )
        finally:
            self.distribution_engine.margin_sd = orig_margin_sd

        return CFBStructuralForecastV4(
            home_expected_points=round(mu_home, 2),
            away_expected_points=round(mu_away, 2),
            projected_margin_home=round(mu_home - mu_away, 2),
            projected_total=round(mu_home + mu_away, 2),
            prob_home_win=round(joint_probs.p_home_win, 4),
            prob_away_win=round(joint_probs.p_away_win, 4),
            spread_home_line=spread_home_line,
            prob_home_cover=round(joint_probs.p_home_cover, 4),
            prob_away_cover=round(joint_probs.p_away_cover, 4),
            prob_push_spread=round(joint_probs.p_push_spread, 4),
            total_line=total_line,
            prob_over=round(joint_probs.p_over, 4),
            prob_under=round(joint_probs.p_under, 4),
            prob_push_total=round(joint_probs.p_push_total, 4),
            home_qb_uncertainty=round(matchup_features.home_qb_value_adjustment, 3),
            away_qb_uncertainty=round(matchup_features.away_qb_value_adjustment, 3),
            altitude_travel_adj=round(
                matchup_features.altitude_fatigue_penalty
                + (matchup_features.travel_distance_miles / 1000.0 * 0.35),
                2,
            ),
            weather_points_adj=round(matchup_features.weather_total_adjustment, 2),
            effective_margin_sd=effective_margin_sd,
        )

    def predict_matchup(
        self,
        history: list[Any],
        game: Any,
    ) -> list[GamePrediction]:
        """Generate standardized GamePrediction objects for Moneyline, Spread, and Total."""
        game_year = None
        if getattr(game, "event_start_utc", None):
            try:
                dt = parse_utc(game.event_start_utc)
                game_year = dt.year - 1 if dt.month == 1 else dt.year
            except Exception:  # noqa: BLE001, S110
                pass
        resolved_season_year = getattr(game, "season_year", None) or game_year or 2026

        feat: CFBMatchupFeatures = self.extractor.extract_features(
            history=history,
            away_team=game.away_team,
            home_team=game.home_team,
            event_id=game.event_id,
            game_start_utc=game.event_start_utc,
            season_year=resolved_season_year,
            week=getattr(game, "week", 1),
            wind_mph=getattr(game, "wind_mph", None),
            temperature_f=getattr(game, "temperature_f", None),
            precipitation_in=getattr(game, "precipitation_in", None),
            is_neutral_site=getattr(game, "is_neutral_site", False),
            qb_starter_prob_away=getattr(game, "qb_starter_prob_away", 1.0),
            qb_starter_prob_home=getattr(game, "qb_starter_prob_home", 1.0),
        )
        spread_home_line = getattr(game, "spread_home_line", -3.5) or -3.5
        sp_away_line = -spread_home_line
        tot_line = getattr(game, "total_line", 52.5) or 52.5

        fc = self.forecast_game(feat, spread_home_line=spread_home_line, total_line=tot_line)

        predictions: list[GamePrediction] = []

        # 1. Moneyline
        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="moneyline",
                line=None,
                probabilities={
                    "home": round(fc.prob_home_win, 6),
                    "away": round(fc.prob_away_win, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=self.version,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"CFB Structural v4: proj {game.away_team} {fc.away_expected_points:.1f} @ "
                    f"{game.home_team} {fc.home_expected_points:.1f} (Margin {fc.projected_margin_home:+.1f}). "
                    f"Total {fc.projected_total:.1f}. Weather adj: {fc.weather_points_adj:+.1f} pts. "
                    f"Eff margin SD: {fc.effective_margin_sd:.1f}."
                ),
            )
        )

        # 2. Spread
        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="spread",
                line=sp_away_line,
                probabilities={
                    "away": round(fc.prob_away_cover, 6),
                    "home": round(fc.prob_home_cover, 6),
                    "push": round(fc.prob_push_spread, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=self.version,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"CFB Structural v4 Spread: Home {spread_home_line:+.1f} / Away {sp_away_line:+.1f} "
                    f"(P(Home cover)={fc.prob_home_cover:.4f}, P(Away cover)={fc.prob_away_cover:.4f}, "
                    f"P(Push)={fc.prob_push_spread:.4f}). Calibrated margin SD: {fc.effective_margin_sd:.1f}."
                ),
            )
        )

        # 3. Total (with calibrated pace & tail shrinkage on extreme probabilities > 0.60)
        p_over_val = fc.prob_over
        p_under_val = fc.prob_under
        if p_over_val > 0.60:
            p_over_val = 0.60 + 0.35 * (p_over_val - 0.60)
            p_under_val = 1.0 - p_over_val
        elif p_under_val > 0.60:
            p_under_val = 0.60 + 0.35 * (p_under_val - 0.60)
            p_over_val = 1.0 - p_under_val

        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="total",
                line=tot_line,
                probabilities={
                    "over": round(p_over_val, 6),
                    "under": round(p_under_val, 6),
                    "push": round(fc.prob_push_total, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=self.version,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"CFB Structural v4 Total: line {tot_line:.1f} "
                    f"(P(Over)={fc.prob_over:.4f}, P(Under)={fc.prob_under:.4f}, "
                    f"P(Push)={fc.prob_push_total:.4f})."
                ),
            )
        )

        return predictions


cfb_structural_v4_model = CFBStructuralV4Model()
