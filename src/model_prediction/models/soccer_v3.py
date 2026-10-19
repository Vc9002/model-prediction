"""Versioned correction for league baseline normalization and fallback shrinkage.

The v2 factory remains available for replay. Paper serving does not establish
prospective profitability.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.trends import ewm_level
from ..soccer.league_model import LeagueSoccerConfig
from ..soccer.registry import named_league_codes, resolve
from .base import GamePrediction
from .soccer import SoccerModel, UpcomingMatch, soccer_model, soccer_model_v2


class SoccerModelV3(SoccerModel):
    def __init__(self) -> None:
        super().__init__(version="soccer-poisson-dc-v3", home_boost=1.18, rho=-0.05, divide_away=False)

    def _league_config(self, league_name: str, historical_baseline: float) -> LeagueSoccerConfig:
        config = resolve(league_name)
        if league_name not in named_league_codes():
            # Unknown competitions retain the existing shrinkage policy, but their
            # scoring scale comes from their own pre-event competition history.
            return replace(config, baseline=historical_baseline, global_baseline=historical_baseline)
        return config

    def _strengths(self, history: Sequence[GameRecord]) -> tuple[dict[str, dict[str, float]], float]:
        leagues = {game.league.strip().upper() for game in history}
        if len(leagues) != 1:
            raise ValueError("soccer_v3_requires_isolated_league_history")
        historical_baseline = sum(game.total for game in history) / (2 * len(history))
        if historical_baseline <= 0 and next(iter(leagues)) not in named_league_codes():
            raise ValueError("soccer_v3_requires_positive_league_goal_baseline")
        config = self._league_config(next(iter(leagues)), historical_baseline)
        by_team: dict[str, dict[str, list[float]]] = {}
        for game in sorted(history, key=lambda game: game.start):
            for team, scored, allowed in (
                (game.home_team, game.home_score, game.away_score),
                (game.away_team, game.away_score, game.home_score),
            ):
                entry = by_team.setdefault(team, {"scored": [], "allowed": []})
                entry["scored"].append(float(scored))
                entry["allowed"].append(float(allowed))
        strengths = {}
        for team, entry in by_team.items():
            count = len(entry["scored"])
            weight = count / (count + config.shrinkage_prior_games) if config.shrinkage_prior_games else 1.0
            values = {
                name: ewm_level(
                    entry[source], config.ewma_halflife, config.baseline, config.ewma_prior_strength
                )
                / config.baseline
                for name, source in (("attack", "scored"), ("defense", "allowed"))
            }
            strengths[team] = {name: weight * value + (1 - weight) for name, value in values.items()} | {
                "games": float(count)
            }
        return strengths, historical_baseline

    def predict_games(
        self, history: Sequence[GameRecord], upcoming: Sequence[UpcomingMatch]
    ) -> list[GamePrediction]:
        predictions = []
        self.skipped_predictions: list[dict[str, str]] = []
        for match in upcoming:
            league = match.league.strip().upper()
            if league in {"", "SOCCER", "UNKNOWN"}:
                raise ValueError("soccer_v3_requires_explicit_competition")
            start = parse_utc(match.event_start_utc)
            league_history = [
                game for game in history if game.league.strip().upper() == league and game.start < start
            ]
            if not league_history:
                # A missing league cannot silently borrow another competition's ratings.
                self.skipped_predictions.append({"event_id": match.event_id, "reason": "no_league_history"})
                continue
            try:
                predictions.extend(super().predict_games(league_history, [replace(match, league=league)]))
            except ValueError as error:
                if "implausible baseline" not in str(error) and "positive_league_goal_baseline" not in str(
                    error
                ):
                    raise
                # The shared config validator rejects implausible empirical scales.
                # Skip this competition explicitly rather than aborting other leagues
                # or silently restoring the inappropriate global scoring scale.
                self.skipped_predictions.append({"event_id": match.event_id, "reason": str(error)})
        return predictions


def soccer_model_v3() -> SoccerModelV3:
    return SoccerModelV3()


def soccer_model_for_version(version: str) -> SoccerModel:
    factories = {
        "soccer-poisson-dc-v1": soccer_model,
        "soccer-poisson-dc-v2": soccer_model_v2,
        "soccer-poisson-dc-v3": soccer_model_v3,
    }
    try:
        return factories[version]()
    except KeyError as error:
        raise ValueError(f"unsupported_soccer_model_version: {version}") from error
