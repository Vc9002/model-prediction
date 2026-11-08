"""Unified Point-in-Time Feature Pipeline.

Guarantees 100% train/serve feature parity across all sports by routing both
offline historical validation and real-time execution through identical,
composable FeatureTransform stages gated by immutable TimeContext boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from model_prediction.core.time_context import TimeContext
from model_prediction.features.base import GameRecord


class LookaheadBiasError(ValueError):
    """Raised when a feature computation attempts to access post-decision data."""


@runtime_checkable
class FeatureTransform(Protocol):
    """Protocol for modular Point-in-Time feature generators."""

    @property
    def transform_name(self) -> str:
        """Unique identifier for this feature stage."""
        ...

    def compute(
        self,
        game: dict[str, Any] | GameRecord,
        context: TimeContext,
        observable_history: Sequence[GameRecord],
    ) -> dict[str, Any]:
        """Compute Point-in-Time features strictly observable before context.decision_time_utc."""
        ...


@dataclass(frozen=True)
class RestAndScheduleTransform:
    """Computes Point-in-Time rest days and schedule fatigue."""

    transform_name: str = "rest_and_schedule"
    max_rest_days: float = 7.0

    def compute(
        self,
        game: dict[str, Any] | GameRecord,
        context: TimeContext,
        observable_history: Sequence[GameRecord],
    ) -> dict[str, Any]:
        home_team = game.home_team if isinstance(game, GameRecord) else str(game.get("home_team", ""))
        away_team = game.away_team if isinstance(game, GameRecord) else str(game.get("away_team", ""))
        game_start_str = (
            game.event_start_utc if isinstance(game, GameRecord) else str(game.get("event_start_utc", ""))
        )

        try:
            target_start = datetime.fromisoformat(game_start_str)
        except (ValueError, TypeError):
            target_start = context.decision_time_utc

        # Find most recent game for home and away teams in observable history
        last_home_game: datetime | None = None
        last_away_game: datetime | None = None

        for rec in observable_history:
            # Enforce PIT constraint
            if not context.is_observable(rec.event_start_utc):
                continue
            rec_dt = rec.start
            if (rec.home_team == home_team or rec.away_team == home_team) and (
                last_home_game is None or rec_dt > last_home_game
            ):
                last_home_game = rec_dt
            if (rec.home_team == away_team or rec.away_team == away_team) and (
                last_away_game is None or rec_dt > last_away_game
            ):
                last_away_game = rec_dt

        home_rest = (
            min(self.max_rest_days, max(1.0, (target_start - last_home_game).total_seconds() / 86400.0))
            if last_home_game
            else self.max_rest_days
        )
        away_rest = (
            min(self.max_rest_days, max(1.0, (target_start - last_away_game).total_seconds() / 86400.0))
            if last_away_game
            else self.max_rest_days
        )

        return {
            "home_rest_days": round(home_rest, 2),
            "away_rest_days": round(away_rest, 2),
            "rest_advantage_home": round(home_rest - away_rest, 2),
            "is_home_b2b": bool(home_rest <= 1.2),
            "is_away_b2b": bool(away_rest <= 1.2),
        }


@dataclass(frozen=True)
class RollingEloTransform:
    """Computes Point-in-Time rolling Elo ratings and win expectancy."""

    transform_name: str = "rolling_elo"
    k_factor: float = 20.0
    initial_elo: float = 1500.0
    home_court_advantage: float = 65.0

    def compute(
        self,
        game: dict[str, Any] | GameRecord,
        context: TimeContext,
        observable_history: Sequence[GameRecord],
    ) -> dict[str, Any]:
        home_team = game.home_team if isinstance(game, GameRecord) else str(game.get("home_team", ""))
        away_team = game.away_team if isinstance(game, GameRecord) else str(game.get("away_team", ""))

        # Track rolling ratings
        ratings: dict[str, float] = {}

        # Replay strictly observable games chronologically
        sorted_history = sorted(
            [r for r in observable_history if context.is_observable(r.event_start_utc)],
            key=lambda r: r.start,
        )

        for rec in sorted_history:
            h_elo = ratings.get(rec.home_team, self.initial_elo)
            a_elo = ratings.get(rec.away_team, self.initial_elo)

            # Home advantage adjustment
            exp_home = 1.0 / (1.0 + 10.0 ** (-(h_elo + self.home_court_advantage - a_elo) / 400.0))
            actual_home = (
                1.0 if rec.home_score > rec.away_score else (0.5 if rec.home_score == rec.away_score else 0.0)
            )

            ratings[rec.home_team] = h_elo + self.k_factor * (actual_home - exp_home)
            ratings[rec.away_team] = a_elo + self.k_factor * ((1.0 - actual_home) - (1.0 - exp_home))

        curr_h = ratings.get(home_team, self.initial_elo)
        curr_a = ratings.get(away_team, self.initial_elo)
        elo_diff = (curr_h + self.home_court_advantage) - curr_a
        p_home_expected = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

        return {
            "home_elo": round(curr_h, 1),
            "away_elo": round(curr_a, 1),
            "elo_differential": round(elo_diff, 1),
            "p_home_elo": round(p_home_expected, 4),
            "p_away_elo": round(1.0 - p_home_expected, 4),
        }


@dataclass(frozen=True)
class RecentFormTransform:
    """Computes Point-in-Time trailing N-game scoring momentum and margins."""

    transform_name: str = "recent_form"
    window_games: int = 5

    def compute(
        self,
        game: dict[str, Any] | GameRecord,
        context: TimeContext,
        observable_history: Sequence[GameRecord],
    ) -> dict[str, Any]:
        home_team = game.home_team if isinstance(game, GameRecord) else str(game.get("home_team", ""))
        away_team = game.away_team if isinstance(game, GameRecord) else str(game.get("away_team", ""))

        valid_history = [r for r in observable_history if context.is_observable(r.event_start_utc)]
        sorted_history = sorted(valid_history, key=lambda r: r.start, reverse=True)

        home_games: list[GameRecord] = []
        away_games: list[GameRecord] = []

        for rec in sorted_history:
            if (rec.home_team == home_team or rec.away_team == home_team) and len(
                home_games
            ) < self.window_games:
                home_games.append(rec)
            if (rec.home_team == away_team or rec.away_team == away_team) and len(
                away_games
            ) < self.window_games:
                away_games.append(rec)
            if len(home_games) >= self.window_games and len(away_games) >= self.window_games:
                break

        def _stats(games: list[GameRecord], team: str) -> tuple[float, float, float]:
            if not games:
                return 0.50, 0.0, 0.0
            wins = 0.0
            scored = 0.0
            allowed = 0.0
            for g in games:
                is_home = g.home_team == team
                s = g.home_score if is_home else g.away_score
                a = g.away_score if is_home else g.home_score
                scored += s
                allowed += a
                if s > a:
                    wins += 1.0
                elif s == a:
                    wins += 0.5
            n = float(len(games))
            return round(wins / n, 3), round(scored / n, 1), round(allowed / n, 1)

        h_win_pct, h_ppg, h_papg = _stats(home_games, home_team)
        a_win_pct, a_ppg, a_papg = _stats(away_games, away_team)

        return {
            "home_form_win_pct": h_win_pct,
            "away_form_win_pct": a_win_pct,
            "home_form_ppg": h_ppg,
            "away_form_ppg": a_ppg,
            "home_form_net_rating": round(h_ppg - h_papg, 1),
            "away_form_net_rating": round(a_ppg - a_papg, 1),
            "net_rating_diff": round((h_ppg - h_papg) - (a_ppg - a_papg), 1),
        }


class UnifiedFeaturePipeline:
    """Master pipeline executing composed transforms under strict Point-in-Time discipline."""

    def __init__(self, transforms: Sequence[FeatureTransform] | None = None) -> None:
        self.transforms: list[FeatureTransform] = list(
            transforms
            or [
                RestAndScheduleTransform(),
                RollingEloTransform(),
                RecentFormTransform(),
            ]
        )

    def add_transform(self, transform: FeatureTransform) -> None:
        self.transforms.append(transform)

    def extract_features(
        self,
        game: dict[str, Any] | GameRecord,
        context: TimeContext,
        history: Sequence[GameRecord],
    ) -> dict[str, Any]:
        """Execute all transforms ensuring zero lookahead bias.

        Filters history strictly to records observable before context.decision_time_utc.
        """
        # Strict Point-in-Time filter
        observable_history = [r for r in history if context.is_observable(r.event_start_utc)]

        features: dict[str, Any] = {
            "decision_time_utc": context.decision_time_utc.isoformat(),
            "observable_history_count": len(observable_history),
        }

        for transform in self.transforms:
            res = transform.compute(game, context, observable_history)
            features.update(res)

        return features
