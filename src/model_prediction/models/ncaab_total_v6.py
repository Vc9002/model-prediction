"""Dedicated NCAAB Over/Under Total Points Model v6.

Advances ncaab-total-v5 with:
1. Division-1 Conference-Tiered Scoring Baselines:
   Replaces the uniform 71.4 defect with empirical conference scoring baselines
   (SEC: 83.4 down to Northeast: 69.0 pts/team), reflecting the 28.8-pt empirical
   spread between up-tempo power conferences and slow-tempo mid-major grinders.
2. Bayesian Sample Credibility Shrinkage (Bühlmann Credibility):
   Continuously contracts expected game totals toward market consensus line on thin
   samples (sample < 5) via Z = sample / (sample + K), eliminating the need for
   arbitrary binary divergence gating.
3. Venue-Aware Tempo Control:
   Empirically weights home court team tempo at 60% controlling share on home court
   (50/50 on neutral venues), preventing fast road teams from falsely inflating
   possession projections against methodical home defenses.
4. Heteroskedastic Pace & Credibility-Scaled Volatility:
   Adjusts standard deviation proportionally to sqrt(matchup_pace) and sample thinness.

2026-11-12 review fixes (docs/MODEL_REVIEW_2026-11-11.md #7, #9, LOW push mass):
- ``conference_trend_priors``: the trend engine shrinks each team's scoring level toward its
  conference baseline, the same reference the pace factor divides by. Before, ``hl10`` was
  shrunk toward the league mean and then compared to the conference mean, so every team in a
  high-scoring conference looked slow and every team in a low-scoring one looked fast (#7).
- ``pace_baseline``: which reference the pace factor uses ("conference" or "league").
- ``league_total_weight``: explicit weight on the conference-baseline total (was derived as
  ``1 - trend - team`` and therefore silently zero).
- Integer total lines exclude the push mass from both over and under.
- ``from_artifact`` / ``ncaab_total_v6_model`` load ``config/models/ncaab-total-v6.json``
  (hash-verified) so the artifact and the served parameters cannot drift (#9).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from math import erf, isnan, sqrt
from pathlib import Path
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame
from .ncaab_spread_v6 import load_verified_artifact

NCAAB_TOTAL_V6_VERSION = "ncaab-total-v6"
ARTIFACT_PATH = Path(__file__).resolve().parents[3] / "config" / "models" / "ncaab-total-v6.json"

DEFAULT_BASELINE_POINTS: float = 76.06
DEFAULT_LEAGUE_PACE: float = 68.1

DEFAULT_CONFERENCE_SCORING_BASELINES: dict[str, float] = {
    # High-tempo / High-scoring Tier
    "Southeastern Conference": 83.41,
    "Big 12 Conference": 79.42,
    "Mid-American Conference": 78.82,
    "Atlantic Coast Conference": 78.52,
    "Horizon League": 78.34,
    "Big South Conference": 78.26,
    "Atlantic Sun Conference": 78.18,
    "Pac-12 Conference": 77.76,
    "Big Sky Conference": 77.71,
    "Big West Conference": 77.68,
    "Big Ten Conference": 77.61,
    "Conference USA": 77.50,
    "Southern Conference": 77.02,
    "Big East Conference": 76.87,
    "American Conference": 76.66,
    "West Coast Conference": 76.63,
    "Summit League": 76.56,
    # Mid-scoring Tier
    "Ivy League": 75.95,
    "Mountain West Conference": 75.77,
    "Missouri Valley Conference": 75.38,
    "Atlantic 10 Conference": 75.30,
    "United Athletic Conference": 75.26,
    "Sun Belt Conference": 74.85,
    "Coastal Athletic Association": 74.63,
    # Low-tempo / Grinding Defensive Tier
    "Ohio Valley Conference": 72.87,
    "Southwestern Athletic Conference": 72.70,
    "Southland Conference": 72.58,
    "Patriot League": 72.40,
    "Mid-Eastern Athletic Conference": 70.27,
    "America East Conference": 69.77,
    "Metro Conference": 69.76,
    "Northeast Conference": 69.01,
}


def _load_team_conference_map(
    conference_file: Path | str | None = None,
) -> dict[str, str]:
    """Map team display names to conference names."""
    path = (
        Path(conference_file)
        if conference_file
        else Path(__file__).resolve().parents[3] / "data/entities/ncaab_conferences.json"
    )
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _effective_current_season_games(team_trend, team_prev, game_start_dt, offseason_gap_days):
    """Detect true season openers where last historical game exceeds offseason_gap_days."""
    if game_start_dt is not None and team_prev:
        last_gap_days = (game_start_dt - team_prev[-1].start).total_seconds() / 86400.0
        if last_gap_days > offseason_gap_days:
            return 0
    return team_trend.current_season_games_played


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NCAABTotalModelV6:
    """Independent NCAAB Over/Under Total Points v6 prediction engine."""

    def __init__(
        self,
        total_sd: float = 13.5,
        baseline_points_per_team: float = DEFAULT_BASELINE_POINTS,
        league_pace: float = DEFAULT_LEAGUE_PACE,
        trend_total_weight: float = 0.85,
        team_total_weight: float = 0.15,
        league_total_weight: float = 0.0,
        dynamic_matchup_pace: bool = True,
        pace_drag: float = 0.5,
        schedule_density_drag: bool = True,
        overtime_risk_adjustment: bool = True,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.20,
        isolate_postseason: bool = True,
        home_tempo_control: float = 0.60,
        use_bayesian_credibility: bool = True,
        credibility_constant: float = 4.0,
        conference_trend_priors: bool = False,
        pace_baseline: str = "conference",
        max_edge: float = 0.08,
        base_units: float = 1.0,
        max_units: float = 2.0,
        dynamic_sizing_enabled: bool = True,
        conference_file: Path | str | None = None,
        conference_baselines: dict[str, float] | None = None,
        version: str = NCAAB_TOTAL_V6_VERSION,
    ) -> None:
        if pace_baseline not in {"conference", "league"}:
            raise ValueError(f"pace_baseline must be 'conference' or 'league', got {pace_baseline!r}")
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.league_total_weight = league_total_weight
        self.dynamic_matchup_pace = dynamic_matchup_pace
        self.pace_drag = pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.home_tempo_control = home_tempo_control
        self.use_bayesian_credibility = use_bayesian_credibility
        self.credibility_constant = credibility_constant
        self.conference_trend_priors = conference_trend_priors
        self.pace_baseline = pace_baseline
        self.max_edge = max_edge
        self.base_units = base_units
        self.max_units = max_units
        self.dynamic_sizing_enabled = dynamic_sizing_enabled
        self.conference_baselines = conference_baselines or DEFAULT_CONFERENCE_SCORING_BASELINES
        self.team_conferences = _load_team_conference_map(conference_file)
        self.version = version
        self.league = "NCAAB"
        self.sport = "ncaab"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any], **overrides: Any) -> NCAABTotalModelV6:
        """Construct the served model from its artifact; every model parameter is read here.

        tests/test_ncaab_v6.py asserts that no artifact key goes unread.
        """
        sizing = dict(artifact.get("dynamic_sizing") or {})
        params: dict[str, Any] = {
            "total_sd": float(artifact.get("total_sd", 13.5)),
            "baseline_points_per_team": float(
                artifact.get("baseline_points_per_team", DEFAULT_BASELINE_POINTS)
            ),
            "league_pace": float(artifact.get("league_pace", DEFAULT_LEAGUE_PACE)),
            "trend_total_weight": float(artifact.get("trend_total_weight", 0.85)),
            "team_total_weight": float(artifact.get("team_total_weight", 0.15)),
            "league_total_weight": float(artifact.get("league_total_weight", 0.0)),
            "dynamic_matchup_pace": bool(artifact.get("dynamic_matchup_pace", True)),
            "pace_drag": float(artifact.get("pace_drag", 0.5)),
            "schedule_density_drag": bool(artifact.get("schedule_density_drag", True)),
            "overtime_risk_adjustment": bool(artifact.get("overtime_risk_adjustment", True)),
            "offseason_gap_days": float(artifact.get("offseason_gap_days", 150.0)),
            "offseason_retention": float(artifact.get("offseason_retention", 0.20)),
            "isolate_postseason": bool(artifact.get("isolate_postseason", True)),
            "home_tempo_control": float(artifact.get("home_tempo_control", 0.60)),
            "use_bayesian_credibility": bool(artifact.get("use_bayesian_credibility", True)),
            "credibility_constant": float(artifact.get("credibility_constant", 4.0)),
            "conference_trend_priors": bool(artifact.get("conference_trend_priors", False)),
            "pace_baseline": str(artifact.get("pace_baseline", "conference")),
            "max_edge": float(artifact.get("max_edge", 0.08)),
            "base_units": float(sizing.get("base_units", 1.0)),
            "max_units": float(sizing.get("max_units", 2.0)),
            "dynamic_sizing_enabled": bool(sizing.get("enabled", True)),
            "version": str(artifact.get("model_version", NCAAB_TOTAL_V6_VERSION)),
        }
        params.update(overrides)
        return cls(**params)

    def _trend_baselines(self, history: Sequence[GameRecord]) -> dict[str, float] | None:
        """Conference scoring baseline per team (shrinkage target for offense and defense)."""
        if not self.conference_trend_priors:
            return None
        teams = {name for game in history for name in (game.home_team, game.away_team)}
        out: dict[str, float] = {}
        for team in teams | set(self.team_conferences):
            conf = self.team_conferences.get(team)
            prior = self.conference_baselines.get(conf) if conf is not None else None
            if prior is not None:
                out[team] = float(prior)
        return out

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        team_baselines = self._trend_baselines(history)
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
            offense_baselines=team_baselines,
            defense_baselines=team_baselines,
        )
        league_baseline = trend.league_baseline or self.baseline_points_per_team

        predictions: list[GamePrediction] = []

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)

            game_start_dt = None
            try:
                game_start_dt = parse_utc(game.event_start_utc)
            except (ValueError, TypeError):
                pass

            home_prev = [g for g in history if g.home_team == game.home_team or g.away_team == game.home_team]
            away_prev = [g for g in history if g.home_team == game.away_team or g.away_team == game.away_team]
            if game_start_dt is not None:
                home_prev = [g for g in home_prev if g.start < game_start_dt]
                away_prev = [g for g in away_prev if g.start < game_start_dt]
            home_prev.sort(key=lambda g: g.start)
            away_prev.sort(key=lambda g: g.start)

            away_sample = _effective_current_season_games(
                away_trend, away_prev, game_start_dt, self.offseason_gap_days
            )
            home_sample = _effective_current_season_games(
                home_trend, home_prev, game_start_dt, self.offseason_gap_days
            )
            sample = min(away_sample, home_sample)

            away_conf = self.team_conferences.get(game.away_team)
            home_conf = self.team_conferences.get(game.home_team)
            away_base = self.conference_baselines.get(away_conf, self.baseline_points_per_team)
            home_base = self.conference_baselines.get(home_conf, self.baseline_points_per_team)
            baseline = (away_base + home_base) / 2.0

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2.0 or away_base
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2.0 or home_base

            home_rest_days = None
            away_rest_days = None
            if game_start_dt is not None:
                if home_prev:
                    home_rest_days = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                if away_prev:
                    away_rest_days = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0

            home_short_rest = home_rest_days is not None and home_rest_days < 1.75
            away_short_rest = away_rest_days is not None and away_rest_days < 1.75
            if home_short_rest:
                home_points -= 0.8
            if away_short_rest:
                away_points -= 1.0

            home_3in5 = False
            away_3in5 = False
            if self.schedule_density_drag and game_start_dt is not None:
                home_3in5 = (
                    len([g for g in home_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                away_3in5 = (
                    len([g for g in away_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                if home_3in5 and not home_short_rest:
                    home_points -= 0.5
                if away_3in5 and not away_short_rest:
                    away_points -= 0.6

            raw_trend_total = away_points + home_points

            # Pace adjustment: scale by expected matchup possessions
            pace_adj = 0.0
            if (home_short_rest or away_short_rest) and self.pace_drag > 0.0:
                pace_adj -= self.pace_drag
            if (home_3in5 or away_3in5) and self.schedule_density_drag:
                pace_adj -= 0.4

            expected_pace = self.league_pace
            is_neutral = bool(getattr(game, "neutral_site", False))
            # The pace factor compares each team's shrunk scoring level with a reference. That
            # reference must be the same one the trend engine shrinks toward, or a team with no
            # information looks fast/slow purely because of its conference label (#7).
            pace_ref_away = away_base if self.pace_baseline == "conference" else league_baseline
            pace_ref_home = home_base if self.pace_baseline == "conference" else league_baseline
            if self.dynamic_matchup_pace and baseline > 0.0:
                pf_away = 1.0 + 0.55 * (
                    ((away_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2.0 - pace_ref_away)
                    / pace_ref_away
                )
                pf_home = 1.0 + 0.55 * (
                    ((home_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2.0 - pace_ref_home)
                    / pace_ref_home
                )
                home_w = 0.50 if is_neutral else self.home_tempo_control
                away_w = 0.50 if is_neutral else (1.0 - self.home_tempo_control)
                expected_pace = max(
                    56.0,
                    min(
                        78.0,
                        self.league_pace * (1.0 + (away_w * (pf_away - 1.0) + home_w * (pf_home - 1.0)))
                        + pace_adj,
                    ),
                )
                pace_multiplier = expected_pace / self.league_pace
                raw_trend_total = raw_trend_total * pace_multiplier

            # Composite total with conference baseline shrinkage
            team_avg_total = (
                away_trend.offense["hl10"]
                + away_trend.defense["hl10"]
                + home_trend.offense["hl10"]
                + home_trend.defense["hl10"]
            ) / 2.0

            w_trend = self.trend_total_weight
            w_team = self.team_total_weight
            if self.league_total_weight > 0.0:
                w_league = self.league_total_weight
                norm = w_trend + w_team + w_league
                w_trend, w_team, w_league = w_trend / norm, w_team / norm, w_league / norm
            else:
                w_league = max(0.0, 1.0 - (w_trend + w_team))
            expected_total = w_trend * raw_trend_total + w_team * team_avg_total + w_league * (baseline * 2.0)

            expected_total -= getattr(game, "availability_total_loss", 0.0)

            # Overtime probability bump (spread closeness adjustment)
            expected_margin = home_points - away_points
            if self.overtime_risk_adjustment:
                if abs(expected_margin) <= 3.5:
                    expected_total += 1.2
                elif abs(expected_margin) <= 6.5:
                    expected_total += 0.6

            # Bayesian Sample Credibility Shrinkage (Bühlmann Credibility):
            # Smoothly contracts the model's projected total toward the market line on thin samples
            # Eliminates reliance on arbitrary post-hoc binary total divergence gates
            raw_model_total = expected_total
            target_line = game.total_line if game.total_line is not None else expected_total
            if self.use_bayesian_credibility and game.total_line is not None:
                cred_k = self.credibility_constant
                z = sample / (sample + cred_k)
                expected_total = z * raw_model_total + (1.0 - z) * float(game.total_line)
            else:
                z = 1.0

            # Pace-scaled dynamic total standard deviation with credibility widening
            credibility_drag = (
                self.credibility_constant / (sample + self.credibility_constant)
                if self.use_bayesian_credibility
                else 0.0
            )
            effective_total_sd = (
                self.total_sd * sqrt(expected_pace / self.league_pace) * (1.0 + 0.40 * credibility_drag)
            )

            uncertainty = max(0.03, min(0.20, 0.20 - 0.02 * sample))
            # Totals are integers: on an integer line the exact-total mass is a push and belongs
            # to neither side.
            if game.total_line is not None and float(game.total_line).is_integer():
                over_prob = 1.0 - _normal_cdf(float(target_line) + 0.5, expected_total, effective_total_sd)
                under_prob = _normal_cdf(float(target_line) - 0.5, expected_total, effective_total_sd)
            else:
                over_prob = 1.0 - _normal_cdf(target_line, expected_total, effective_total_sd)
                under_prob = 1.0 - over_prob
            push_prob = max(0.0, 1.0 - over_prob - under_prob)

            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "away_conference": away_conf,
                    "home_conference": home_conf,
                    "expected_pace": round(expected_pace, 1),
                    "neutral_site": is_neutral,
                    "league_pace": self.league_pace,
                    "pace_baseline": self.pace_baseline,
                    "league_baseline": round(league_baseline, 2),
                    "raw_model_total": round(raw_model_total, 2),
                    "expected_total": round(expected_total, 2),
                    "credibility_factor": round(z, 3),
                    "effective_total_sd": round(effective_total_sd, 2),
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            predictions.append(
                GamePrediction(
                    market_type="total",
                    line=game.total_line,
                    inference_inputs={
                        "mean": round(expected_total, 2),
                        "sd": round(effective_total_sd, 2),
                        "line": game.total_line,
                        "raw_model_mean": round(raw_model_total, 2),
                        "credibility_z": round(z, 3),
                    },
                    probabilities={
                        "over": round(max(0.0001, min(0.9999, over_prob)), 6),
                        "under": round(max(0.0001, min(0.9999, under_prob)), 6),
                        "push": round(push_prob, 6),
                    },
                    rationale=(
                        f"Projected total {expected_total:.1f} (raw {raw_model_total:.1f}, "
                        f"z={z:.2f}, pace {expected_pace:.1f}, sd {effective_total_sd:.1f})."
                    ),
                    **base,
                )
            )

        return predictions

    # Alias predict_games to predict_totals for polymarket slate runner
    predict_games = predict_totals

    def compute_dynamic_units(
        self, edge: float, min_edge: float = 0.02, max_edge: float | None = None
    ) -> float:
        """Unit sizing scaling linearly with edge from ``base_units`` at ``min_edge`` to
        ``max_units`` at ``max_edge`` (the artifact's values unless overridden)."""
        if not self.dynamic_sizing_enabled:
            return round(self.base_units, 2)
        top = self.max_edge if max_edge is None else max_edge
        if edge <= min_edge or top <= min_edge:
            return round(self.base_units, 2)
        scaled = self.base_units + (edge - min_edge) * (self.max_units - self.base_units) / (top - min_edge)
        return round(min(self.max_units, max(self.base_units, scaled)), 2)


def ncaab_total_v6_model() -> NCAABTotalModelV6:
    """Production factory: the served parameters are the hash-verified artifact's, nothing else."""
    return NCAABTotalModelV6.from_artifact(load_verified_artifact(ARTIFACT_PATH))
