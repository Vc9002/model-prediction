"""College Football Structural v6 research challenger (cfb-structural-v6). INERT: nothing routes to it.

v6 replaces v5's v4-derived projection (an offense/defense half-sum averaged against fixed conference-tier
offsets) with an additive points model fitted by a joint ridge solve over every rated team
(``features/cfb_features_v6.py``), then applies the frozen mean calibration and per-slice outcome-residual SDs
for BOTH margin and total. It is a NEW class, not a subclass of the v4/v5 model: their ``predict_matchup``
runs the v4/v5 feature extractor.

Not a champion, not in ``config/production.yaml`` or ``config/model.yaml``, not imported by
``learned_forward.py``, ``college_football.py`` or any CLI module (``tests/test_cfb_structural_v6.py`` proves
this in a clean subprocess). The frozen fit that produced the artifact is
``outputs/ncaaf/cfb_v6_frozen_fit.json``; promotion would be a separate governed decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..features.cfb_features_v6 import CFBV6FeatureExtractor, CFBV6MatchupFeatures, V6Game
from ..production_registry import compute_artifact_hash
from .base import GamePrediction
from .cfb_distribution import CFBDistributionType, CFBJointDistributionEngine
from .cfb_structural_v4 import CFB_V4_N_SIMULATIONS, CFBStructuralForecastV4

CFB_V6_MODEL_VERSION = "cfb-structural-v6"
CFB_V6_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V6_MODEL_VERSION}.json"
_METADATA_KEYS = frozenset(
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
        "training",
    }
)
_PARAMETER_KEYS = frozenset(
    {
        "distribution",
        "margin_calibration",
        "total_calibration",
        "margin_sd_table",
        "total_sd_table",
        "established_min_sample",
        "solver",
        "features",
    }
)
_SOLVER_KEYS = frozenset({"lambda", "w_prev", "result_buffer_hours"})
SD_SLICES = ("established", "thin_sample", "fbs_vs_fcs")
# Engine guards, not fitted values: the replay contract requires strictly positive side means and v5 floors the
# calibrated total and each side the same way. They bind only on extreme lopsided projections.
MIN_CALIBRATED_TOTAL = 24.0
MIN_SIDE_MEAN = 3.0


def load_cfb_structural_v6_artifact(path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(path) if path is not None else CFB_V6_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V6_MODEL_VERSION:
        raise ValueError(f"cfb v6 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v6 artifact_hash mismatch for {artifact_path}")
    return payload


class CFBStructuralV6Model:
    """Frozen v6 additive projection + mean calibration + slice margin/total SDs."""

    version: str = CFB_V6_MODEL_VERSION

    def __init__(
        self,
        *,
        margin_calibration: Mapping[str, float],
        total_calibration: Mapping[str, float],
        margin_sd_table: Mapping[str, float],
        total_sd_table: Mapping[str, float],
        established_min_sample: int,
        solver: Mapping[str, float],
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
        n_simulations: int = CFB_V4_N_SIMULATIONS,
        random_seed: int = 42,
    ) -> None:
        for name, table in (("margin_sd_table", margin_sd_table), ("total_sd_table", total_sd_table)):
            missing = [k for k in SD_SLICES if k not in table]
            if missing:
                raise ValueError(f"{name} missing slices: {missing}")
            if any(float(table[k]) <= 0 for k in SD_SLICES):
                raise ValueError(f"{name} needs positive SDs")
        for name, cal in (
            ("margin_calibration", margin_calibration),
            ("total_calibration", total_calibration),
        ):
            if "intercept" not in cal or "slope" not in cal or float(cal["slope"]) <= 0:
                raise ValueError(f"{name} needs a positive slope and an intercept")
        if set(solver) != _SOLVER_KEYS:
            raise ValueError(f"solver block must carry exactly {sorted(_SOLVER_KEYS)}")
        self.margin_calibration = {k: float(margin_calibration[k]) for k in ("intercept", "slope")}
        self.total_calibration = {k: float(total_calibration[k]) for k in ("intercept", "slope")}
        self.margin_sd_table = {k: float(margin_sd_table[k]) for k in SD_SLICES}
        self.total_sd_table = {k: float(total_sd_table[k]) for k in SD_SLICES}
        self.established_min_sample = int(established_min_sample)
        self.solver = {k: float(solver[k]) for k in _SOLVER_KEYS}
        self.distribution_type = distribution_type
        self.n_simulations = int(n_simulations)
        self.spread_version = self.version
        self.total_version = self.version
        # The one RNG stream every game in a slate draws from, wired exactly as v5 wires it.
        self.distribution_engine = CFBJointDistributionEngine(
            distribution_type=distribution_type,
            margin_sd=self.margin_sd_table["established"],
            total_sd=self.total_sd_table["established"],
            n_simulations=self.n_simulations,
            random_seed=random_seed,
        )

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any], **overrides: Any) -> CFBStructuralV6Model:
        declared = tuple(str(name) for name in artifact["features"])
        produced = {field.name for field in fields(CFBV6MatchupFeatures)}
        unknown = sorted(set(declared) - produced)
        if unknown:
            raise ValueError(f"cfb v6 artifact declares features the extractor never produces: {unknown}")
        unread = set(artifact) - _METADATA_KEYS - _PARAMETER_KEYS
        if unread:
            raise ValueError(
                f"cfb v6 artifact carries parameters the served model never reads: {sorted(unread)}"
            )
        params: dict[str, Any] = {
            "distribution_type": CFBDistributionType(str(artifact["distribution"])),
            "margin_calibration": artifact["margin_calibration"],
            "total_calibration": artifact["total_calibration"],
            "margin_sd_table": artifact["margin_sd_table"],
            "total_sd_table": artifact["total_sd_table"],
            "established_min_sample": int(artifact["established_min_sample"]),
            "solver": artifact["solver"],
        }
        params.update(overrides)
        return cls(**params)

    def extractor_for(
        self, history: Sequence[V6Game], *, backfill_sha256: str | None = None
    ) -> CFBV6FeatureExtractor:
        """Feature extractor wired to this artifact's solver config (lambda, w_prev, result buffer)."""
        return CFBV6FeatureExtractor(
            list(history),
            lam=self.solver["lambda"],
            w_prev=self.solver["w_prev"],
            buffer_hours=self.solver["result_buffer_hours"],
            backfill_sha256=backfill_sha256,
        )

    # ------------------------------------------------------------------ fitted layers
    def calibrate_means(self, projected_margin_home: float, projected_total: float) -> tuple[float, float]:
        margin = (
            self.margin_calibration["intercept"] + self.margin_calibration["slope"] * projected_margin_home
        )
        total = self.total_calibration["intercept"] + self.total_calibration["slope"] * projected_total
        return margin, max(MIN_CALIBRATED_TOTAL, total)

    def sd_slice(self, *, is_fbs_vs_fcs: bool, sample_games: int) -> str:
        if is_fbs_vs_fcs:
            return "fbs_vs_fcs"
        return "established" if sample_games >= self.established_min_sample else "thin_sample"

    def margin_sd_for(self, *, is_fbs_vs_fcs: bool, sample_games: int) -> float:
        return self.margin_sd_table[self.sd_slice(is_fbs_vs_fcs=is_fbs_vs_fcs, sample_games=sample_games)]

    def total_sd_for(self, *, is_fbs_vs_fcs: bool, sample_games: int) -> float:
        return self.total_sd_table[self.sd_slice(is_fbs_vs_fcs=is_fbs_vs_fcs, sample_games=sample_games)]

    def forecast_game(
        self,
        matchup_features: CFBV6MatchupFeatures,
        spread_home_line: float,
        total_line: float,
    ) -> CFBStructuralForecastV4:
        from ..cfb_replay import capture_inputs

        margin, total = self.calibrate_means(
            matchup_features.projected_margin_home, matchup_features.projected_total
        )
        mu_home = max(MIN_SIDE_MEAN, (total + margin) / 2.0)
        mu_away = max(MIN_SIDE_MEAN, (total - margin) / 2.0)
        effective_margin_sd = self.margin_sd_for(
            is_fbs_vs_fcs=matchup_features.is_fbs_vs_fcs, sample_games=matchup_features.sample_games
        )
        effective_total_sd = self.total_sd_for(
            is_fbs_vs_fcs=matchup_features.is_fbs_vs_fcs, sample_games=matchup_features.sample_games
        )
        engine = CFBJointDistributionEngine(
            distribution_type=self.distribution_type,
            margin_sd=effective_margin_sd,
            total_sd=effective_total_sd,
            n_simulations=self.n_simulations,
        )
        engine.rng = self.distribution_engine.rng
        inference_inputs = capture_inputs(
            engine, mu_home=mu_home, mu_away=mu_away, spread_home_line=spread_home_line, total_line=total_line
        )
        joint = engine.compute_market_probabilities(
            mu_away=mu_away, mu_home=mu_home, spread_home_line=spread_home_line, total_line=total_line
        )
        # No QB, altitude/travel or weather point adjustment exists in v6 (D-NO-HANDSET-TERMS): fields stay 0.0.
        return CFBStructuralForecastV4(
            home_expected_points=round(mu_home, 2),
            away_expected_points=round(mu_away, 2),
            projected_margin_home=round(mu_home - mu_away, 2),
            projected_total=round(mu_home + mu_away, 2),
            prob_home_win=round(joint.p_home_win, 6),
            prob_away_win=round(joint.p_away_win, 6),
            spread_home_line=spread_home_line,
            prob_home_cover=round(joint.p_home_cover, 6),
            prob_away_cover=round(joint.p_away_cover, 6),
            prob_push_spread=round(joint.p_push_spread, 6),
            total_line=total_line,
            prob_over=round(joint.p_over, 6),
            prob_under=round(joint.p_under, 6),
            prob_push_total=round(joint.p_push_total, 6),
            home_qb_uncertainty=0.0,
            away_qb_uncertainty=0.0,
            altitude_travel_adj=0.0,
            weather_points_adj=0.0,
            effective_margin_sd=effective_margin_sd,
            effective_total_sd=effective_total_sd,
            inference_inputs=inference_inputs,
        )

    def predict_matchup(
        self,
        history: Sequence[V6Game] | CFBV6FeatureExtractor,
        game: Any,
        *,
        project_missing_lines: bool = False,
    ) -> list[GamePrediction]:
        """Moneyline, spread and total predictions, mirroring v4's three blocks with v6 version labels.

        ``history`` is a V6Game list (an extractor is built from it) or a ready extractor. An unrated team raises
        ``V6NoCall`` (NO_CALL_CFB_V6_UNRATED_TEAM): the model never guesses.
        """
        extractor = history if isinstance(history, CFBV6FeatureExtractor) else self.extractor_for(history)
        feat = extractor.extract_features(
            event_id=game.event_id,
            game_start_utc=game.event_start_utc,
            home_team=game.home_team,
            away_team=game.away_team,
            is_neutral_site=getattr(game, "is_neutral_site", False),
            wind_mph=getattr(game, "wind_mph", None),
            temperature_f=getattr(game, "temperature_f", None),
            precipitation_in=getattr(game, "precipitation_in", None),
            qb_starter_prob_home=getattr(game, "qb_starter_prob_home", None),
            qb_starter_prob_away=getattr(game, "qb_starter_prob_away", None),
        )
        supplied_spread = getattr(game, "spread_home_line", None)
        if supplied_spread is None and getattr(game, "spread_away_line", None) is not None:
            supplied_spread = -float(game.spread_away_line)
        tot_line = getattr(game, "total_line", None)
        if supplied_spread is None or tot_line is None:
            if not project_missing_lines:
                return []
            margin, total = self.calibrate_means(feat.projected_margin_home, feat.projected_total)
            if supplied_spread is None:
                supplied_spread = round(-margin * 2.0) / 2.0
            if tot_line is None:
                tot_line = total
        spread_home_line = float(supplied_spread)
        sp_away_line = -spread_home_line
        tot_line = float(tot_line)

        fc = self.forecast_game(feat, spread_home_line=spread_home_line, total_line=tot_line)
        basis = {
            **feat.to_dict(),
            "effective_margin_sd": fc.effective_margin_sd,
            "effective_total_sd": fc.effective_total_sd,
        }
        common: dict[str, Any] = {
            "event_id": game.event_id,
            "event_start_utc": game.event_start_utc,
            "league": "NCAAF",
            "away_team": game.away_team,
            "home_team": game.home_team,
            # v6 has no scalar uncertainty; the fitted SDs ride in feature_basis.
            "uncertainty": 0.0,
            "feature_basis": basis,
            "inference_inputs": fc.inference_inputs,
        }
        return [
            GamePrediction(
                **common,
                market_type="moneyline",
                line=None,
                probabilities={"home": round(fc.prob_home_win, 6), "away": round(fc.prob_away_win, 6)},
                model_version=self.version,
                rationale=(
                    f"CFB Structural v6: proj {game.away_team} {fc.away_expected_points:.1f} @ "
                    f"{game.home_team} {fc.home_expected_points:.1f} (Margin {fc.projected_margin_home:+.1f}). "
                    f"Total {fc.projected_total:.1f}. Eff margin SD: {fc.effective_margin_sd:.1f}."
                ),
            ),
            GamePrediction(
                **common,
                market_type="spread",
                line=sp_away_line,
                probabilities={
                    "away": round(fc.prob_away_cover, 6),
                    "home": round(fc.prob_home_cover, 6),
                    "push": round(fc.prob_push_spread, 6),
                },
                model_version=self.spread_version,
                rationale=(
                    f"CFB Structural v6 Spread: Home {spread_home_line:+.1f} / Away {sp_away_line:+.1f} "
                    f"(P(Home cover)={fc.prob_home_cover:.4f}, P(Away cover)={fc.prob_away_cover:.4f}, "
                    f"P(Push)={fc.prob_push_spread:.4f}). Fitted margin SD: {fc.effective_margin_sd:.1f}."
                ),
            ),
            GamePrediction(
                **common,
                market_type="total",
                line=tot_line,
                probabilities={
                    "over": round(fc.prob_over, 6),
                    "under": round(fc.prob_under, 6),
                    "push": round(fc.prob_push_total, 6),
                },
                model_version=self.total_version,
                rationale=(
                    f"CFB Structural v6 Total: line {tot_line:.1f} "
                    f"(P(Over)={fc.prob_over:.4f}, P(Under)={fc.prob_under:.4f}, "
                    f"P(Push)={fc.prob_push_total:.4f}). Fitted total SD: {fc.effective_total_sd:.1f}."
                ),
            ),
        ]


def cfb_structural_v6_model() -> CFBStructuralV6Model:
    return CFBStructuralV6Model.from_artifact(load_cfb_structural_v6_artifact())
