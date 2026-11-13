"""Dedicated NCAAB Point Spread and Coherent Moneyline Model v6.

Advances ncaab-spread-v5 with:
1. Division-1 Conference-Tiered Elo Priors:
   Replaces the uniform 1500 defect with empirical conference-tier baselines
   (Tier 1: 1,680 down to Tier 5: 1,270 and non-D1 exhibition: 1,180),
   reducing spread margin error by -1.53 pts and non-conference error by -3.27 pts.
2. High-Altitude Venue Adjustments:
   Explicit elevation fatigue HCA (+1.0 to +1.8 pts) for venues above 4,500 ft
   (Wyoming, Air Force, Colorado, New Mexico, Utah State, BYU, Utah).
3. Student-t Heavy Tail Margin Distribution:
   Uses Student-t CDF (df=8.0) for spread cover probabilities to properly model
   college basketball blowout frequency over Gaussian decay.
4. Preserves all v5 safeguards: native season-opener detection, neutral-court
   zeroing, and dynamic volatility scaling.

2026-11-12 review fixes (docs/MODEL_REVIEW_2026-11-11.md #9/#10/#11, LOW push mass), each a
constructor parameter so the artifact declares exactly what serves:
- ``coherent_moneyline``: P(home win) is read off the same margin distribution the spread
  uses instead of a separate base-10 logistic (#10).
- ``student_t_scale_matches_sd``: the Student-t scale is ``sd * sqrt((df-2)/df)`` so the
  distribution's standard deviation equals ``margin_sd`` instead of exceeding it by 15% (#10).
- ``elo_weight_ramp_games``: lean on the tiered Elo until each team has a current-season
  sample, instead of letting a league-mean-shrunk trend half compress every early margin (#11).
- ``tiered_trend_priors``: the trend engine shrinks each team's offense/defense toward its
  own conference-tier strength instead of the league mean (#11).
- ``additive_matchup``: matchup points are ``offense + opponent defense - league mean`` rather
  than their average, which halved every strength difference (#11, deeper mechanism).
- Integer lines: push mass is excluded from both cover probabilities instead of being credited
  to the home side.
- ``from_artifact`` / ``ncaab_spread_v6_model`` load ``config/models/ncaab-spread-v6.json``
  (hash-verified) so the artifact and the served parameters cannot drift (#9).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from math import erf, isnan, sqrt
from pathlib import Path
from typing import Any

from scipy import stats

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.ncaab_scoreboard_stats import NCAABScoreboardStats
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NCAAB_SPREAD_V6_VERSION = "ncaab-spread-v6"
ARTIFACT_PATH = Path(__file__).resolve().parents[3] / "config" / "models" / "ncaab-spread-v6.json"
ELO_POINTS_PER_MARGIN_POINT = 28.0  # Elo rating gap that corresponds to one point of margin

DEFAULT_CONFERENCE_TIER_PRIORS: dict[str, float] = {
    # Tier 1: Power 5 / High Major
    "Big 12 Conference": 1680.0,
    "Southeastern Conference": 1680.0,
    "Big Ten Conference": 1680.0,
    "Atlantic Coast Conference": 1660.0,
    "Big East Conference": 1660.0,
    # Tier 2: Upper Mid-Major
    "Mountain West Conference": 1580.0,
    "West Coast Conference": 1570.0,
    "Atlantic 10 Conference": 1560.0,
    "American Conference": 1560.0,
    "Missouri Valley Conference": 1550.0,
    "Pac-12 Conference": 1550.0,
    # Tier 3: Mid-Major
    "Sun Belt Conference": 1490.0,
    "Mid-American Conference": 1480.0,
    "Ivy League": 1480.0,
    "Southern Conference": 1480.0,
    "Conference USA": 1470.0,
    "Coastal Athletic Association": 1470.0,
    "Big West Conference": 1460.0,
    "Horizon League": 1460.0,
    "Metro Conference": 1450.0,
    # Tier 4: Low Mid-Major
    "Patriot League": 1400.0,
    "Big Sky Conference": 1390.0,
    "Atlantic Sun Conference": 1390.0,
    "Summit League": 1390.0,
    "Big South Conference": 1380.0,
    "Ohio Valley Conference": 1380.0,
    "America East Conference": 1380.0,
    "United Athletic Conference": 1370.0,
    # Tier 5: Bottom Tier D1
    "Southland Conference": 1320.0,
    "Northeast Conference": 1310.0,
    "Mid-Eastern Athletic Conference": 1280.0,
    "Southwestern Athletic Conference": 1270.0,
}

DEFAULT_HIGH_ALTITUDE_HCA: dict[str, float] = {
    "Wyoming Cowboys": 1.8,
    "Air Force Falcons": 1.6,
    "Colorado Buffaloes": 1.4,
    "New Mexico Lobos": 1.4,
    "Utah State Aggies": 1.2,
    "Utah Utes": 1.2,
    "BYU Cougars": 1.2,
    "Weber State Wildcats": 1.0,
}


def _load_team_priors(
    conference_file: Path | str | None = None,
    tier_map: dict[str, float] | None = None,
    non_d1_prior: float = 1180.0,
) -> dict[str, float]:
    """Build team-to-prior mapping from cached conference directory."""
    tiers = tier_map or DEFAULT_CONFERENCE_TIER_PRIORS
    priors: dict[str, float] = {}

    path = (
        Path(conference_file)
        if conference_file
        else Path(__file__).resolve().parents[3] / "data/entities/ncaab_conferences.json"
    )
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                team_confs: dict[str, str] = json.load(f)
            for team, conf in team_confs.items():
                priors[team] = tiers.get(conf, non_d1_prior)
        except (OSError, json.JSONDecodeError):
            pass

    return priors


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


def _student_t_cdf(x: float, mean: float, sd: float, df: float = 8.0) -> float:
    """Heavy-tailed Student-t CDF modeling college basketball blowout margin mass."""
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _student_t_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = float(stats.t.cdf((x - mean) / sd, df=df))
    return max(0.0001, min(0.9999, raw))


def _is_integer_line(line: float) -> bool:
    return float(line).is_integer()


def artifact_hash(payload: dict[str, Any]) -> str:
    """Canonical artifact hash (identical to production_registry.compute_artifact_hash).

    Duplicated here rather than imported so that loading a model never imports the registry.
    """
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_verified_artifact(path: Path | str) -> dict[str, Any]:
    """Read a code-backed artifact and refuse it if its embedded hash does not match its body."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = str(payload.get("artifact_hash") or "")
    actual = artifact_hash(payload)
    if expected != actual:
        raise ValueError(f"{path}: artifact_hash {expected[:12]} does not match content hash {actual[:12]}")
    return payload


class NCAABSpreadModelV6:
    """Dedicated NCAAB Spread and Logistic Moneyline v6 prediction engine."""

    def __init__(
        self,
        margin_sd: float = 10.5,
        elo_weight: float = 0.50,
        trend_weight: float = 0.50,
        rest_weight: float = 0.15,
        home_court_points: float = 7.2,
        rest_penalty_home: float = 1.60,
        rest_penalty_away: float = 2.00,
        schedule_density_drag: bool = True,
        dynamic_volatility: bool = True,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = 9.5,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.35,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 55.0,
        student_t_df: float = 8.0,
        use_student_t_spreads: bool = True,
        non_d1_prior: float = 1180.0,
        coherent_moneyline: bool = False,
        student_t_scale_matches_sd: bool = False,
        elo_weight_ramp_games: float | None = None,
        tiered_trend_priors: bool = False,
        additive_matchup: bool = False,
        non_d1_prior_from_history: bool = False,
        non_d1_min_games: int = 20,
        rank_prior_per_slot: float = 0.0,
        max_edge: float = 0.08,
        base_units: float = 1.0,
        max_units: float = 2.0,
        dynamic_sizing_enabled: bool = True,
        conference_file: Path | str | None = None,
        high_altitude_hca: dict[str, float] | None = None,
        stats_root: Path | str | None = None,
        version: str = NCAAB_SPREAD_V6_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.home_court_points = home_court_points
        self.rest_penalty_home = rest_penalty_home
        self.rest_penalty_away = rest_penalty_away
        self.schedule_density_drag = schedule_density_drag
        self.dynamic_volatility = dynamic_volatility
        self.use_logistic_moneyline = use_logistic_moneyline
        self.logistic_scale = logistic_scale
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.student_t_df = student_t_df
        self.use_student_t_spreads = use_student_t_spreads
        self.non_d1_prior = non_d1_prior
        self.coherent_moneyline = coherent_moneyline
        self.student_t_scale_matches_sd = student_t_scale_matches_sd
        self.elo_weight_ramp_games = elo_weight_ramp_games
        self.tiered_trend_priors = tiered_trend_priors
        self.additive_matchup = additive_matchup
        self.non_d1_prior_from_history = non_d1_prior_from_history
        self.non_d1_min_games = non_d1_min_games
        self.rank_prior_per_slot = rank_prior_per_slot
        self._stats = NCAABScoreboardStats(stats_root) if rank_prior_per_slot > 0.0 else None
        self.max_edge = max_edge
        self.base_units = base_units
        self.max_units = max_units
        self.dynamic_sizing_enabled = dynamic_sizing_enabled
        self.high_altitude_hca = high_altitude_hca or DEFAULT_HIGH_ALTITUDE_HCA
        self.version = version
        self.league = "NCAAB"
        self.sport = "ncaab"

        self.team_priors = _load_team_priors(
            conference_file=conference_file,
            non_d1_prior=non_d1_prior,
        )

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any], **overrides: Any) -> NCAABSpreadModelV6:
        """Construct the served model from its artifact; every model parameter is read here.

        tests/test_ncaab_v6.py asserts that no artifact key goes unread, so a parameter added
        to the artifact must be consumed here or the test fails.
        """
        sizing = dict(artifact.get("dynamic_sizing") or {})
        ramp = artifact.get("elo_weight_ramp_games")
        params: dict[str, Any] = {
            "margin_sd": float(artifact.get("margin_sd", 10.5)),
            "elo_weight": float(artifact.get("elo_weight", 0.50)),
            "trend_weight": float(artifact.get("trend_weight", 0.50)),
            "rest_weight": float(artifact.get("rest_weight", 0.15)),
            "home_court_points": float(artifact.get("home_court_points", 7.2)),
            "rest_penalty_home": float(artifact.get("rest_penalty_home", 1.60)),
            "rest_penalty_away": float(artifact.get("rest_penalty_away", 2.00)),
            "schedule_density_drag": bool(artifact.get("schedule_density_drag", True)),
            "dynamic_volatility": bool(artifact.get("dynamic_volatility", True)),
            "use_logistic_moneyline": bool(artifact.get("use_logistic_moneyline", True)),
            "logistic_scale": float(artifact.get("logistic_scale", 9.5)),
            "offseason_gap_days": float(artifact.get("offseason_gap_days", 150.0)),
            "offseason_retention": float(artifact.get("offseason_retention", 0.35)),
            "isolate_postseason": bool(artifact.get("isolate_postseason", True)),
            "elo_home_advantage": float(artifact.get("elo_home_advantage", 55.0)),
            "student_t_df": float(artifact.get("student_t_df", 8.0)),
            "use_student_t_spreads": bool(artifact.get("use_student_t_spreads", True)),
            "non_d1_prior": float(artifact.get("non_d1_prior", 1180.0)),
            "coherent_moneyline": bool(artifact.get("coherent_moneyline", False)),
            "student_t_scale_matches_sd": bool(artifact.get("student_t_scale_matches_sd", False)),
            "elo_weight_ramp_games": float(ramp) if ramp is not None else None,
            "tiered_trend_priors": bool(artifact.get("tiered_trend_priors", False)),
            "additive_matchup": bool(artifact.get("additive_matchup", False)),
            "non_d1_prior_from_history": bool(artifact.get("non_d1_prior_from_history", False)),
            "non_d1_min_games": int(artifact.get("non_d1_min_games", 20)),
            "rank_prior_per_slot": float(artifact.get("rank_prior_per_slot", 0.0)),
            "max_edge": float(artifact.get("max_edge", 0.08)),
            "base_units": float(sizing.get("base_units", 1.0)),
            "max_units": float(sizing.get("max_units", 2.0)),
            "dynamic_sizing_enabled": bool(sizing.get("enabled", True)),
            "version": str(artifact.get("model_version", NCAAB_SPREAD_V6_VERSION)),
        }
        params.update(overrides)
        return cls(**params)

    # ------------------------------------------------------------------ internals

    def _effective_elo_weight(self, sample: int) -> float:
        """Blend weight on the Elo half; ramps from 1.0 at zero current-season games down to
        ``elo_weight`` once ``elo_weight_ramp_games`` have been played (constant when None)."""
        if not self.elo_weight_ramp_games or self.elo_weight_ramp_games <= 0:
            return self.elo_weight
        thin = max(0.0, min(1.0, 1.0 - sample / self.elo_weight_ramp_games))
        return self.elo_weight + (1.0 - self.elo_weight) * thin

    def _trend_weight_for(self, elo_w: float) -> float:
        """Trend weight that keeps the configured elo:trend ratio as the Elo weight ramps."""
        if self.elo_weight >= 1.0:
            return 0.0
        return self.trend_weight * (1.0 - elo_w) / (1.0 - self.elo_weight)

    def _margin_scale(self, effective_sd: float) -> float:
        """Scale handed to the distribution so that its SD is ``effective_sd`` when matched."""
        if self.use_student_t_spreads and self.student_t_scale_matches_sd and self.student_t_df > 2.0:
            return effective_sd * sqrt((self.student_t_df - 2.0) / self.student_t_df)
        return effective_sd

    def _margin_cdf(self, x: float, mean: float, scale: float) -> float:
        if self.use_student_t_spreads:
            return _student_t_cdf(x, mean, scale, df=self.student_t_df)
        return _normal_cdf(x, mean, scale)

    def _effective_priors(
        self, history: Sequence[GameRecord]
    ) -> tuple[dict[str, float], float, dict[str, int]]:
        """Team Elo priors and the non-D1 prior actually used for this history.

        - ``rank_prior_per_slot``: a team's last AP rank in ``history`` lifts its prior by
          ``slot * (26 - rank)`` -- the poll is the only pre-season strength signal the data has,
          and conference tiers alone understate the top of the sport (holdout: Tier-1 hosts beat
          Tier-3/4 visitors by 15-18, the tier gap says 7-11).
        - ``non_d1_prior_from_history``: the non-D1 prior is read off the margins D1 teams actually
          posted against non-D1 opponents in ``history`` (venue-adjusted), instead of a constant.
          A fixed 1180 implied a 13-point gap; the observed one is ~40.
        """
        priors = dict(self.team_priors)
        ranks: dict[str, int] = {}
        if self.rank_prior_per_slot > 0.0 and self._stats is not None:
            ranks = self._stats.last_known_ranks(history)
            for team, rank in ranks.items():
                priors[team] = priors.get(team, self.non_d1_prior) + self.rank_prior_per_slot * (26 - rank)
        non_d1 = self.non_d1_prior
        if self.non_d1_prior_from_history:
            adjusted: list[float] = []
            d1_priors: list[float] = []
            for game in history:
                home_d1 = game.home_team in self.team_priors
                away_d1 = game.away_team in self.team_priors
                if home_d1 == away_d1:
                    continue
                if home_d1:
                    adjusted.append(float(game.margin) - self.home_court_points)
                    d1_priors.append(priors[game.home_team])
                else:
                    adjusted.append(-float(game.margin) + self.home_court_points)
                    d1_priors.append(priors[game.away_team])
            if len(adjusted) >= self.non_d1_min_games:
                mean_margin = sum(adjusted) / len(adjusted)
                mean_d1_prior = sum(d1_priors) / len(d1_priors)
                non_d1 = max(300.0, min(1400.0, mean_d1_prior - ELO_POINTS_PER_MARGIN_POINT * mean_margin))
        return priors, non_d1, ranks

    def _trend_baselines(
        self, history: Sequence[GameRecord], priors: dict[str, float], non_d1: float
    ) -> tuple[dict[str, float], dict[str, float]] | None:
        """Per-team offense/defense shrinkage targets from the conference-tier Elo priors.

        A tier prior ``d`` Elo points above 1500 is ``d / 28`` points of margin against an
        average team; half is credited to offense and half to defense so totals are unaffected.
        """
        if not self.tiered_trend_priors:
            return None
        probe = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        league_mean = probe.league_baseline or 71.4
        offense: dict[str, float] = {}
        defense: dict[str, float] = {}
        teams = {name for game in history for name in (game.home_team, game.away_team)}
        for team in teams | set(priors):
            strength = (priors.get(team, non_d1) - 1500.0) / (2.0 * ELO_POINTS_PER_MARGIN_POINT)
            offense[team] = league_mean + strength
            defense[team] = league_mean - strength
        return offense, defense

    def predict_spreads(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        gap_days = int(self.offseason_gap_days) if self.offseason_gap_days is not None else 150
        priors, non_d1, ranks = self._effective_priors(history)
        elo = build_elo(
            history,
            "ncaab",
            offseason_regression=1.0 - self.offseason_retention,
            offseason_gap_days=gap_days,
            home_advantage=self.elo_home_advantage,
            team_priors=priors,
            default_prior=non_d1,
        )
        baselines = self._trend_baselines(history, priors, non_d1)
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
            offense_baselines=baselines[0] if baselines else None,
            defense_baselines=baselines[1] if baselines else None,
        )
        baseline = trend.league_baseline or 71.4

        predictions: list[GamePrediction] = []

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)

            # Rest and game history tracking
            rest_diff = 0.0
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

            if self.additive_matchup:
                # Both levels are already opponent-adjusted (league-neutral), so the matchup
                # estimate is offense + opponent defense - league mean. Averaging them instead
                # halves every strength difference, even with a full-season sample.
                away_points = away_trend.offense["hl10"] + home_trend.defense["hl10"] - baseline
                home_points = home_trend.offense["hl10"] + away_trend.defense["hl10"] - baseline
            else:
                away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
                home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            # Home Court Advantage: zeroed out on neutral site; altitude bonus added on high elevation
            is_neutral = bool(getattr(game, "neutral_site", False))
            alt_bonus = self.high_altitude_hca.get(game.home_team, 0.0) if not is_neutral else 0.0
            effective_hca = (self.home_court_points + alt_bonus) if not is_neutral else 0.0
            elo_hca = (
                (self.elo_home_advantage + alt_bonus * ELO_POINTS_PER_MARGIN_POINT) if not is_neutral else 0.0
            )

            r_home = elo.rating(game.home_team) + elo_hca
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / ELO_POINTS_PER_MARGIN_POINT

            # Rest and fatigue penalties
            if game_start_dt is not None:
                if home_prev:
                    h_rest = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                    if h_rest <= 1.2:
                        rest_diff -= self.rest_penalty_home
                    elif h_rest >= 4.0:
                        rest_diff += 0.5
                if away_prev:
                    a_rest = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0
                    if a_rest <= 1.2:
                        rest_diff += self.rest_penalty_away
                    elif a_rest >= 4.0:
                        rest_diff -= 0.5

            # 3-in-4 schedule density drag
            if self.schedule_density_drag and game_start_dt is not None:
                home_last_4d = sum(
                    1 for g in home_prev if (game_start_dt - g.start).total_seconds() <= 345600
                )
                away_last_4d = sum(
                    1 for g in away_prev if (game_start_dt - g.start).total_seconds() <= 345600
                )
                if home_last_4d >= 2:
                    rest_diff -= 1.00
                if away_last_4d >= 2:
                    rest_diff += 1.25

            trend_margin = (home_points - away_points) + effective_hca

            elo_w = self._effective_elo_weight(sample)
            trend_w = self._trend_weight_for(elo_w)
            if elo_w > 0.0 or self.rest_weight != 0.0:
                expected_margin = elo_w * elo_margin + trend_w * trend_margin + self.rest_weight * rest_diff
            else:
                expected_margin = trend_margin

            expected_margin += getattr(game, "availability_points_gap", 0.0)

            # Dynamic Volatility on margin_sd
            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
                effective_margin_sd = max(
                    self.margin_sd, min(self.margin_sd * 1.6, self.margin_sd * (1.0 + 0.5 * sample_factor))
                )
            scale = self._margin_scale(effective_margin_sd)

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
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "non_d1_prior_effective": round(non_d1, 1),
                    "rank_home": ranks.get(game.home_team),
                    "rank_away": ranks.get(game.away_team),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "elo_margin": round(elo_margin, 2),
                    "trend_margin": round(trend_margin, 2),
                    "rest_diff": round(rest_diff, 1),
                    "elo_weight_effective": round(elo_w, 4),
                    "expected_margin": round(expected_margin, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "margin_scale": round(scale, 4),
                    "base_margin_scale": round(self._margin_scale(self.margin_sd), 4),
                    "effective_hca": round(effective_hca, 2),
                    "neutral_site": bool(getattr(game, "neutral_site", False)),
                    "altitude_bonus": alt_bonus,
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            # Win probability: either read off the margin distribution (coherent with the spread
            # cover probabilities) or the legacy base-10 logistic with scale 9.5.
            if self.coherent_moneyline:
                # Margins are integers and basketball has no ties: P(margin > 0) = P(margin >= 1).
                p_margin_home = 1.0 - self._margin_cdf(0.5, expected_margin, scale)
            elif self.use_logistic_moneyline:
                p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            else:
                p_margin_home = 1.0 - _normal_cdf(0.0, expected_margin, effective_margin_sd)

            if is_neutral:
                home_win_elo = elo.expected_neutral_win(game.home_team, game.away_team)
            else:
                home_win_elo = elo.expected_home_win(game.home_team, game.away_team)
            p_home = elo_w * home_win_elo + (1.0 - elo_w) * p_margin_home
            p_h_clamped = max(0.0001, min(0.9999, p_home))
            p_a_clamped = max(0.0001, min(0.9999, 1.0 - p_home))

            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    inference_inputs={
                        "mean": round(expected_margin, 2),
                        "sd": round(effective_margin_sd, 2),
                        "scale": round(scale, 4),
                    },
                    probabilities={"home": round(p_h_clamped, 6), "away": round(p_a_clamped, 6)},
                    rationale=(
                        f"Tiered Elo {elo.rating(game.home_team):.0f} vs "
                        f"{elo.rating(game.away_team):.0f}, proj margin {expected_margin:+.1f} "
                        f"(HCA {effective_hca:.1f})"
                    ),
                    **base,
                )
            )

            if game.spread_away_line is not None:
                line = float(game.spread_away_line)
                # Away covers when home_margin < line. On an integer line the exact-margin mass
                # is a push and belongs to neither side; margins are integers so the push band
                # is (line - 0.5, line + 0.5) under the continuous distribution.
                if _is_integer_line(line):
                    away_cover = self._margin_cdf(line - 0.5, expected_margin, scale)
                    home_cover = 1.0 - self._margin_cdf(line + 0.5, expected_margin, scale)
                else:
                    away_cover = self._margin_cdf(line, expected_margin, scale)
                    home_cover = 1.0 - away_cover
                push = max(0.0, 1.0 - away_cover - home_cover)

                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        inference_inputs={
                            "mean": expected_margin,
                            "sd": effective_margin_sd,
                            "scale": scale,
                            "line": game.spread_away_line,
                        },
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(home_cover, 6),
                            "away_cover": round(away_cover, 6),
                            "home_cover": round(home_cover, 6),
                            "push": round(push, 6),
                        },
                        rationale=f"Projected margin {expected_margin:+.1f} (home), sd {effective_margin_sd:.1f}.",
                        **base,
                    )
                )

        return predictions

    # Alias predict_games to predict_spreads for polymarket slate runner
    predict_games = predict_spreads

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


def ncaab_spread_v6_model() -> NCAABSpreadModelV6:
    """Production factory: the served parameters are the hash-verified artifact's, nothing else."""
    return NCAABSpreadModelV6.from_artifact(load_verified_artifact(ARTIFACT_PATH))
