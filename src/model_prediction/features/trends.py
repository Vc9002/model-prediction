"""Opponent-adjusted rolling trend engine — the core feature pipeline.

Everything is computed from locally cached completed games, oldest to newest,
with exponential decay at three half-lives (3, 10, 25 games), shrinkage toward
a league baseline, and opponent adjustment via strength-of-schedule. Per-date
snapshots are cached by ``FeatureStore`` for backtesting.

The small ``opponent_adjusted_ewm_trend`` primitive at the bottom is the
versioned helper reused by the MLB Trend Engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, log, sqrt
from typing import Any, Iterable, Sequence

from .base import FeatureContext, GameRecord, register_feature


HALF_LIVES = (3.0, 10.0, 25.0)
PRIOR_STRENGTH_GAMES = 12.0


@dataclass(frozen=True)
class TeamTrend:
    """Trend vector for one team as of a date, per half-life."""

    team: str
    games_played: int
    # Offense/defense levels per half-life key ("hl3", "hl10", "hl25"),
    # opponent-adjusted and shrunk toward the league baseline.
    offense: dict[str, float]
    defense: dict[str, float]
    # Momentum = short-half-life level minus long-half-life level.
    offensive_momentum: float
    defensive_momentum: float
    # Consistency: 1 / (1 + stdev of recent scoring); higher = steadier.
    consistency: float
    # Hot/cold: z-score of the hl3 offense level against the team's season mean.
    hot_cold_score: float
    win_rate_hl10: float


def ewm_level(values: Sequence[float], half_life: float, baseline: float, prior: float) -> float:
    """Exponentially weighted level shrunk toward a baseline."""
    if half_life <= 0:
        raise ValueError("half life must be positive")
    if not values:
        return baseline
    decay = exp(-log(2) / half_life)
    weights = [decay ** (len(values) - 1 - index) for index in range(len(values))]
    recent = sum(w * v for w, v in zip(weights, values, strict=True)) / sum(weights)
    shrinkage = len(values) / (len(values) + prior)
    return shrinkage * recent + (1 - shrinkage) * baseline


class TrendEngine:
    """Builds opponent-adjusted trend vectors from a chronological game list."""

    def __init__(
        self,
        games: Iterable[GameRecord],
        half_lives: Sequence[float] = HALF_LIVES,
        prior_strength: float = PRIOR_STRENGTH_GAMES,
    ) -> None:
        self.games = sorted(games, key=lambda game: game.start)
        self.half_lives = tuple(half_lives)
        self.prior_strength = prior_strength
        self._by_team: dict[str, list[dict[str, float]]] = {}
        self._league_points: list[float] = []
        self._index()

    def _index(self) -> None:
        for game in self.games:
            self._league_points.extend([float(game.away_score), float(game.home_score)])
            for team, opponent, scored, allowed, won in (
                (
                    game.away_team,
                    game.home_team,
                    game.away_score,
                    game.home_score,
                    game.away_score > game.home_score,
                ),
                (
                    game.home_team,
                    game.away_team,
                    game.home_score,
                    game.away_score,
                    game.home_score > game.away_score,
                ),
            ):
                self._by_team.setdefault(team, []).append(
                    {
                        "opponent": opponent,
                        "scored": float(scored),
                        "allowed": float(allowed),
                        "won": 1.0 if won else 0.0,
                    }
                )

    @property
    def league_baseline(self) -> float:
        if not self._league_points:
            return 0.0
        return sum(self._league_points) / len(self._league_points)

    def _defense_simple(self, team: str) -> float:
        """Unadjusted points allowed per game — used for opponent adjustment."""
        rows = self._by_team.get(team, [])
        if not rows:
            return self.league_baseline
        return sum(row["allowed"] for row in rows) / len(rows)

    def _offense_simple(self, team: str) -> float:
        rows = self._by_team.get(team, [])
        if not rows:
            return self.league_baseline
        return sum(row["scored"] for row in rows) / len(rows)

    def team_trend(self, team: str) -> TeamTrend:
        rows = self._by_team.get(team, [])
        baseline = self.league_baseline
        # Opponent adjustment: scale each scored value by how stingy the opponent
        # is relative to league average (scoring 5 on a strong defense counts for
        # more than 5 on a weak one), and symmetrically for points allowed.
        adjusted_scored: list[float] = []
        adjusted_allowed: list[float] = []
        for row in rows:
            opponent_defense = self._defense_simple(str(row["opponent"]))
            opponent_offense = self._offense_simple(str(row["opponent"]))
            defense_ratio = opponent_defense / baseline if baseline > 0 else 1.0
            offense_ratio = opponent_offense / baseline if baseline > 0 else 1.0
            adjusted_scored.append(row["scored"] / max(defense_ratio, 1e-6))
            adjusted_allowed.append(row["allowed"] / max(offense_ratio, 1e-6))
        offense = {
            f"hl{int(hl)}": round(ewm_level(adjusted_scored, hl, baseline, self.prior_strength), 6)
            for hl in self.half_lives
        }
        defense = {
            f"hl{int(hl)}": round(ewm_level(adjusted_allowed, hl, baseline, self.prior_strength), 6)
            for hl in self.half_lives
        }
        short_key = f"hl{int(self.half_lives[0])}"
        long_key = f"hl{int(self.half_lives[-1])}"
        recent_scored = [row["scored"] for row in rows[-10:]]
        season_scored = [row["scored"] for row in rows]
        consistency = 1.0 / (1.0 + _stdev(recent_scored)) if recent_scored else 0.0
        # Hot/cold compares the RAW short-half-life scoring level against the
        # team's raw season baseline (same units on both sides of the z-score;
        # the opponent-adjusted levels above serve matchup projection instead).
        hot_cold = 0.0
        if len(season_scored) >= 5:
            season_mean = sum(season_scored) / len(season_scored)
            season_sd = _stdev(season_scored)
            if season_sd > 1e-9:
                raw_recent = ewm_level(season_scored, self.half_lives[0], season_mean, self.prior_strength)
                hot_cold = (raw_recent - season_mean) / season_sd
        wins_recent = [row["won"] for row in rows[-10:]]
        return TeamTrend(
            team=team,
            games_played=len(rows),
            offense=offense,
            defense=defense,
            offensive_momentum=round(offense[short_key] - offense[long_key], 6),
            defensive_momentum=round(defense[long_key] - defense[short_key], 6),
            consistency=round(consistency, 6),
            hot_cold_score=round(hot_cold, 6),
            win_rate_hl10=round(sum(wins_recent) / len(wins_recent), 6) if wins_recent else 0.5,
        )

    def all_trends(self) -> dict[str, TeamTrend]:
        return {team: self.team_trend(team) for team in sorted(self._by_team)}

    def momentum_percentile(self, team: str) -> float:
        """Percentile rank of a team's offensive momentum across the league."""
        trends = self.all_trends()
        if team not in trends or len(trends) < 2:
            return 0.5
        values = sorted(item.offensive_momentum for item in trends.values())
        target = trends[team].offensive_momentum
        below = sum(1 for value in values if value < target)
        return below / (len(values) - 1) if len(values) > 1 else 0.5


def market_lag_signal(
    trend_momentum: float,
    momentum_percentile: float,
    market_move: float | None,
) -> dict[str, Any]:
    """Detect a trend shift that stored Polymarket prices have not priced yet.

    ``market_move`` is the change in the stored executable ask for the same side
    over the observation window (None when fewer than two snapshots exist).
    A lag flag needs a strong momentum reading with a flat or opposing market.
    """
    if market_move is None:
        return {"status": "insufficient_market_history", "lag_detected": False}
    strong_trend = momentum_percentile >= 0.90 or momentum_percentile <= 0.10
    market_flat = abs(market_move) < 0.02
    market_opposed = (trend_momentum > 0 > market_move) or (trend_momentum < 0 < market_move)
    return {
        "status": "ok",
        "lag_detected": bool(strong_trend and (market_flat or market_opposed)),
        "trend_momentum": trend_momentum,
        "momentum_percentile": momentum_percentile,
        "market_move": market_move,
    }


@register_feature("trends")
def trends_snapshot(context: FeatureContext) -> dict[str, Any]:
    engine = TrendEngine(context.games)
    return {
        "league_baseline": round(engine.league_baseline, 6),
        "half_lives": list(HALF_LIVES),
        "teams": {team: asdict(trend) for team, trend in engine.all_trends().items()},
    }


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


# ---------------------------------------------------------------------------
# Versioned primitive used by the MLB Trend Engine.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendObservation:
    value: float
    opponent_adjustment: float = 0.0


@dataclass(frozen=True)
class TrendFeature:
    adjusted_recent_level: float
    baseline: float
    change_from_baseline: float
    effective_games: int


def opponent_adjusted_ewm_trend(
    observations: Iterable[TrendObservation],
    baseline: float,
    half_life_games: float,
    prior_strength_games: float = 12,
) -> TrendFeature:
    """Return a recent level shrunk toward a pre-game baseline.

    Observations must be ordered oldest to newest and contain only information
    available before the prediction timestamp.
    """
    values = list(observations)
    if half_life_games <= 0 or prior_strength_games < 0:
        raise ValueError("half life must be positive and prior strength non-negative")
    if not values:
        return TrendFeature(baseline, baseline, 0.0, 0)
    decay = exp(-log(2) / half_life_games)
    weights = [decay ** (len(values) - 1 - index) for index in range(len(values))]
    adjusted = [item.value + item.opponent_adjustment for item in values]
    recent = sum(weight * value for weight, value in zip(weights, adjusted, strict=True)) / sum(weights)
    shrinkage = len(values) / (len(values) + prior_strength_games)
    level = shrinkage * recent + (1 - shrinkage) * baseline
    return TrendFeature(level, baseline, level - baseline, len(values))
