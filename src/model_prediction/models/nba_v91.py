"""NBA Structural Model v9.1 (research challenger to nba-spread-margin-v9 / nba-total-margin-v9).

A copy of the v9 engine with the model-output fixes from the 2026-11-20 NBA review, each behind
its own artifact switch so it can be ablated on the replay harness
(``scripts/nba_v10/replay_harness.py``). With every switch off it reproduces v9 exactly (pinned
by ``tests/test_nba_v91.py``). v9 itself is protected and is not edited.

- B1 ``current_season_sample``: the overtime-bump sample maturity counts current-season games,
  not lifetime ``games_played`` (which reads 220+ on opening night).
- B5 ``rolling_baseline_games``: the league scoring baseline (TrendEngine shrinkage target and
  opponent-adjustment reference) is the mean of the last N current-season games as of the
  decision date, shrunk toward the previous season's closing level with
  ``baseline_prior_games`` pseudo-games, instead of the all-history mean.
- B6 ``fatigue_mode="fitted"``: back-to-back and 3-in-4 effects are one symmetric per-team
  points model (tired team scores ``*_offense_penalty`` fewer and allows ``*_defense_penalty``
  more), applied at full weight to both the margin and the total, replacing v9's one-sided
  constants (which hit totals fully but margins only through the 0.30 trend weight).
- B7 ``playoff_total_offset``: additive total offset for postseason games (v9 priced playoff
  totals like the regular season and ran 13 points high).
- B4: the market-blend hook uses ``spread_away_line`` as the home-margin line (v9 negated it).
  The weight stays 0 in every research artifact; anchoring lives in ``nba_anchor_v10``.
- ``probability_logit_slope`` (v9.2, quick-261123-h40): the final P(over) is served as
  sigmoid(s * logit(p)), intercept 0. Absent (1.0) on the research artifacts, which price as before.
  ``NBATotalModelV92`` is the serving wrapper for ``nba-total-margin-v9.2``.

Every fitted constant comes from the artifact; the defaults are v9's behaviour.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame
from .nba_v9 import NBAStructuralV9Engine, _normal_cdf

NBA_SPREAD_V91_VERSION = "nba-spread-margin-v9-1"
NBA_TOTAL_V91_VERSION = "nba-total-margin-v9-1"
NBA_MONEYLINE_V91_VERSION = "nba-moneyline-v9-1"
NBA_TOTAL_V92_VERSION = "nba-total-margin-v9.2"

POSTSEASON_TYPES = frozenset({"post-season", "playoffs"})
_ISOLATED_TYPES = frozenset({"post-season", "playoffs", "play-in-season"})


def _logit_shrink(p: float, slope: float) -> float:
    """sigmoid(slope * logit(p)) with intercept 0: pulls p toward 50% without moving its side.

    The same transform as models/nfl_total_v7.py, duplicated on purpose: scalar_artifact_replay
    hashes each engine file on its own, so this file must carry the transform it replays. slope ==
    1.0 returns p untouched (sigmoid(logit(p)) is not bit-exact), so an artifact without
    probability_logit_slope prices exactly as before the field existed.
    """
    if slope == 1.0:
        return p
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability outside (0, 1) passed to _logit_shrink: {p}")
    if slope == 0.0:
        return 0.5
    return 1.0 / (1.0 + math.exp(-slope * math.log(p / (1.0 - p))))


def _validated_logit_slope(value: Any) -> float:
    slope = float(value)
    if not math.isfinite(slope) or not 0.0 <= slope <= 1.0:
        raise ValueError(f"probability_logit_slope must be in [0, 1], got {value!r}")
    return slope


def _total_probabilities(line: float, mean: float, sd: float, slope: float) -> tuple[float, float]:
    """Served (P(over), P(under)): the normal-CDF price, then the logit shrink. Shared with the replay.

    At slope 1.0 both values are computed exactly as before the shrink existed (under is the CDF
    itself, not 1 - over), so the research artifacts stay bit-identical.
    """
    under_prob = _normal_cdf(line, mean, sd)
    over_prob = 1.0 - under_prob
    if slope != 1.0:
        over_prob = _logit_shrink(over_prob, slope)
        under_prob = 1.0 - over_prob
    return over_prob, under_prob


class RollingBaselineTrendEngine(TrendEngine):
    """TrendEngine whose league baseline is the current-season rolling mean (B5).

    Only the baseline changes; every other TrendEngine computation is inherited unchanged.
    """

    def __init__(
        self,
        games: Iterable[GameRecord],
        *,
        rolling_games: int,
        prior_games: float,
        offseason_gap_days: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(games, offseason_gap_days=offseason_gap_days, **kwargs)
        self._rolling_baseline = _season_rolling_baseline(
            self.games,
            rolling_games=rolling_games,
            prior_games=prior_games,
            offseason_gap_days=offseason_gap_days,
            isolate_postseason=self.isolate_postseason,
        )

    @property
    def league_baseline(self) -> float:
        return self._rolling_baseline


def _season_rolling_baseline(
    games: Sequence[GameRecord],
    *,
    rolling_games: int,
    prior_games: float,
    offseason_gap_days: float,
    isolate_postseason: bool,
) -> float:
    """Mean points per team over the last ``rolling_games`` current-season games.

    The current season starts after the latest gap longer than ``offseason_gap_days``. The
    estimate is shrunk toward the previous season's last ``rolling_games`` games with
    ``prior_games`` pseudo-games, so opening week is not a 1-game average.
    """
    pool = [g for g in games if not (isolate_postseason and getattr(g, "season_type", "") in _ISOLATED_TYPES)]
    if not pool:
        return 0.0
    season_start = 0
    for index in range(len(pool) - 1, 0, -1):
        if (pool[index].start - pool[index - 1].start).total_seconds() / 86400.0 > offseason_gap_days:
            season_start = index
            break
    current = pool[season_start:][-rolling_games:]
    previous = pool[:season_start][-rolling_games:]

    def per_team(rows: Sequence[GameRecord]) -> float:
        return sum(float(g.home_score) + float(g.away_score) for g in rows) / (2.0 * len(rows))

    if not previous:
        return per_team(current)
    prior = per_team(previous)
    n = float(len(current))
    if n == 0:
        return prior
    return (n * per_team(current) + prior_games * prior) / (n + prior_games)


class NBAStructuralV91Engine(NBAStructuralV9Engine):
    """v9 engine plus switchable B1/B4/B5/B6/B7 fixes (research challenger)."""

    def __init__(
        self,
        *,
        current_season_sample: bool = False,
        rolling_baseline_games: int = 0,
        baseline_prior_games: float = 30.0,
        fatigue_mode: str = "v9",
        b2b_offense_penalty: float = 0.0,
        b2b_defense_penalty: float = 0.0,
        three_in_four_offense_penalty: float = 0.0,
        three_in_four_defense_penalty: float = 0.0,
        playoff_total_offset: float = 0.0,
        probability_logit_slope: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if fatigue_mode not in {"v9", "fitted"}:
            raise ValueError(f"unknown fatigue_mode {fatigue_mode!r}")
        self.current_season_sample = current_season_sample
        self.rolling_baseline_games = int(rolling_baseline_games)
        self.baseline_prior_games = float(baseline_prior_games)
        self.fatigue_mode = fatigue_mode
        self.b2b_offense_penalty = float(b2b_offense_penalty)
        self.b2b_defense_penalty = float(b2b_defense_penalty)
        self.three_in_four_offense_penalty = float(three_in_four_offense_penalty)
        self.three_in_four_defense_penalty = float(three_in_four_defense_penalty)
        self.playoff_total_offset = float(playoff_total_offset)
        self.probability_logit_slope = _validated_logit_slope(probability_logit_slope)

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBAStructuralV91Engine:
        engine = cast(NBAStructuralV91Engine, super().from_artifact(artifact))
        engine.current_season_sample = bool(artifact.get("current_season_sample", False))
        engine.rolling_baseline_games = int(artifact.get("rolling_baseline_games", 0))
        engine.baseline_prior_games = float(artifact.get("baseline_prior_games", 30.0))
        engine.fatigue_mode = str(artifact.get("fatigue_mode", "v9"))
        if engine.fatigue_mode not in {"v9", "fitted"}:
            raise ValueError(f"unknown fatigue_mode {engine.fatigue_mode!r}")
        engine.b2b_offense_penalty = float(artifact.get("b2b_offense_penalty", 0.0))
        engine.b2b_defense_penalty = float(artifact.get("b2b_defense_penalty", 0.0))
        engine.three_in_four_offense_penalty = float(artifact.get("three_in_four_offense_penalty", 0.0))
        engine.three_in_four_defense_penalty = float(artifact.get("three_in_four_defense_penalty", 0.0))
        engine.playoff_total_offset = float(artifact.get("playoff_total_offset", 0.0))
        engine.probability_logit_slope = _validated_logit_slope(artifact.get("probability_logit_slope", 1.0))
        return engine

    @property
    def _total_label(self) -> str:
        return (
            "v9.1"
            if self.probability_logit_slope == 1.0
            else f"v9.2 (shrunk, s={self.probability_logit_slope})"
        )

    def _trend(self, history: Sequence[GameRecord]) -> TrendEngine:
        if self.rolling_baseline_games > 0:
            return RollingBaselineTrendEngine(
                history,
                rolling_games=self.rolling_baseline_games,
                prior_games=self.baseline_prior_games,
                offseason_gap_days=self.offseason_gap_days,
                offseason_retention=self.offseason_retention,
                isolate_postseason=self.isolate_postseason,
            )
        return TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
        season_types: Mapping[str, str] | None = None,
    ) -> list[GamePrediction]:
        """Price ``upcoming``. ``season_types`` (event_id -> schedule season type) drives B7."""
        if not upcoming:
            return []
        season_types = season_types or {}
        fitted_fatigue = self.fatigue_mode == "fitted"

        elo = build_elo(
            history,
            "nba",
            offseason_gap_days=int(self.offseason_gap_days),
            home_advantage=self.elo_home_advantage,
        )
        trend = self._trend(history)
        baseline = trend.league_baseline or self.baseline_points_per_team

        predictions: list[GamePrediction] = []
        sample = min(len(history), 200)

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)

            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline

            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            r_home = elo.rating(game.home_team) + (0.0 if game.neutral_site else elo.home_advantage)
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0
            home_win = (
                elo.expected_neutral_win(game.home_team, game.away_team)
                if game.neutral_site
                else elo.expected_home_win(game.home_team, game.away_team)
            )

            game_start_dt = parse_utc(game.event_start_utc)
            home_prev = [
                p
                for p in history
                if p.start < game_start_dt
                and (p.home_team == game.home_team or p.away_team == game.home_team)
            ]
            away_prev = [
                p
                for p in history
                if p.start < game_start_dt
                and (p.home_team == game.away_team or p.away_team == game.away_team)
            ]
            home_prev.sort(key=lambda p: p.start)
            away_prev.sort(key=lambda p: p.start)

            home_rest = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0 if home_prev else 3.0
            away_rest = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0 if away_prev else 3.0
            rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
            home_b2b = home_rest <= 1.25
            away_b2b = away_rest <= 1.25

            home_3in4 = False
            away_3in4 = False
            if (self.schedule_density_drag or fitted_fatigue) and game_start_dt is not None:
                h_4d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                a_4d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                home_3in4 = len(h_4d) >= 2
                away_3in4 = len(a_4d) >= 2

            if not fitted_fatigue:
                # v9 one-sided constants, unchanged.
                if home_b2b and self.b2b_penalty_home > 0:
                    home_points -= self.b2b_penalty_home
                if away_b2b and self.b2b_penalty_away > 0:
                    away_points -= self.b2b_penalty_away
                if home_3in4 and not home_b2b:
                    home_points -= 0.80
                if away_3in4 and not away_b2b:
                    away_points -= 1.20

            est_possessions = self.league_pace
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
            pace_drag = -self.b2b_pace_drag if (home_b2b or away_b2b) and not fitted_fatigue else 0.0
            est_possessions = max(80.0, (self.league_pace * pf_away * pf_home) + pace_drag)

            if self.half_pace_additive:
                expected_total = (away_points + home_points) + (est_possessions - self.league_pace) * 0.55
            else:
                expected_total = (away_points + home_points) * (est_possessions / self.league_pace)

            if not fitted_fatigue:
                if (home_3in4 or away_3in4) and self.schedule_density_drag:
                    expected_total -= 0.60
                if home_b2b and away_b2b:
                    expected_total -= 1.00

            if game.availability_total_loss > 0:
                expected_total -= 0.15 * game.availability_total_loss

            trend_margin = (home_points - away_points) + (
                0.0 if game.neutral_site else self.home_court_points
            )
            expected_margin = (
                self.elo_weight * elo_margin + self.trend_weight * trend_margin + self.rest_weight * rest_diff
            )
            expected_margin += game.availability_points_gap

            if fitted_fatigue:
                # One per-team points model feeds both markets at full weight (B6).
                home_tired_b2b, away_tired_b2b = float(home_b2b), float(away_b2b)
                home_tired_34 = float(home_3in4 and not home_b2b)
                away_tired_34 = float(away_3in4 and not away_b2b)
                home_delta = (
                    -self.b2b_offense_penalty * home_tired_b2b
                    + self.b2b_defense_penalty * away_tired_b2b
                    - self.three_in_four_offense_penalty * home_tired_34
                    + self.three_in_four_defense_penalty * away_tired_34
                )
                away_delta = (
                    -self.b2b_offense_penalty * away_tired_b2b
                    + self.b2b_defense_penalty * home_tired_b2b
                    - self.three_in_four_offense_penalty * away_tired_34
                    + self.three_in_four_defense_penalty * home_tired_34
                )
                expected_total += home_delta + away_delta
                expected_margin += home_delta - away_delta

            # B4: spread_away_line is the home-margin line (away covers iff margin < line).
            if self.market_blend_weight > 0.0 and game.spread_away_line is not None:
                market_home_margin = game.spread_away_line
                expected_margin = (
                    1.0 - self.market_blend_weight
                ) * expected_margin + self.market_blend_weight * market_home_margin

            margin_proxy = abs(expected_margin)
            if self.current_season_sample:
                current_season_games_min = min(
                    away_trend.current_season_games_played, home_trend.current_season_games_played
                )
            else:
                current_season_games_min = min(away_trend.games_played, home_trend.games_played)
            sample_maturity = (
                min(1.0, max(0.0, current_season_games_min / 15.0)) if self.sample_scaled_overtime else 1.0
            )
            if self.overtime_risk_adjustment:
                if margin_proxy <= 3.5:
                    expected_total += 1.20 * sample_maturity
                elif margin_proxy <= 6.5:
                    expected_total += 0.60 * sample_maturity

            is_playoff = season_types.get(game.event_id, "") in POSTSEASON_TYPES
            if is_playoff and self.playoff_total_offset != 0.0:
                expected_total += self.playoff_total_offset

            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                c_away = away_trend.consistency or 0.05
                c_home = home_trend.consistency or 0.05
                avg_c = (c_away + c_home) / 2.0
                effective_margin_sd = max(
                    12.0, min(17.5, self.margin_sd * (1.0 + 0.25 * (1.0 - avg_c / 0.055)))
                )
            if self.pace_sd_scaling:
                effective_margin_sd = max(
                    10.0, min(20.0, effective_margin_sd * math.sqrt(est_possessions / self.league_pace))
                )
                effective_total_sd = max(
                    13.0, min(24.0, self.total_sd * math.sqrt(est_possessions / self.league_pace))
                )
            else:
                effective_total_sd = self.total_sd

            uncertainty = max(0.03, min(0.20, 0.20 - 0.005 * sample))

            p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            p_home = (
                1.0 - self.moneyline_margin_weight
            ) * home_win + self.moneyline_margin_weight * p_margin_home
            p_h = max(0.0001, min(0.9999, p_home))
            p_a = max(0.0001, min(0.9999, 1.0 - p_home))

            fatigue_flags = {
                "home_b2b": home_b2b,
                "away_b2b": away_b2b,
                "home_3in4": home_3in4,
                "away_3in4": away_3in4,
            }
            ml_base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.moneyline_version,
                "feature_basis": {
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "expected_margin": round(expected_margin, 2),
                    "expected_total": round(expected_total, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "est_possessions": round(est_possessions, 1),
                    "history_games": len(history),
                    "current_season_games_min": current_season_games_min,
                    "league_baseline": round(baseline, 3),
                    "playoff": is_playoff,
                    **fatigue_flags,
                },
            }
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(p_h, 6), "away": round(p_a, 6)},
                    rationale=f"NBA v9.1 coherent ML: margin {expected_margin:+.1f}, win prob {p_h * 100:.1f}%.",
                    **ml_base,
                )
            )

            if game.spread_away_line is not None:
                away_cover = _normal_cdf(game.spread_away_line, expected_margin, effective_margin_sd)
                spread_base: GamePredictionBase = {
                    "event_id": game.event_id,
                    "event_start_utc": game.event_start_utc,
                    "league": self.league,
                    "away_team": game.away_team,
                    "home_team": game.home_team,
                    "uncertainty": round(uncertainty, 4),
                    "model_version": self.spread_version,
                    "feature_basis": {
                        "elo_home": round(elo.rating(game.home_team), 1),
                        "elo_away": round(elo.rating(game.away_team), 1),
                        "expected_margin": round(expected_margin, 2),
                        "effective_margin_sd": round(effective_margin_sd, 2),
                        "est_possessions": round(est_possessions, 1),
                        "history_games": len(history),
                        "current_season_games_min": current_season_games_min,
                    },
                }
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        inference_inputs={
                            "mean": expected_margin,
                            "sd": effective_margin_sd,
                            "line": game.spread_away_line,
                        },
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(1.0 - away_cover, 6),
                        },
                        rationale=(
                            f"NBA v9.1 margin {expected_margin:+.1f} vs away line {game.spread_away_line:+.1f} "
                            f"(cover {away_cover * 100:.1f}%)."
                        ),
                        **spread_base,
                    )
                )

            if game.total_line is not None:
                over_prob, under_prob = _total_probabilities(
                    game.total_line, expected_total, effective_total_sd, self.probability_logit_slope
                )
                total_base: GamePredictionBase = {
                    "event_id": game.event_id,
                    "event_start_utc": game.event_start_utc,
                    "league": self.league,
                    "away_team": game.away_team,
                    "home_team": game.home_team,
                    "uncertainty": round(uncertainty, 4),
                    "model_version": self.total_version,
                    "feature_basis": {
                        "expected_total": round(expected_total, 2),
                        "effective_total_sd": round(effective_total_sd, 2),
                        "est_possessions": round(est_possessions, 1),
                        "history_games": len(history),
                        "current_season_games_min": current_season_games_min,
                    },
                }
                if self.probability_logit_slope != 1.0:
                    # Audit only (the replay recomputes from the inference inputs): the unshrunk price.
                    total_base["feature_basis"]["probability_logit_slope"] = self.probability_logit_slope
                    total_base["feature_basis"]["over_probability_unshrunk"] = round(
                        1.0 - _normal_cdf(game.total_line, expected_total, effective_total_sd), 6
                    )
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
                            f"NBA {self._total_label} total {expected_total:.1f} vs line "
                            f"{game.total_line:.1f} (over {over_prob * 100:.1f}%, under {under_prob * 100:.1f}%)."
                        ),
                        **total_base,
                    )
                )

        return predictions


class NBATotalModelV92:
    """Serving wrapper for ``nba-total-margin-v9.2``: the v9.1 total engine with the shrunk P(over).

    Player availability (B9) is never applied: v9.1 never fitted it, and the served heuristic raised
    live margin MAE 11.78 -> 12.51 in the 2026-11-19 tests. Any availability on the inputs is zeroed
    here, so the guarantee does not depend on the caller.
    """

    def __init__(self, engine: NBAStructuralV91Engine) -> None:
        self.engine = engine
        self.version = engine.total_version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBATotalModelV92:
        if artifact.get("model_version") != NBA_TOTAL_V92_VERSION:
            raise ValueError(f"not a {NBA_TOTAL_V92_VERSION} artifact: {artifact.get('model_version')!r}")
        if "probability_logit_slope" not in artifact:
            raise ValueError(f"{NBA_TOTAL_V92_VERSION} artifact must set probability_logit_slope")
        if artifact.get("pace_sd_scaling"):
            # The scalar replay checks the stored sd against total_sd; a pace-scaled sd cannot replay.
            raise ValueError(f"{NBA_TOTAL_V92_VERSION} requires pace_sd_scaling false")
        engine = NBAStructuralV91Engine.from_artifact(artifact)
        if engine.total_version != NBA_TOTAL_V92_VERSION:
            raise ValueError(f"{NBA_TOTAL_V92_VERSION} artifact must set total_version to its own id")
        return cls(engine)

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
        season_types: Mapping[str, str] | None = None,
    ) -> list[GamePrediction]:
        no_availability = [
            replace(game, availability_points_gap=0.0, availability_total_loss=0.0) for game in upcoming
        ]
        return [
            p
            for p in self.engine.predict_games(history, no_availability, season_types=season_types)
            if p.market_type == "total"
        ]

    predict_games = predict_totals
