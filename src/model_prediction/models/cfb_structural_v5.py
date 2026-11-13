"""College Football Structural v5 challenger (cfb-structural-v5).

v4's score projection with three fitted layers in place of hand-set constants, each
chosen on the walk-forward train split of the 2026-11-12 replay evidence
(`outputs/ncaaf/cfb_v4_replay.md`, `scripts/cfb_v5_research.py`):

1. Margin / total mean calibration: ``m' = a + b * m`` and ``t' = c + d * t`` on v4's
   projected home margin and total. The replay's reliability slope of 1.20 and the
   +0.8 (2025-26: +2.0) home residual said v4's projected margins are compressed and
   its home advantage understated.
2. Margin SD from realized residuals per matchup slice (established FBS-vs-FBS,
   thin in-season sample, FBS-vs-FCS) with a fitted conference scale, instead of one
   constant widened by ``uncertainty``: realized residual SD was 16.8 against a served
   14.8 on established matchups and did not vary with ``uncertainty`` except through
   the FCS / thin-sample slices.
3. Total SD as the realized residual SD with no uncertainty widening (realized 16.9 vs
   served 18.4). The deviation-from-line widening and the blowout-line margin widening
   are carried over from v4 unchanged: no historical lines exist to evaluate them.

Challenger only: not registered as a champion; promotion is a separate governance step.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..features.cfb_features import CFBMatchupFeatures
from ..production_registry import compute_artifact_hash
from .cfb_distribution import CFBDistributionType, CFBJointDistributionEngine
from .cfb_structural_v4 import (
    CFB_BLOWOUT_MARGIN_SCALE,
    CFB_BLOWOUT_SPREAD_THRESHOLD,
    CFB_TOTAL_DEVIATION_SCALE,
    CFB_TOTAL_DEVIATION_THRESHOLD,
    CFB_V4_N_SIMULATIONS,
    CFBStructuralForecastV4,
    CFBStructuralV4Model,
    main_gates_from_artifact,
)

CFB_V5_MODEL_VERSION = "cfb-structural-v5"
CFB_V5_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V5_MODEL_VERSION}.json"
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
        "base_model",
    }
)
_PARAMETER_KEYS = frozenset(
    {
        "distribution",
        "home_advantage_points",
        "margin_calibration",
        "total_calibration",
        "margin_sd_table",
        "established_min_sample",
        "conference_margin_scale",
        "total_sd",
        "features",
        "gates",
    }
)
MARGIN_SD_SLICES = ("established", "thin_sample", "fbs_vs_fcs")


def load_cfb_structural_v5_artifact(path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(path) if path is not None else CFB_V5_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V5_MODEL_VERSION:
        raise ValueError(f"cfb v5 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v5 artifact_hash mismatch for {artifact_path}")
    return payload


class CFBStructuralV5Model(CFBStructuralV4Model):
    """v4 projection + fitted mean calibration, slice SD table and residual total SD."""

    version: str = CFB_V5_MODEL_VERSION

    def __init__(
        self,
        *,
        margin_calibration: Mapping[str, float],
        total_calibration: Mapping[str, float],
        margin_sd_table: Mapping[str, float],
        established_min_sample: int,
        conference_margin_scale: float,
        total_sd: float,
        home_advantage_points: float = 2.5,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
        n_simulations: int = CFB_V4_N_SIMULATIONS,
        random_seed: int = 42,
    ) -> None:
        missing = [k for k in MARGIN_SD_SLICES if k not in margin_sd_table]
        if missing:
            raise ValueError(f"margin_sd_table missing slices: {missing}")
        for name, cal in (
            ("margin_calibration", margin_calibration),
            ("total_calibration", total_calibration),
        ):
            if "intercept" not in cal or "slope" not in cal or float(cal["slope"]) <= 0:
                raise ValueError(f"{name} needs a positive slope and an intercept")
        if not 0.5 <= float(conference_margin_scale) <= 1.5:
            raise ValueError("conference_margin_scale outside a sane range")
        super().__init__(
            home_advantage_points=home_advantage_points,
            margin_sd=float(margin_sd_table["established"]),
            total_sd=float(total_sd),
            distribution_type=distribution_type,
            n_simulations=n_simulations,
            random_seed=random_seed,
        )
        self.margin_calibration = {
            "intercept": float(margin_calibration["intercept"]),
            "slope": float(margin_calibration["slope"]),
        }
        self.total_calibration = {
            "intercept": float(total_calibration["intercept"]),
            "slope": float(total_calibration["slope"]),
        }
        self.margin_sd_table = {k: float(margin_sd_table[k]) for k in MARGIN_SD_SLICES}
        self.established_min_sample = int(established_min_sample)
        self.conference_margin_scale = float(conference_margin_scale)

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any], **overrides: Any) -> CFBStructuralV5Model:
        declared = tuple(str(name) for name in artifact["features"])
        produced = {field.name for field in fields(CFBMatchupFeatures)}
        unknown = sorted(set(declared) - produced)
        if unknown:
            raise ValueError(f"cfb v5 artifact declares features the extractor never produces: {unknown}")
        unread = set(artifact) - _METADATA_KEYS - _PARAMETER_KEYS
        if unread:
            raise ValueError(
                f"cfb v5 artifact carries parameters the served model never reads: {sorted(unread)}"
            )
        main_gates_from_artifact(artifact)
        params: dict[str, Any] = {
            "home_advantage_points": float(artifact["home_advantage_points"]),
            "distribution_type": CFBDistributionType(str(artifact["distribution"])),
            "margin_calibration": artifact["margin_calibration"],
            "total_calibration": artifact["total_calibration"],
            "margin_sd_table": artifact["margin_sd_table"],
            "established_min_sample": int(artifact["established_min_sample"]),
            "conference_margin_scale": float(artifact["conference_margin_scale"]),
            "total_sd": float(artifact["total_sd"]),
        }
        params.update(overrides)
        return cls(**params)

    # ------------------------------------------------------------------ fitted layers
    def calibrate_means(self, projected_margin_home: float, projected_total: float) -> tuple[float, float]:
        margin = (
            self.margin_calibration["intercept"] + self.margin_calibration["slope"] * projected_margin_home
        )
        total = self.total_calibration["intercept"] + self.total_calibration["slope"] * projected_total
        return margin, max(24.0, total)

    def margin_slice(self, *, is_fbs_vs_fcs: bool, sample_games: int) -> str:
        if is_fbs_vs_fcs:
            return "fbs_vs_fcs"
        return "established" if sample_games >= self.established_min_sample else "thin_sample"

    def compute_margin_sd(
        self,
        *,
        spread_home_line: float,
        is_fbs_vs_fcs: bool,
        sample_games: int,
        is_conference_matchup: bool,
    ) -> float:
        slice_name = self.margin_slice(is_fbs_vs_fcs=is_fbs_vs_fcs, sample_games=sample_games)
        sd = self.margin_sd_table[slice_name]
        # The conference scale is fitted on established matchups only; applying it to thin
        # or FCS slices would be extrapolation the harness never evaluated.
        if is_conference_matchup and slice_name == "established":
            sd *= self.conference_margin_scale
        abs_spread = abs(spread_home_line)
        if abs_spread > CFB_BLOWOUT_SPREAD_THRESHOLD:
            sd *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * min(15.0, abs_spread - CFB_BLOWOUT_SPREAD_THRESHOLD)
        return round(sd, 3)

    def compute_total_sd(self, *, projected_total: float, total_line: float) -> float:
        sd = self.total_sd
        deviation = abs(projected_total - total_line)
        if deviation > CFB_TOTAL_DEVIATION_THRESHOLD:
            sd *= 1.0 + CFB_TOTAL_DEVIATION_SCALE * min(20.0, deviation - CFB_TOTAL_DEVIATION_THRESHOLD)
        return round(sd, 3)

    # v4's uncertainty-driven helpers are not part of v5's arithmetic; route callers that
    # still use the v4 names to the fitted versions so no code path prices with v4 SDs.
    def compute_effective_margin_sd(  # type: ignore[override]
        self,
        spread_home_line: float,
        uncertainty: float = 0.04,
        is_conference_matchup: bool = False,
        *,
        is_fbs_vs_fcs: bool = False,
        sample_games: int | None = None,
    ) -> float:
        return self.compute_margin_sd(
            spread_home_line=spread_home_line,
            is_fbs_vs_fcs=is_fbs_vs_fcs,
            sample_games=self.established_min_sample if sample_games is None else sample_games,
            is_conference_matchup=is_conference_matchup,
        )

    def compute_effective_total_sd(  # type: ignore[override]
        self, projected_total: float, total_line: float, uncertainty: float = 0.04
    ) -> float:
        return self.compute_total_sd(projected_total=projected_total, total_line=total_line)

    def margin_sd_for_features(self, features: Mapping[str, Any], spread_home_line: float) -> float:
        return self.compute_margin_sd(
            spread_home_line=spread_home_line,
            is_fbs_vs_fcs=bool(features.get("is_fbs_vs_fcs", False)),
            sample_games=int(features.get("sample_games", self.established_min_sample)),
            is_conference_matchup=bool(features.get("is_conference_matchup", False)),
        )

    def total_sd_for_features(
        self, features: Mapping[str, Any], projected_total: float, total_line: float
    ) -> float:
        return self.compute_total_sd(projected_total=projected_total, total_line=total_line)

    def forecast_game(
        self,
        matchup_features: CFBMatchupFeatures,
        spread_home_line: float,
        total_line: float,
    ) -> CFBStructuralForecastV4:
        from ..cfb_replay import capture_inputs

        margin, total = self.calibrate_means(
            matchup_features.projected_margin_home, matchup_features.projected_total
        )
        mu_home = max(3.0, (total + margin) / 2.0)
        mu_away = max(3.0, (total - margin) / 2.0)
        effective_margin_sd = self.compute_margin_sd(
            spread_home_line=spread_home_line,
            is_fbs_vs_fcs=matchup_features.is_fbs_vs_fcs,
            sample_games=matchup_features.sample_games,
            is_conference_matchup=matchup_features.is_conference_matchup,
        )
        effective_total_sd = self.compute_total_sd(projected_total=mu_home + mu_away, total_line=total_line)
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


def cfb_structural_v5_model() -> CFBStructuralV5Model:
    return CFBStructuralV5Model.from_artifact(load_cfb_structural_v5_artifact())
