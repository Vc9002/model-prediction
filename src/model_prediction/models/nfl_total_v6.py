"""Dedicated NFL Over/Under Total Points Model v6.

Completely independent from the spread engine. Focuses exclusively on
baseline game total points, drive efficiency, indoor dome inflation (+1.8 pts),
non-linear wind penalties (wind > 14 mph / > 20 mph), short-week TNF fatigue,
and regularized total CDF bounds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

NFL_TOTAL_V6_VERSION = "nfl-total-structural-v6"
NFL_EMPIRICAL_TOTAL_SD = 13.89
NFL_LEAGUE_AVG_TOTAL = 45.8


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if math.isnan(x) or math.isnan(mean) or math.isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))
    return max(0.0001, min(0.9999, raw))


@dataclass(frozen=True)
class NFLTotalForecast:
    home_team: str
    away_team: str
    projected_home_points: float
    projected_away_points: float
    projected_total: float
    prob_over: float
    prob_under: float
    total_line: float
    effective_sd: float
    weather_total_penalty: float
    short_week_penalty: float


class NFLTotalModelV6:
    """Independent NFL Over/Under Total Points prediction engine."""

    def __init__(
        self,
        total_sd: float = NFL_EMPIRICAL_TOTAL_SD,
        league_avg_total: float = NFL_LEAGUE_AVG_TOTAL,
        dome_boost: float = 1.8,
        wind_15_penalty: float = 2.5,
        wind_20_penalty: float = 4.5,
        cold_temp_penalty: float = 1.5,
        short_week_drag: float = 1.0,
        max_total_prob: float = 0.65,
        min_total_prob: float = 0.35,
        version: str = NFL_TOTAL_V6_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.league_avg_total = league_avg_total
        self.dome_boost = dome_boost
        self.wind_15_penalty = wind_15_penalty
        self.wind_20_penalty = wind_20_penalty
        self.cold_temp_penalty = cold_temp_penalty
        self.short_week_drag = short_week_drag
        self.max_total_prob = max_total_prob
        self.min_total_prob = min_total_prob
        self.version = version
        self.sport = "nfl"
        self.league = "NFL"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NFLTotalModelV6:
        return cls(
            total_sd=float(artifact.get("total_sd", NFL_EMPIRICAL_TOTAL_SD)),
            league_avg_total=float(artifact.get("league_avg_total", NFL_LEAGUE_AVG_TOTAL)),
            dome_boost=float(artifact.get("dome_boost", 1.8)),
            wind_15_penalty=float(artifact.get("wind_15_penalty", 2.5)),
            wind_20_penalty=float(artifact.get("wind_20_penalty", 4.5)),
            cold_temp_penalty=float(artifact.get("cold_temp_penalty", 1.5)),
            short_week_drag=float(artifact.get("short_week_drag", 1.0)),
            max_total_prob=float(artifact.get("max_total_prob", 0.65)),
            min_total_prob=float(artifact.get("min_total_prob", 0.35)),
            version=str(artifact.get("model_version", NFL_TOTAL_V6_VERSION)),
        )

    def forecast_total(
        self,
        home_team: str,
        away_team: str,
        home_off_epa: float = 0.00,
        home_def_epa: float = 0.00,
        away_off_epa: float = 0.00,
        away_def_epa: float = 0.00,
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        wind_mph: float = 5.0,
        temperature_f: float = 65.0,
        is_dome: bool = False,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        total_line: float = 45.5,
        is_neutral_site: bool = False,
        league_avg_epa_play: float = 0.00,
    ) -> NFLTotalForecast:
        base_points_per_side = self.league_avg_total / 2.0

        # 1. EPA / Efficiency Points Projection (~32.0 pts per 1.0 EPA/play delta across 65 plays)
        h_eff_epa = (home_off_epa - league_avg_epa_play) + (away_def_epa - league_avg_epa_play)
        a_eff_epa = (away_off_epa - league_avg_epa_play) + (home_def_epa - league_avg_epa_play)
        h_eff_pts = h_eff_epa * 32.0 + home_qb_tier_adj
        a_eff_pts = a_eff_epa * 32.0 + away_qb_tier_adj

        # 2. Situational Short-Week Factor
        short_week_penalty = 0.0
        if home_rest_days <= 5 and away_rest_days <= 5:
            short_week_penalty = self.short_week_drag
        elif home_rest_days <= 5 or away_rest_days <= 5:
            short_week_penalty = self.short_week_drag / 2.0

        # 3. Weather Penalties & Venue Environment
        weather_penalty = 0.0
        if is_dome:
            # Dome boost: lack of wind/weather systematically elevates passing totals
            base_points_per_side += self.dome_boost / 2.0
        else:
            if wind_mph > 20.0:
                weather_penalty += self.wind_20_penalty
            elif wind_mph > 14.0:
                weather_penalty += self.wind_15_penalty
            if temperature_f < 25.0:
                weather_penalty += self.cold_temp_penalty

        pts_home = max(
            6.0, base_points_per_side + h_eff_pts - (weather_penalty / 2.0) - (short_week_penalty / 2.0)
        )
        pts_away = max(
            6.0, base_points_per_side + a_eff_pts - (weather_penalty / 2.0) - (short_week_penalty / 2.0)
        )
        exp_total = round(pts_home + pts_away, 2)

        # 4. Total Over/Under Distribution with Bounded Tail Safeguards
        p_raw_under = _normal_cdf(total_line, exp_total, self.total_sd)
        p_raw_over = 1.0 - p_raw_under

        # Bound probabilities: prevent extreme probability spikes (e.g. 90%) on alternate totals
        p_over = max(self.min_total_prob, min(self.max_total_prob, p_raw_over))
        p_under = 1.0 - p_over

        return NFLTotalForecast(
            home_team=home_team,
            away_team=away_team,
            projected_home_points=round(pts_home, 1),
            projected_away_points=round(pts_away, 1),
            projected_total=exp_total,
            prob_over=round(p_over, 6),
            prob_under=round(p_under, 6),
            total_line=total_line,
            effective_sd=self.total_sd,
            weather_total_penalty=round(weather_penalty, 2),
            short_week_penalty=round(short_week_penalty, 2),
        )
