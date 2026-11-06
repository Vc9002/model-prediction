"""Dedicated NCAAB Over/Under Total Points Model v4.

Mirrors nba_total_v6.py's shape, with the same current-season sample fix
as ncaab_spread_v4.py and NCAAB-tuned rest/density thresholds (NCAAB rarely
plays true back-to-backs; "short rest" and schedule-density windows use
different day thresholds than NBA's B2B-tuned numbers).
"""

from __future__ import annotations

from collections.abc import Sequence
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NCAAB_TOTAL_V4_VERSION = "ncaab-total-v4"


def _effective_current_season_games(team_trend, team_prev, game_start_dt, offseason_gap_days):
    """A team's prior-season game count still shows up in
    TeamTrend.current_season_games_played because that field only sees gaps
    WITHIN the history list -- it can't see the gap between the team's last
    historical game and the upcoming game being predicted now. A true season
    opener (last game more than offseason_gap_days ago) must report 0, not
    whatever stale lifetime count current_season_games_played returns."""
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


class NCAABTotalModelV4:
    """Independent NCAAB Over/Under Total Points prediction engine."""

    def __init__(
        self,
        total_sd: float = 14.0,
        baseline_points_per_team: float = 71.4,
        league_pace: float = 68.1,
        trend_total_weight: float = 0.85,
        team_total_weight: float = 0.15,
        dynamic_matchup_pace: bool = True,
        pace_drag: float = 0.6,
        schedule_density_drag: bool = True,
        overtime_risk_adjustment: bool = True,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.25,
        isolate_postseason: bool = True,
        version: str = NCAAB_TOTAL_V4_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.dynamic_matchup_pace = dynamic_matchup_pace
        self.pace_drag = pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.version = version
        self.sport = "ncaab"
        self.league = "NCAAB"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABTotalModelV4:
        return cls(
            total_sd=float(artifact.get("total_sd", 14.0)),
            baseline_points_per_team=float(artifact.get("baseline_points_per_team", 71.4)),
            league_pace=float(artifact.get("league_pace", 68.1)),
            trend_total_weight=float(artifact.get("trend_total_weight", 0.85)),
            team_total_weight=float(artifact.get("team_total_weight", 0.15)),
            dynamic_matchup_pace=bool(artifact.get("dynamic_matchup_pace", True)),
            pace_drag=float(artifact.get("pace_drag", 0.6)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            overtime_risk_adjustment=bool(artifact.get("overtime_risk_adjustment", True)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 150.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.25)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            version=str(artifact.get("model_version", NCAAB_TOTAL_V4_VERSION)),
        )

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or self.baseline_points_per_team

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

            # Fix: current-season sample, not lifetime (see ncaab_spread_v4.py),
            # including the true-season-opener override that
            # current_season_games_played alone can't detect.
            away_sample = _effective_current_season_games(
                away_trend, away_prev, game_start_dt, self.offseason_gap_days
            )
            home_sample = _effective_current_season_games(
                home_trend, home_prev, game_start_dt, self.offseason_gap_days
            )
            sample = min(away_sample, home_sample)

            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline

            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            home_short_rest = False
            away_short_rest = False
            try:
                home_rest = (
                    (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and home_prev)
                    else 3.0
                )
                away_rest = (
                    (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and away_prev)
                    else 3.0
                )
                home_short_rest = home_rest <= 2.0
                away_short_rest = away_rest <= 2.0
            except (ValueError, TypeError):
                pass

            home_dense = False
            away_dense = False
            if self.schedule_density_drag and game_start_dt is not None:
                h_3d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                a_3d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                home_dense = len(h_3d) >= 2
                away_dense = len(a_3d) >= 2

            est_possessions = self.league_pace
            if self.dynamic_matchup_pace:
                pf_away = (
                    1.0 + 0.50 * (((away_off + away_def) / (2.0 * baseline)) - 1.0)
                    if away_trend.games_played > 0
                    else 1.0
                )
                pf_home = (
                    1.0 + 0.50 * (((home_off + home_def) / (2.0 * baseline)) - 1.0)
                    if home_trend.games_played > 0
                    else 1.0
                )
                drag = -self.pace_drag if (home_short_rest or away_short_rest) else 0.0
                est_possessions = max(55.0, (self.league_pace * pf_away * pf_home) + drag)

            expected_total = (away_points + home_points) * (est_possessions / self.league_pace)

            if (home_dense or away_dense) and self.schedule_density_drag:
                expected_total -= 0.50
            if home_short_rest and away_short_rest:
                expected_total -= 0.80

            margin_proxy = abs(home_points - away_points)
            if self.overtime_risk_adjustment and margin_proxy <= 3.0:
                expected_total += 1.00

            # Dynamic volatility: widen the ACTUAL total_sd on thin-sample
            # games, not just the reported uncertainty metadata (same gap
            # fixed in ncaab_spread_v4.py).
            sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
            effective_total_sd = max(
                self.total_sd, min(self.total_sd * 1.6, self.total_sd * (1.0 + 0.5 * sample_factor))
            )
            effective_total_sd = max(
                10.0, min(22.0, effective_total_sd * sqrt(est_possessions / self.league_pace))
            )

            uncertainty = max(0.03, min(0.20, 0.20 - 0.025 * sample))

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
                    "est_possessions": round(est_possessions, 1),
                    "expected_total": round(expected_total, 2),
                    "effective_total_sd": round(effective_total_sd, 2),
                    "home_short_rest": home_short_rest,
                    "away_short_rest": away_short_rest,
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            if game.total_line is not None:
                under_prob = _normal_cdf(game.total_line, expected_total, effective_total_sd)
                over_prob = 1.0 - under_prob
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        inference_inputs={
                            "mean": expected_total,
                            "sd": effective_total_sd,
                            "line": game.total_line,
                        },
                        line=game.total_line,
                        probabilities={
                            "over": round(over_prob, 6),
                            "under": round(under_prob, 6),
                        },
                        rationale=(
                            f"Projected total {expected_total:.1f} (poss {est_possessions:.1f}), "
                            f"sd {effective_total_sd:.1f}."
                        ),
                        **base,
                    )
                )

        return predictions
