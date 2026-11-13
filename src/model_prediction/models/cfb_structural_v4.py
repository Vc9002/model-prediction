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
   - Calibrated Collegiate Margin SD: 14.5 pts baseline (walk-forward retune 2026-11-05,
     see CFB_V4_BASELINE_MARGIN_SD), widened per game by uncertainty, blowout lines and
     conference familiarity instead of one constant for every matchup.
   - Calibrated Modern Clock Pace: Accommodates NCAA running first-down clock rules.
   - Blowout Disparity Tail Expansion: Expands effective margin SD on spreads > 21.5 pts
     to account for fourth-quarter starter benching, garbage-time volatility, and depth disparity.
   - Dynamic Edge-Scaled Sizing: Sizing dynamically scales between 1.00U and 2.00U based on model edge.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from ..features.cfb_features import (
    CFB_UNCERTAINTY_FLOOR,
    CFBFeatureExtractor,
    CFBMatchupFeatures,
)
from ..pricing import implied_probability
from ..production_registry import compute_artifact_hash
from .base import GamePrediction
from .cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
)

CFB_V4_MODEL_VERSION = "cfb-structural-v4"
CFB_V4_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V4_MODEL_VERSION}.json"
CFB_V4_DEFAULT_HOME_ADVANTAGE = 2.5
# Walk-forward grid search on data/historical/ncaaf_games_all.jsonl (7,896 games,
# train 2016-2021 / val 2022 / holdout 2023-2024 -- see docs/handoffs/HANDOFF.md):
# margin_sd=14.5 (selected on validation, moneyline Brier 0.19721 vs 0.19760 at the
# prior 16.2) beat the prior value on the 2023-2024 holdout too (0.18779 vs 0.18848),
# confirmed via date-clustered bootstrap (95% CI [0.00007, 0.00131] excludes zero,
# P(improvement)=98.5%). Effect size is small; home_advantage_points showed no net
# benefit once combined with this change and was left at 2.5.
CFB_V4_BASELINE_MARGIN_SD = 14.5
CFB_V4_BASELINE_TOTAL_SD = 16.0
CFB_BLOWOUT_SPREAD_THRESHOLD = 21.5
CFB_BLOWOUT_MARGIN_SCALE = 0.025
CFB_TOTAL_DEVIATION_THRESHOLD = 8.0
CFB_TOTAL_DEVIATION_SCALE = 0.025
# Per unit of feat.uncertainty above its floor, widen margin_sd by this factor. Walk-forward
# residual check (2,434 games, 2023-2024): FBS-vs-FCS / thin-sample residual SD ~19.2 vs
# ~16.7 for established matchups, i.e. ~15% wider at the uncertainty ceiling -- not 1:1.
CFB_UNCERTAINTY_MARGIN_SD_SCALE = 2.0
# 15,000 draws gave a +/-0.8 pp 95% CI on a near-50% cover probability against a 2 pp
# min_edge gate -- simulation noise alone could create or erase a Main pick. 200,000 draws
# is +/-0.22 pp. The count is recorded in inference_inputs so replay reproduces it exactly.
CFB_V4_N_SIMULATIONS = 200_000
STANDARD_LAY_ASK = round(implied_probability(-110), 6)
_ARTIFACT_METADATA_KEYS = frozenset(
    {
        "artifact_hash",
        "model_version",
        "method",
        "note",
        "league",
        "sport",
        "schema_version",
        "positive_class",
        "qualification",
        "calibration",
    }
)
_ARTIFACT_PARAMETER_KEYS = frozenset(
    {"distribution", "home_advantage_points", "margin_sd", "total_sd", "features", "gates"}
)


def load_cfb_structural_v4_artifact(path: str | Path | None = None) -> dict[str, Any]:
    """Read the frozen v4 artifact, refusing a file whose identity or hash does not match.

    The serving model takes its parameters from here so that "cfb-structural-v4" on a
    ledger row always means the artifact's numbers. Review 2026-11-11 #1 found production
    serving a 16.2 margin_sd under the v4 label while the artifact (and the class every
    test exercised) said 14.5.
    """
    artifact_path = Path(path) if path is not None else CFB_V4_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V4_MODEL_VERSION:
        raise ValueError(f"cfb artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb artifact_hash mismatch for {artifact_path}")
    return payload


@dataclass(frozen=True)
class CFBMainGates:
    """The artifact's ``gates`` block as the Main ledger consumes it.

    ``max_moneyline_dog_odds`` is stored in the artifact as American odds; it is exposed
    here as the equivalent minimum executable ask (+200 -> 33.3%) because every decision
    path in this codebase prices in probabilities.
    """

    min_edge: float
    max_edge: float
    min_moneyline_ask: float
    max_spread_line: float
    block_fcs: bool
    sizing: str


def main_gates_from_artifact(artifact: Mapping[str, Any]) -> CFBMainGates:
    gates = artifact["gates"]
    min_edge = float(gates["min_edge"])
    max_edge = float(gates["max_edge"])
    if not 0.0 <= min_edge < max_edge < 1.0:
        raise ValueError(f"cfb artifact gates have an invalid edge band: {min_edge}..{max_edge}")
    dog_odds = float(gates["max_moneyline_dog_odds"])
    if dog_odds < 100.0:
        raise ValueError("cfb artifact max_moneyline_dog_odds must be a positive American price")
    return CFBMainGates(
        min_edge=min_edge,
        max_edge=max_edge,
        min_moneyline_ask=round(implied_probability(int(dog_odds)), 6),
        max_spread_line=float(gates["max_spread_line"]),
        block_fcs=bool(gates["block_fcs"]),
        sizing=str(gates["sizing"]),
    )


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
    effective_total_sd: float
    inference_inputs: dict[str, Any] | None = None


class CFBStructuralV4Model:
    """NCAAF Structural v4 Game & Market Engine."""

    version: str = CFB_V4_MODEL_VERSION

    def __init__(
        self,
        home_advantage_points: float = CFB_V4_DEFAULT_HOME_ADVANTAGE,
        margin_sd: float = CFB_V4_BASELINE_MARGIN_SD,
        total_sd: float = CFB_V4_BASELINE_TOTAL_SD,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
        n_simulations: int = CFB_V4_N_SIMULATIONS,
        random_seed: int = 42,
    ) -> None:
        self.home_advantage_points = home_advantage_points
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.distribution_type = distribution_type
        self.n_simulations = int(n_simulations)
        self.spread_version = self.version
        self.total_version = self.version
        self.extractor = CFBFeatureExtractor(
            home_advantage_points=home_advantage_points,
            margin_sd=margin_sd,
            total_sd=total_sd,
        )
        # Holds the one RNG stream every game in a slate draws from (see forecast_game).
        self.distribution_engine = CFBJointDistributionEngine(
            distribution_type=distribution_type,
            margin_sd=margin_sd,
            total_sd=total_sd,
            n_simulations=self.n_simulations,
            random_seed=random_seed,
        )

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any], **overrides: Any) -> CFBStructuralV4Model:
        """Build the served model from the frozen artifact's own parameters.

        Every declared feature must be a field the extractor produces, and every
        non-metadata key must be something the serving path reads: an artifact that
        advertises a feature or parameter the code never touches is a false claim to
        anyone auditing it, so it fails closed here instead of being ignored.
        """
        declared = tuple(str(name) for name in artifact["features"])
        produced = {field.name for field in fields(CFBMatchupFeatures)}
        unknown = sorted(set(declared) - produced)
        if unknown:
            raise ValueError(f"cfb artifact declares features the extractor never produces: {unknown}")
        unread = set(artifact) - _ARTIFACT_METADATA_KEYS - _ARTIFACT_PARAMETER_KEYS
        if unread:
            raise ValueError(
                f"cfb artifact carries parameters the served model never reads: {sorted(unread)}"
            )
        main_gates_from_artifact(artifact)
        params: dict[str, Any] = {
            "home_advantage_points": float(artifact["home_advantage_points"]),
            "margin_sd": float(artifact["margin_sd"]),
            "total_sd": float(artifact["total_sd"]),
            "distribution_type": CFBDistributionType(str(artifact["distribution"])),
        }
        params.update(overrides)
        return cls(**params)

    def compute_effective_margin_sd(
        self,
        spread_home_line: float,
        uncertainty: float = 0.04,
        is_conference_matchup: bool = False,
    ) -> float:
        """Compute effective margin standard deviation with conference, blowout and uncertainty scaling."""
        base_sd = self.margin_sd * (0.9135 if is_conference_matchup else 1.0)
        sd = base_sd * (1.0 + CFB_UNCERTAINTY_MARGIN_SD_SCALE * max(0.0, uncertainty - CFB_UNCERTAINTY_FLOOR))
        abs_spread = abs(spread_home_line)
        if abs_spread > CFB_BLOWOUT_SPREAD_THRESHOLD:
            excess = min(15.0, abs_spread - CFB_BLOWOUT_SPREAD_THRESHOLD)
            sd *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * excess
        return round(sd, 3)

    def compute_effective_total_sd(
        self,
        projected_total: float,
        total_line: float,
        uncertainty: float = 0.04,
    ) -> float:
        """Compute effective total standard deviation with uncertainty and large-deviation scaling.

        Mirrors compute_effective_margin_sd's shape (same uncertainty response; the
        margin side widens on an extreme *spread line*, this side widens on an extreme
        *projected deviation from the total line* -- the actual driver of the old
        post-hoc >0.60 probability clamp this replaces). A fixed-width distribution
        understates tail risk exactly when the model and the total line disagree a
        lot; widening total_sd here lets the simulated distribution express that
        disagreement instead of clamping the resulting probability after the fact.
        """
        sd = self.total_sd * (
            1.0 + CFB_UNCERTAINTY_MARGIN_SD_SCALE * max(0.0, uncertainty - CFB_UNCERTAINTY_FLOOR)
        )
        deviation = abs(projected_total - total_line)
        if deviation > CFB_TOTAL_DEVIATION_THRESHOLD:
            excess = min(20.0, deviation - CFB_TOTAL_DEVIATION_THRESHOLD)
            sd *= 1.0 + CFB_TOTAL_DEVIATION_SCALE * excess
        return round(sd, 3)

    def margin_sd_for_features(self, features: Mapping[str, Any], spread_home_line: float) -> float:
        """Effective margin SD for a priced line from a recorded feature dict.

        The candidate-evidence pass re-prices every listed line from captured features; this
        is the one entry point it uses, so a subclass with a different SD model (v5's slice
        table) overrides it and the slate builder never reaches into version-specific knobs.
        """
        return self.compute_effective_margin_sd(
            spread_home_line=spread_home_line,
            uncertainty=float(features.get("uncertainty", CFB_UNCERTAINTY_FLOOR)),
            is_conference_matchup=bool(features.get("is_conference_matchup", False)),
        )

    def total_sd_for_features(
        self, features: Mapping[str, Any], projected_total: float, total_line: float
    ) -> float:
        return self.compute_effective_total_sd(
            projected_total=projected_total,
            total_line=total_line,
            uncertainty=float(features.get("uncertainty", CFB_UNCERTAINTY_FLOOR)),
        )

    def forecast_game(
        self,
        matchup_features: CFBMatchupFeatures,
        spread_home_line: float,
        total_line: float,
    ) -> CFBStructuralForecastV4:
        """Compute structural home/away point expectations and joint market probabilities."""
        mu_home = matchup_features.projected_home_points
        mu_away = matchup_features.projected_away_points

        effective_margin_sd = self.compute_effective_margin_sd(
            spread_home_line=spread_home_line,
            uncertainty=matchup_features.uncertainty,
            is_conference_matchup=getattr(matchup_features, "is_conference_matchup", False),
        )

        effective_total_sd = self.compute_effective_total_sd(
            projected_total=mu_home + mu_away,
            total_line=total_line,
            uncertainty=matchup_features.uncertainty,
        )

        # A per-game engine carries this game's widened SDs, but it draws from the model's
        # single advancing RNG stream: a seed-only restart would hand every game in a slate
        # the same noise, and the replay contract (cfb_replay.capture_inputs) records the
        # exact pre-draw state so the second and later games reproduce too.
        from ..cfb_replay import capture_inputs

        engine = type(self.distribution_engine)(
            distribution_type=self.distribution_type,
            margin_sd=effective_margin_sd,
            total_sd=effective_total_sd,
            n_simulations=self.n_simulations,
        )
        engine.rng = self.distribution_engine.rng
        inference_inputs = capture_inputs(
            engine,
            mu_home=mu_home,
            mu_away=mu_away,
            spread_home_line=spread_home_line,
            total_line=total_line,
        )
        joint_probs = engine.compute_market_probabilities(
            mu_away=mu_away,
            mu_home=mu_home,
            spread_home_line=spread_home_line,
            total_line=total_line,
        )

        return CFBStructuralForecastV4(
            home_expected_points=round(mu_home, 2),
            away_expected_points=round(mu_away, 2),
            projected_margin_home=round(mu_home - mu_away, 2),
            projected_total=round(mu_home + mu_away, 2),
            prob_home_win=round(joint_probs.p_home_win, 6),
            prob_away_win=round(joint_probs.p_away_win, 6),
            spread_home_line=spread_home_line,
            prob_home_cover=round(joint_probs.p_home_cover, 6),
            prob_away_cover=round(joint_probs.p_away_cover, 6),
            prob_push_spread=round(joint_probs.p_push_spread, 6),
            total_line=total_line,
            prob_over=round(joint_probs.p_over, 6),
            prob_under=round(joint_probs.p_under, 6),
            prob_push_total=round(joint_probs.p_push_total, 6),
            home_qb_uncertainty=round(matchup_features.home_qb_value_adjustment, 3),
            away_qb_uncertainty=round(matchup_features.away_qb_value_adjustment, 3),
            altitude_travel_adj=round(
                matchup_features.altitude_fatigue_penalty
                + (matchup_features.travel_distance_miles / 1000.0 * 0.35),
                2,
            ),
            weather_points_adj=round(matchup_features.weather_total_adjustment, 2),
            effective_margin_sd=effective_margin_sd,
            effective_total_sd=effective_total_sd,
            inference_inputs=inference_inputs,
        )

    def predict_matchup(
        self,
        history: list[Any],
        game: Any,
        *,
        project_missing_lines: bool = False,
    ) -> list[GamePrediction]:
        """Generate standardized GamePrediction objects for Moneyline, Spread, and Total.

        Without market lines there is nothing to price, so the default refuses. The slate
        builder opts into ``project_missing_lines`` so a game with a Polymarket moneyline
        but no spread anywhere is still priced (the spread line only enters the moneyline
        through blowout SD widening); the candidate-evidence pass re-prices every listed
        line from the captured inputs regardless.
        """
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
        supplied_spread = getattr(game, "spread_home_line", None)
        if supplied_spread is None and getattr(game, "spread_away_line", None) is not None:
            supplied_spread = -float(game.spread_away_line)
        tot_line = getattr(game, "total_line", None)
        if supplied_spread is None or tot_line is None:
            if not project_missing_lines:
                return []
            if supplied_spread is None:
                supplied_spread = round(-feat.projected_margin_home * 2.0) / 2.0
            if tot_line is None:
                tot_line = feat.projected_total
        spread_home_line = float(supplied_spread)
        sp_away_line = -spread_home_line
        tot_line = float(tot_line)

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
                inference_inputs=fc.inference_inputs,
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
                model_version=self.spread_version,
                feature_basis=feat.to_dict(),
                inference_inputs=fc.inference_inputs,
                rationale=(
                    f"CFB Structural v4 Spread: Home {spread_home_line:+.1f} / Away {sp_away_line:+.1f} "
                    f"(P(Home cover)={fc.prob_home_cover:.4f}, P(Away cover)={fc.prob_away_cover:.4f}, "
                    f"P(Push)={fc.prob_push_spread:.4f}). Calibrated margin SD: {fc.effective_margin_sd:.1f}."
                ),
            )
        )

        # 3. Total (tail risk is expressed via effective_total_sd widening in forecast_game,
        # not a post-hoc probability clamp -- see compute_effective_total_sd)
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
                    "over": round(fc.prob_over, 6),
                    "under": round(fc.prob_under, 6),
                    "push": round(fc.prob_push_total, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=self.total_version,
                feature_basis=feat.to_dict(),
                inference_inputs=fc.inference_inputs,
                rationale=(
                    f"CFB Structural v4 Total: line {tot_line:.1f} "
                    f"(P(Over)={fc.prob_over:.4f}, P(Under)={fc.prob_under:.4f}, "
                    f"P(Push)={fc.prob_push_total:.4f}). Eff total SD: {fc.effective_total_sd:.1f}."
                ),
            )
        )

        return predictions


cfb_structural_v4_model = CFBStructuralV4Model()
