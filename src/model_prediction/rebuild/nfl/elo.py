"""NFL Elo + trend construction — rebuild-native feature engine for `nfl-elo-trend-lr-rebuild-v1`.

Independently fit from rebuild-owned nflverse data (2021-2025, 1,424 games),
never loading the incumbent `nfl-elo-trend-lr-v4` artifact or its rating state.

Differences from the WNBA rebuild module:
- 2 features only: elo_probability + trend_gap (no defensive_trend_gap — audit
  confirmed it's unstable/noisy, and the NFL incumbent's ECE is poor enough that
  calibration, not feature complexity, is the priority)
- NFL-specific Elo config: k=20.0, home_advantage=55.0, offseason_regression=0.50
- Week-based, not day-based (NFL plays weekly, same-day leakage is less of a concern,
  but we still guard against same-week contamination)

PIT-safety: games are grouped by NFL week within season, ordered chronologically
by event_start_utc. A week's Elo/trend snapshot is taken from history (all prior
weeks) before that week's games are appended — "snapshot, then extend."
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

DEFAULT_ELO = 1500.0
NFL_ELO_CONFIG = {
    "k": 20.0,
    "home_advantage": 55.0,
    "offseason_regression": 0.50,
    "offseason_gap_days": 180,
}


@dataclass
class NFLGameRow:
    """One NFL game row from the normalized store."""

    event_id: str
    season: int
    season_type: str
    week: int
    event_start_utc: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    home_rest_days: int | None = None
    away_rest_days: int | None = None


@dataclass
class WalkForwardRow:
    """One walk-forward prediction row: Elo snapshot BEFORE game outcome."""

    event_id: str
    season: int
    season_type: str
    week: int
    event_start_utc: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    home_win: int  # 1 if home won
    elo_probability: float  # P(home wins) from Elo
    trend_gap: float  # home offensive momentum — away offensive momentum
    home_elo: float
    away_elo: float
    home_rest_days: int | None = None
    away_rest_days: int | None = None


@dataclass
class WalkForwardResult:
    rows: list[WalkForwardRow]
    skipped_bootstrap: int
    skipped_cold_start: int
    n_total: int


class EloBook:
    """Simplified Elo book for NFL — overall rating only, no surface tracks."""

    def __init__(
        self,
        k: float = NFL_ELO_CONFIG["k"],
        home_advantage: float = NFL_ELO_CONFIG["home_advantage"],
        default_elo: float = DEFAULT_ELO,
    ) -> None:
        self.k = k
        self.home_advantage = home_advantage
        self.default_elo = default_elo
        self.ratings: dict[str, float] = defaultdict(lambda: default_elo)
        self.total_matches: dict[str, int] = defaultdict(int)

    def rating(self, team: str) -> float:
        return self.ratings[team]

    def expected_home_win(self, home: str, away: str) -> float:
        r_home = self.ratings[home] + self.home_advantage
        r_away = self.ratings[away]
        return 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))

    def update(self, home: str, away: str, home_score: int, away_score: int) -> None:
        exp_home = self.expected_home_win(home, away)
        if home_score > away_score:
            delta = self.k * (1.0 - exp_home)
            self.ratings[home] += delta
            self.ratings[away] -= delta
        else:
            delta = self.k * (0.0 - exp_home)
            self.ratings[home] += delta
            self.ratings[away] -= delta
        self.total_matches[home] += 1
        self.total_matches[away] += 1

    def has_minimum_history(self, team: str, min_matches: int = 3) -> bool:
        return self.total_matches[team] >= min_matches


def _trend_gap(history: list[NFLGameRow], team: str, window: int = 10) -> float:
    """Simple rolling win-rate trend: win pct over last N games minus season avg.

    Returns a momentum score in [-1, 1] range. Positive = team is playing
    above their season average recently."""
    team_games = [g for g in history if g.home_team_id == team or g.away_team_id == team]
    if len(team_games) < window:
        return 0.0

    # Season average win rate
    season_wins = sum(
        1
        for g in team_games
        if (g.home_team_id == team and g.home_score > g.away_score)
        or (g.away_team_id == team and g.away_score > g.home_score)
    )
    season_avg = season_wins / len(team_games)

    # Recent win rate
    recent = team_games[-window:]
    recent_wins = sum(
        1
        for g in recent
        if (g.home_team_id == team and g.home_score > g.away_score)
        or (g.away_team_id == team and g.away_score > g.home_score)
    )
    recent_avg = recent_wins / window

    return recent_avg - season_avg


def load_games(data_root: str, seasons: list[int] | None = None) -> list[NFLGameRow]:
    """Load completed NFL games from the normalised store."""
    from model_prediction.rebuild.nfl.store import NFLNormalizedStore

    store = NFLNormalizedStore(data_root)
    all_rows: list[NFLGameRow] = []

    for season in seasons or [2021, 2022, 2023, 2024, 2025]:
        try:
            frame = store.read_season("games", season)
        except Exception as error:  # noqa: BLE001 — a missing/partial season file must skip, not abort the table build
            logger.warning("skipping NFL season %s: %s", season, error)
            continue

        if frame.is_empty():
            continue

        frame = frame.filter(pl.col("completed") == True)
        frame = frame.sort("event_start_utc")

        for row in frame.iter_rows(named=True):
            all_rows.append(
                NFLGameRow(
                    event_id=str(row["event_id"]),
                    season=int(row["season"]),
                    season_type=str(row["season_type"]),
                    week=int(row["week"]),
                    event_start_utc=str(row["event_start_utc"]),
                    home_team_id=str(row["home_team_id"]),
                    away_team_id=str(row["away_team_id"]),
                    home_score=int(row["home_score"]),
                    away_score=int(row["away_score"]),
                    home_rest_days=row.get("home_rest_days"),
                    away_rest_days=row.get("away_rest_days"),
                )
            )

    return all_rows


def build_walk_forward_rows(
    games: list[NFLGameRow],
    minimum_history_games: int = 50,
    minimum_team_games: int = 3,
) -> WalkForwardResult:
    """Build walk-forward prediction rows with week-bucketed Elo snapshots.

    For each NFL week (within season):
    1. Take an Elo/trend snapshot from history (all prior weeks)
    2. Predict this week's games using that snapshot
    3. Then update Elo with this week's results

    Prevents same-week contamination: games in the same NFL week do not
    see each other's results.
    """
    if not games:
        return WalkForwardResult(rows=[], skipped_bootstrap=0, skipped_cold_start=0, n_total=0)

    book = EloBook()
    history: list[NFLGameRow] = []

    # Group by season, then by week
    by_season_week: dict[tuple[int, int], list[NFLGameRow]] = defaultdict(list)
    for g in games:
        by_season_week[(g.season, g.week)].append(g)

    sorted_keys = sorted(by_season_week.keys())

    rows: list[WalkForwardRow] = []
    skipped_bootstrap = 0
    skipped_cold_start = 0

    # Track season boundaries for offseason regression
    last_season = -1

    for season, week in sorted_keys:
        week_games = by_season_week[(season, week)]

        # Offseason regression between seasons
        if season != last_season and last_season != -1:
            regress = NFL_ELO_CONFIG["offseason_regression"]
            for team in list(book.ratings.keys()):
                book.ratings[team] = book.ratings[team] * (1 - regress) + DEFAULT_ELO * regress

        last_season = season

        # Snapshot → predict → then update
        for g in sorted(week_games, key=lambda x: x.event_start_utc):
            if len(history) < minimum_history_games:
                skipped_bootstrap += 1
            elif not book.has_minimum_history(
                g.home_team_id, minimum_team_games
            ) or not book.has_minimum_history(g.away_team_id, minimum_team_games):
                skipped_cold_start += 1
            else:
                elo_prob = book.expected_home_win(g.home_team_id, g.away_team_id)
                home_trend = _trend_gap(history, g.home_team_id)
                away_trend = _trend_gap(history, g.away_team_id)

                rows.append(
                    WalkForwardRow(
                        event_id=g.event_id,
                        season=g.season,
                        season_type=g.season_type,
                        week=g.week,
                        event_start_utc=g.event_start_utc,
                        home_team_id=g.home_team_id,
                        away_team_id=g.away_team_id,
                        home_score=g.home_score,
                        away_score=g.away_score,
                        home_win=1 if g.home_score > g.away_score else 0,
                        elo_probability=elo_prob,
                        trend_gap=home_trend - away_trend,
                        home_elo=book.rating(g.home_team_id),
                        away_elo=book.rating(g.away_team_id),
                        home_rest_days=g.home_rest_days,
                        away_rest_days=g.away_rest_days,
                    )
                )

        # Now update Elo with this week's results
        for g in week_games:
            book.update(g.home_team_id, g.away_team_id, g.home_score, g.away_score)

        history.extend(week_games)

    return WalkForwardResult(
        rows=rows,
        skipped_bootstrap=skipped_bootstrap,
        skipped_cold_start=skipped_cold_start,
        n_total=len(games),
    )


def rows_to_frame(rows: list[WalkForwardRow]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame([r.__dict__ for r in rows])


def build_dataset(
    data_root: str = "data/rebuild",
    seasons: list[int] | None = None,
    **kwargs: Any,
) -> WalkForwardResult:
    """Convenience: load games from store, build walk-forward rows."""
    games = load_games(f"{data_root}/normalized", seasons=seasons)
    return build_walk_forward_rows(games, **kwargs)
