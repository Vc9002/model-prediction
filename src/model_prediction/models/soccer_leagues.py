# Per-league independent Dixon-Coles soccer model registry and fitting engine.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from .soccer_dixon_coles import (
    DixonColesEngine,
    DixonColesMatchPrediction,
    optimize_decay_xi,
)


class SoccerLeague(str, Enum):
    """Supported soccer league identifiers."""

    EPL = "EPL"
    LA_LIGA = "LA_LIGA"
    BUNDESLIGA = "BUNDESLIGA"
    SERIE_A = "SERIE_A"
    MLS = "MLS"
    UCL = "UCL"
    OTHER = "OTHER"


def _parse_date(date_str: str) -> datetime:
    """Parse date or ISO timestamp string into UTC datetime."""
    clean = date_str.replace("Z", "+00:00").split("T")[0]
    return datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=UTC)


def _is_strictly_before(record_date: str, as_of_date: str) -> bool:
    """Check if record_date is strictly before as_of_date."""
    if record_date == as_of_date:
        return False
    if ("T" in record_date or " " in record_date) and ("T" in as_of_date or " " in as_of_date):
        return record_date < as_of_date
    return _parse_date(record_date).date() < _parse_date(as_of_date).date()


@dataclass(slots=True)
class LeagueMatchRecord:
    """Historical match record assigned to a specific league."""

    match_id: str
    match_date: str  # YYYY-MM-DD or ISO timestamp
    league: SoccerLeague | str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int


@dataclass(slots=True)
class LeagueFittedArtifact:
    """Serialized parameters and metadata for a fitted league model."""

    league: str
    as_of_date: str
    matches_count: int
    teams_count: int
    optimal_xi: float
    attack_params: dict[str, float] = field(default_factory=dict)
    defense_params: dict[str, float] = field(default_factory=dict)
    home_advantage: float = 1.25
    rho: float = -0.05


class LeagueDixonColesRegistry:
    """Registry maintaining per-league match histories and independently fitted Dixon-Coles engines."""

    def __init__(self) -> None:
        self._matches: dict[str, list[LeagueMatchRecord]] = {}
        self._engines: dict[str, DixonColesEngine] = {}
        self._engine_as_of_dates: dict[str, str] = {}

    def record_match(self, match: LeagueMatchRecord) -> None:
        """Record a completed match into its league partition sequentially."""
        league_key = match.league.value if isinstance(match.league, SoccerLeague) else str(match.league)
        self._matches.setdefault(league_key, []).append(match)

    def record_matches(self, matches: Sequence[LeagueMatchRecord]) -> None:
        """Batch record matches into their respective leagues."""
        for m in matches:
            self.record_match(m)

    def fit_league(
        self,
        league: SoccerLeague | str,
        as_of_date: str,
        xi: float = 0.002,
        auto_tune_decay: bool = False,
    ) -> LeagueFittedArtifact:
        """Fit an independent Dixon-Coles parameter set for a specific league as of as_of_date."""
        league_key = league.value if isinstance(league, SoccerLeague) else str(league)
        all_matches = self._matches.get(league_key, [])
        valid_matches = [m for m in all_matches if _is_strictly_before(m.match_date, as_of_date)]

        if not valid_matches:
            engine = DixonColesEngine(xi=xi)
            self._engines[league_key] = engine
            self._engine_as_of_dates[league_key] = as_of_date
            return LeagueFittedArtifact(
                league=league_key,
                as_of_date=as_of_date,
                matches_count=0,
                teams_count=0,
                optimal_xi=xi,
            )

        match_dicts = [
            {
                "home_team": m.home_team,
                "away_team": m.away_team,
                "home_goals": m.home_goals,
                "away_goals": m.away_goals,
                "date": m.match_date,
            }
            for m in valid_matches
        ]

        optimal_xi = xi
        if auto_tune_decay and len(valid_matches) >= 30:
            optimal_xi, _ = optimize_decay_xi(match_dicts)

        engine = DixonColesEngine(xi=optimal_xi)
        engine.fit(match_dicts, t_now=as_of_date)
        self._engines[league_key] = engine
        self._engine_as_of_dates[league_key] = as_of_date

        unique_teams = set()
        for m in valid_matches:
            unique_teams.add(m.home_team)
            unique_teams.add(m.away_team)

        return LeagueFittedArtifact(
            league=league_key,
            as_of_date=as_of_date,
            matches_count=len(valid_matches),
            teams_count=len(unique_teams),
            optimal_xi=optimal_xi,
            attack_params=engine.attack_params,
            defense_params=engine.defense_params,
            home_advantage=engine.home_advantage,
            rho=engine.rho,
        )

    def forecast_match(
        self,
        league: SoccerLeague | str,
        home_team: str,
        away_team: str,
        as_of_date: str,
    ) -> DixonColesMatchPrediction:
        """Generate multi-market Dixon-Coles forecast using the league-specific fitted engine."""
        league_key = league.value if isinstance(league, SoccerLeague) else str(league)
        if league_key not in self._engines or self._engine_as_of_dates.get(league_key) != as_of_date:
            # Fit on demand strictly as-of as_of_date
            self.fit_league(league_key, as_of_date=as_of_date)

        engine = self._engines[league_key]
        return engine.predict_match(home_team, away_team)
