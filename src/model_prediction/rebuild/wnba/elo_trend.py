"""WNBA Elo + trend construction, freshly fit from rebuild-owned data.

Builds the `elo_probability` / `trend_gap` / `defensive_trend_gap` feature
trio for `wnba-elo-trend-lr-rebuild-v1` -- an independently-trained sibling
of the incumbent `wnba-elo-trend-lr-v4`
(`docs/model_audit/models/WNBA_ELO_TREND_LR_V4.md`), same family (Elo +
trend, logistic regression), never loading the incumbent artifact or its
rating state (`docs/model_audit/ARCHITECTURE_CORRECTION.md`). The
incumbent's `ELO_CONFIG["wnba"]` values (`k=20.0`, `home_advantage=60.0`,
`offseason_regression=0.40`) are used only as a documented reference
starting point, not copied state -- every rating here is recomputed from
`data/rebuild/normalized/wnba/{games,team_box}`.

Why this does NOT reuse `features.py`/`horizon_builder.py`'s PIT gate
(even though task instructions point at those recovered modules): their
`eligible_prior_team_games` filters on `observed_at_utc <= decision_time`,
and every row in this single-vintage SportsDataverse backfill carries the
same `observed_at_utc` (this repo's real 2026-08-xx backfill capture
time) regardless of the game's own real historical date
(`docs/model_audit/models/WNBA_REBUILD_DATA_FOUNDATION.md` section 3,
independently reproduced by
`tests/rebuild/test_wnba_features.py::test_real_backfilled_2024_data_produces_a_real_available_snapshot`).
That means `observed_at_utc <= decision_time` is false for *any*
decision_time set inside a historical season -- the gate can never pass,
so it cannot be used to build genuine per-game walk-forward training rows
from this data. This module instead orders strictly on each game's real
`event_start_utc`/`sports_event_date` (real historical fact -- game order
and final scores are real, only the *capture* timestamp is not
retrospective), which is exactly the documented, honest way this data can
be used: "team form ... ordered by real historical event_start_utc", not
"the model would have seen exactly this at the time" -- see the training
script and model card for the caveat stated in full.

PIT-safety mechanism (mirrors
`docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`'s audited methodology
exactly): games are grouped into calendar-day buckets by
`sports_event_date` (WNBA's own Eastern slate-date convention,
`.time.sports_event_date`). A day's Elo/trend snapshot is always taken
from `history` (games strictly on *prior* days) before that day's own
games are appended to `history` -- "snapshot, then extend", never
inverted. Every produced row also carries `last_home_update_utc`/
`last_away_update_utc` (the latest real `event_start_utc` folded into that
team's rating so far) so a direct leakage test can assert
`last_*_update_utc < event_start_utc` for every row, the same invariant
`outputs/rebuild/audit/elo_leakage_trace.py` checked for NBA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import exp, log
from typing import Any

import polars as pl

from .store import WNBANormalizedStore

# Reference starting point only (see module docstring) -- freshly refit,
# not copied incumbent rating state.
WNBA_ELO_CONFIG: dict[str, float] = {
    "k": 20.0,
    "home_advantage": 60.0,
    "offseason_regression": 0.40,
    "offseason_gap_days": 90,
}
DEFAULT_ELO = 1500.0

# Same half-life pair used by the incumbent's trend_gap/defensive_trend_gap
# (short vs. long half-life momentum; see features/trends.py's
# HALF_LIVES = (3.0, 10.0, 25.0), of which trend_gap/defensive_trend_gap
# use only the hl3/hl25 endpoints) -- reimplemented here, not imported,
# since that module operates on the incumbent's own GameRecord/FeatureStore
# abstraction over a different on-disk data model.
TREND_HALF_LIVES = (3.0, 25.0)
TREND_PRIOR_STRENGTH_GAMES = 12.0


def expected_win_probability(rating_a: float, rating_b: float, advantage: float = 0.0) -> float:
    """The one shared Elo logistic formula (same shape as
    features/elo_ratings.py::expected_win_probability, reimplemented so
    this module has zero import dependency on incumbent serving code)."""
    return 1.0 / (1.0 + 10.0 ** (-(rating_a + advantage - rating_b) / 400.0))


@dataclass
class EloBook:
    ratings: dict[str, float] = field(default_factory=dict)
    k: float = WNBA_ELO_CONFIG["k"]
    home_advantage: float = WNBA_ELO_CONFIG["home_advantage"]

    def rating(self, team: str) -> float:
        return self.ratings.get(team, DEFAULT_ELO)

    def expected_home_win(self, home: str, away: str) -> float:
        return expected_win_probability(self.rating(home), self.rating(away), self.home_advantage)

    def update(self, home: str, away: str, home_score: float, away_score: float) -> None:
        """Margin-of-victory-scaled update (538-style log scaling,
        autocorrelation damped) -- same shape as
        features/elo_ratings.py::EloBook.update, reimplemented locally."""
        expected = self.expected_home_win(home, away)
        if home_score > away_score:
            outcome = 1.0
        elif home_score < away_score:
            outcome = 0.0
        else:
            outcome = 0.5  # WNBA has no ties; defensive only, never expected to fire
        margin = abs(home_score - away_score)
        rating_gap = self.rating(home) + self.home_advantage - self.rating(away)
        winner_gap = rating_gap if outcome >= 0.5 else -rating_gap
        multiplier = log(max(margin, 1) + 1) * (2.2 / (winner_gap * 0.001 + 2.2))
        delta = self.k * multiplier * (outcome - expected)
        self.ratings[home] = self.rating(home) + delta
        self.ratings[away] = self.rating(away) - delta

    def regress_to_mean(self, fraction: float) -> None:
        """Pull every known team's rating `fraction` of the way back to
        DEFAULT_ELO -- fires once per detected offseason gap, before the
        next season's first snapshot is taken (see build_walk_forward_rows),
        never after a game already used that snapshot."""
        if fraction <= 0 or not self.ratings:
            return
        for team in list(self.ratings):
            self.ratings[team] = DEFAULT_ELO * fraction + self.ratings[team] * (1.0 - fraction)


@dataclass(frozen=True)
class WNBAGameRow:
    """One completed, real WNBA game -- home_score/away_score come from
    `games.home_score`/`away_score` (SportsDataverse schedule feed), not
    fabricated or joined from team_box (both exist; games already carries
    final scores directly)."""

    event_id: str
    event_start_utc: str
    sports_event_date: str
    season: int
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int


def load_completed_games(store: WNBANormalizedStore, seasons: list[int]) -> list[WNBAGameRow]:
    """Real completed games for `seasons`, deduped to the latest observed
    vintage per event (store.read_latest), sorted chronologically. Ties and
    null scores are dropped and counted rather than silently coerced --
    WNBA has no regular-season ties, so any tie found here would be a real
    data anomaly worth surfacing, not fed into Elo as a genuine 0.5 draw."""
    rows: list[WNBAGameRow] = []
    dropped_incomplete = 0
    dropped_ties = 0
    for season in seasons:
        frame = store.read_latest("games", season)
        if frame.is_empty():
            continue
        completed = frame.filter(pl.col("completed"))
        for row in completed.iter_rows(named=True):
            home_score, away_score = row.get("home_score"), row.get("away_score")
            if home_score is None or away_score is None:
                dropped_incomplete += 1
                continue
            if home_score == away_score:
                dropped_ties += 1
                continue
            rows.append(
                WNBAGameRow(
                    event_id=str(row["event_id"]),
                    event_start_utc=str(row["event_start_utc"]),
                    sports_event_date=str(row["sports_event_date"]),
                    season=int(row["season"]),
                    home_team_id=str(row["home_team_id"]),
                    away_team_id=str(row["away_team_id"]),
                    home_score=int(home_score),
                    away_score=int(away_score),
                )
            )
    rows.sort(key=lambda g: (g.event_start_utc, g.event_id))
    if dropped_incomplete or dropped_ties:
        # Real, disclosed data-quality counts -- callers (training script,
        # model card) surface these rather than silently losing rows.
        load_completed_games.last_drop_counts = {  # type: ignore[attr-defined]
            "dropped_incomplete": dropped_incomplete,
            "dropped_ties": dropped_ties,
        }
    else:
        load_completed_games.last_drop_counts = {"dropped_incomplete": 0, "dropped_ties": 0}  # type: ignore[attr-defined]
    return rows


def ewm_level(values: list[float], half_life: float, baseline: float, prior: float) -> float:
    """Exponentially weighted level shrunk toward a baseline -- identical
    formula to features/trends.py::ewm_level, reimplemented locally (see
    module docstring for why this file doesn't import that one)."""
    if half_life <= 0:
        raise ValueError("half life must be positive")
    if not values:
        return baseline
    decay = exp(-log(2) / half_life)
    weights = [decay ** (len(values) - 1 - index) for index in range(len(values))]
    recent = sum(w * v for w, v in zip(weights, values, strict=True)) / sum(weights)
    shrinkage = len(values) / (len(values) + prior)
    return shrinkage * recent + (1 - shrinkage) * baseline


@dataclass(frozen=True)
class TeamForm:
    games_played: int
    offensive_momentum: float
    defensive_momentum: float


def _team_form_snapshot(history: list[WNBAGameRow]) -> dict[str, TeamForm]:
    """Opponent-adjusted EWMA offensive/defensive momentum per team, built
    fresh from `history` (already strictly-prior games) -- same shape as
    features/trends.py::TrendEngine.team_trend, reimplemented against this
    module's own WNBAGameRow instead of the incumbent's GameRecord."""
    by_team: dict[str, list[dict[str, float | str]]] = {}
    league_points: list[float] = []
    for g in history:
        league_points.extend([float(g.home_score), float(g.away_score)])
        by_team.setdefault(g.home_team_id, []).append(
            {"opponent": g.away_team_id, "scored": float(g.home_score), "allowed": float(g.away_score)}
        )
        by_team.setdefault(g.away_team_id, []).append(
            {"opponent": g.home_team_id, "scored": float(g.away_score), "allowed": float(g.home_score)}
        )
    if not league_points:
        return {}
    baseline = sum(league_points) / len(league_points)

    def simple_offense(team: str) -> float:
        rows = by_team.get(team, [])
        return sum(float(r["scored"]) for r in rows) / len(rows) if rows else baseline

    def simple_defense(team: str) -> float:
        rows = by_team.get(team, [])
        return sum(float(r["allowed"]) for r in rows) / len(rows) if rows else baseline

    result: dict[str, TeamForm] = {}
    for team, rows in by_team.items():
        adjusted_scored: list[float] = []
        adjusted_allowed: list[float] = []
        for row in rows:
            opponent = str(row["opponent"])
            opponent_defense = simple_defense(opponent)
            opponent_offense = simple_offense(opponent)
            defense_ratio = opponent_defense / baseline if baseline > 0 else 1.0
            offense_ratio = opponent_offense / baseline if baseline > 0 else 1.0
            adjusted_scored.append(float(row["scored"]) / max(defense_ratio, 1e-6))
            adjusted_allowed.append(float(row["allowed"]) / max(offense_ratio, 1e-6))
        off_short = ewm_level(adjusted_scored, TREND_HALF_LIVES[0], baseline, TREND_PRIOR_STRENGTH_GAMES)
        off_long = ewm_level(adjusted_scored, TREND_HALF_LIVES[1], baseline, TREND_PRIOR_STRENGTH_GAMES)
        def_short = ewm_level(adjusted_allowed, TREND_HALF_LIVES[0], baseline, TREND_PRIOR_STRENGTH_GAMES)
        def_long = ewm_level(adjusted_allowed, TREND_HALF_LIVES[1], baseline, TREND_PRIOR_STRENGTH_GAMES)
        result[team] = TeamForm(
            games_played=len(rows),
            offensive_momentum=off_short - off_long,
            # long - short: a team allowing FEWER points recently than its
            # own longer baseline has a positive (improving) defensive
            # momentum -- same sign convention as trends.py's
            # defensive_momentum.
            defensive_momentum=def_long - def_short,
        )
    return result


@dataclass(frozen=True)
class WalkForwardRow:
    event_id: str
    event_start_utc: str
    sports_event_date: str
    season: int
    home_team_id: str
    away_team_id: str
    home_win: int
    elo_probability: float
    trend_gap: float
    defensive_trend_gap: float
    home_elo_rating: float
    away_elo_rating: float
    home_games_played: int
    away_games_played: int
    last_home_update_utc: str | None
    last_away_update_utc: str | None


@dataclass(frozen=True)
class WalkForwardResult:
    rows: list[WalkForwardRow]
    skipped_bootstrap: int  # history shorter than minimum_history_games
    skipped_cold_start_team: int  # a participant team below minimum_team_games


def build_walk_forward_rows(
    games: list[WNBAGameRow],
    *,
    minimum_history_games: int = 30,
    minimum_team_games: int = 3,
) -> WalkForwardResult:
    """Day-bucketed walk-forward Elo + trend snapshot, mirroring
    `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`'s audited methodology
    exactly: for each WNBA slate day (`sports_event_date`), every game on
    that day is scored from a snapshot built strictly from `history` (prior
    days only); only after every game on the day has been scored does that
    day's games get folded into `history` for future days. No game can ever
    see its own result or any same-day game's result.

    `minimum_history_games`/`minimum_team_games` are a priori bootstrap
    floors (not tuned against validation/holdout) -- an early-season row
    where either team has fewer than `minimum_team_games` real prior games
    is skipped rather than fed a near-default, uninformative snapshot as if
    it were a real signal.
    """
    by_date: dict[str, list[WNBAGameRow]] = {}
    for g in games:
        by_date.setdefault(g.sports_event_date, []).append(g)

    history: list[WNBAGameRow] = []
    last_update: dict[str, str] = {}
    book = EloBook()
    rows: list[WalkForwardRow] = []
    last_processed_date: date | None = None
    skipped_bootstrap = 0
    skipped_cold_start_team = 0

    for day in sorted(by_date):
        day_date = date.fromisoformat(day)
        if last_processed_date is not None:
            gap_days = (day_date - last_processed_date).days
            if gap_days > WNBA_ELO_CONFIG["offseason_gap_days"]:
                book.regress_to_mean(WNBA_ELO_CONFIG["offseason_regression"])

        day_games = sorted(by_date[day], key=lambda g: (g.event_start_utc, g.event_id))

        if len(history) >= minimum_history_games:
            trend = _team_form_snapshot(history)
            for g in day_games:
                home_form = trend.get(g.home_team_id)
                away_form = trend.get(g.away_team_id)
                home_played = home_form.games_played if home_form else 0
                away_played = away_form.games_played if away_form else 0
                if home_played < minimum_team_games or away_played < minimum_team_games:
                    skipped_cold_start_team += 1
                    continue
                home_om = home_form.offensive_momentum if home_form else 0.0
                away_om = away_form.offensive_momentum if away_form else 0.0
                home_dm = home_form.defensive_momentum if home_form else 0.0
                away_dm = away_form.defensive_momentum if away_form else 0.0
                rows.append(
                    WalkForwardRow(
                        event_id=g.event_id,
                        event_start_utc=g.event_start_utc,
                        sports_event_date=g.sports_event_date,
                        season=g.season,
                        home_team_id=g.home_team_id,
                        away_team_id=g.away_team_id,
                        home_win=1 if g.home_score > g.away_score else 0,
                        elo_probability=book.expected_home_win(g.home_team_id, g.away_team_id),
                        trend_gap=home_om - away_om,
                        defensive_trend_gap=home_dm - away_dm,
                        home_elo_rating=book.rating(g.home_team_id),
                        away_elo_rating=book.rating(g.away_team_id),
                        home_games_played=home_played,
                        away_games_played=away_played,
                        last_home_update_utc=last_update.get(g.home_team_id),
                        last_away_update_utc=last_update.get(g.away_team_id),
                    )
                )
        else:
            skipped_bootstrap += len(day_games)

        for g in day_games:
            book.update(g.home_team_id, g.away_team_id, g.home_score, g.away_score)
            last_update[g.home_team_id] = max(
                last_update.get(g.home_team_id, g.event_start_utc), g.event_start_utc
            )
            last_update[g.away_team_id] = max(
                last_update.get(g.away_team_id, g.event_start_utc), g.event_start_utc
            )

        history.extend(day_games)
        last_processed_date = day_date

    return WalkForwardResult(
        rows=rows, skipped_bootstrap=skipped_bootstrap, skipped_cold_start_team=skipped_cold_start_team
    )


def build_dataset(data_root: str, seasons: list[int], **kwargs: Any) -> WalkForwardResult:
    """Convenience entrypoint: real backfilled games for `seasons` under
    `data_root` -> walk-forward rows. `**kwargs` forwards to
    build_walk_forward_rows (minimum_history_games/minimum_team_games)."""
    store = WNBANormalizedStore(f"{data_root}/normalized")
    games = load_completed_games(store, seasons)
    return build_walk_forward_rows(games, **kwargs)


def rows_to_frame(rows: list[WalkForwardRow]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame([r.__dict__ for r in rows])
