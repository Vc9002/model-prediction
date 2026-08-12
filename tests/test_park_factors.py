"""Point-in-time park factor tests: credibility shrinkage, convergence,
and date-aware filtering.
"""

from __future__ import annotations

from model_prediction.features.park_factors import (
    PARK_FACTORS_VERSION,
    compute_park_factors_from_games,
    park_factor,
    park_factor_at,
)


class _FakeGame:
    """Duck-typed game with the fields park_factor_at expects."""

    def __init__(
        self,
        home_team: str,
        home_score: int,
        away_score: int,
        event_start_utc: str,
    ) -> None:
        self.home_team = home_team
        self.home_score = home_score
        self.away_score = away_score
        self.event_start_utc = event_start_utc


# ── compute_park_factors_from_games ──────────────────────────────────────────


def test_empty_games_returns_empty_dict():
    assert compute_park_factors_from_games([]) == {}


def test_single_game_returns_pure_prior():
    """With one game (n=1) and prior_strength=30, the result is heavily
    shrunk toward 1.0 but NOT exactly 1.0 because there is a real
    observation.  The formula is PF = 1/31 * empirical + 30/31 * 1.0."""
    games = [
        _FakeGame("Team A", 10, 0, "2025-04-01T19:00:00Z"),
        # Lots of neutral games so the league average is low
        *[_FakeGame("Team X", 4, 4, "2025-04-01T19:00:00Z") for _ in range(100)],
    ]
    factors = compute_park_factors_from_games(games, prior_strength=30)
    # League avg total: (10 + 100*8) / 101 = 810/101 ≈ 8.02
    # Team A empirical: 10 / 8.02 ≈ 1.247 → shrunk: 1/31*1.247 + 30/31*1.0 = 1.008
    assert "Team A" in factors
    assert 0.99 < factors["Team A"] < 1.10
    # Team X with 100 games: empirical = 8/8.02 ≈ 0.998 → nearly 1.0
    assert "Team X" in factors
    assert 0.99 < factors["Team X"] < 1.01


def test_many_games_converges_to_empirical():
    """With many games (n >> k), the factor approaches the observed ratio."""
    games = [
        *[_FakeGame("Team A", 6, 6, "2025-04-01T19:00:00Z") for _ in range(500)],
        *[_FakeGame("Team X", 4, 4, "2025-04-01T19:00:00Z") for _ in range(500)],
    ]
    factors = compute_park_factors_from_games(games, prior_strength=30)
    # League avg total: (500*12 + 500*8) / 1000 = 10
    # Team A empirical: 12/10 = 1.2, shrunk with n=500, k=30 → ≈ 1.2
    # credibility = 500/530 ≈ 0.943, PF ≈ 0.943*1.2 + 0.057*1.0 ≈ 1.189
    assert abs(factors["Team A"] - 1.189) < 0.01
    # Team X empirical: 8/10 = 0.8, shrunk → 0.943*0.8 + 0.057*1.0 ≈ 0.811
    assert abs(factors["Team X"] - 0.811) < 0.01


def test_zero_games_for_team_returns_one():
    """When a team appears in games_data but has zero prior games
    (filtered out), park_factor_at returns 1.0 with unavailable status."""
    games = [_FakeGame("Team A", 6, 6, "2025-04-15T19:00:00Z")]
    result = park_factor_at("Team B", "2025-04-20", games_data=games)
    assert result["park_factor"] == 1.0
    assert result["status"] == "unavailable_from_source"


# ── park_factor_at PIT correctness ───────────────────────────────────────────


def test_pit_differs_from_static_early_season():
    """Early in a season the PIT park factor should differ from the
    full-season static table because it only sees prior games."""
    games = [
        # Team A: hitter's park — first game high-scoring
        _FakeGame("Team A", 8, 4, "2025-04-01T19:00:00Z"),
        # Many neutral games
        *[_FakeGame("Team X", 4, 4, f"2025-04-{day:02d}T19:00:00Z")
          for day in range(1, 15)],
    ]
    static = park_factor("Team A")
    pit = park_factor_at("Team A", "2025-04-02", games_data=games)
    # Static factor comes from the hardcoded PARK_RUN_FACTORS table,
    # which is based on 2024-2026 data.  The PIT factor only sees
    # one high-scoring game for Team A (April 1) before the target
    # date of April 2.  These should differ.
    assert pit["version"] == "point-in-time"
    assert static["version"] == PARK_FACTORS_VERSION
    # They won't be equal because they use different data windows
    assert pit["park_factor"] != static["park_factor"]


def test_pit_converges_toward_static_with_accumulated_games():
    """As more games accumulate in the PIT window, the factor should
    move in the same direction as the static table (higher for strong
    hitter's parks)."""
    # Build a team that is consistently a hitter's park across many games
    games = [
        *[_FakeGame("Rockies", 7, 5, f"2025-04-{day:02d}T19:00:00Z")
          for day in range(1, 30)],
        *[_FakeGame("Team X", 4, 4, f"2025-04-{day:02d}T19:00:00Z")
          for day in range(1, 30)],
    ]
    # With only 1 prior game, the factor is heavily shrunk (near 1.0)
    pit_early = park_factor_at("Rockies", "2025-04-03", games_data=games)
    # With 28 prior games, the factor reflects the actual run environment
    pit_late = park_factor_at("Rockies", "2025-04-30", games_data=games)
    # The late factor should be further from 1.0 (more extreme) than the
    # early factor because credibility increases with more data.
    assert abs(pit_late["park_factor"] - 1.0) >= abs(pit_early["park_factor"] - 1.0)


def test_pit_falls_back_to_static_when_games_data_is_none():
    """When games_data is omitted, park_factor_at must return the same
    value as park_factor()."""
    result = park_factor_at("Colorado Rockies", "2025-06-01")
    static = park_factor("Colorado Rockies")
    assert result == static


def test_pit_with_no_prior_games_returns_neutral():
    """When the target date is before any game, all parks get 1.0."""
    games = [_FakeGame("Team A", 6, 6, "2025-04-15T19:00:00Z")]
    result = park_factor_at("Team A", "2025-04-15", games_data=games)
    # No games before 2025-04-15 → no factors computed → unavailable
    assert result["park_factor"] == 1.0
    assert result["status"] == "unavailable_from_source"
    assert result["version"] == "point-in-time"


# ── credibility shrinkage formula ────────────────────────────────────────────


def test_credibility_shrinkage_zero_games_is_pure_prior():
    """n=0: PF = (0/(0+k))*observed + (k/(0+k))*1.0 = 1.0"""
    # park_factor_at with no prior games → pure prior
    games = [_FakeGame("Team A", 10, 0, "2025-04-02T19:00:00Z")]
    # Target date is April 2 — but the only Team A game is on April 2,
    # so there are zero Team A games before the target date.
    result = park_factor_at("Team A", "2025-04-02", games_data=games)
    assert result["park_factor"] == 1.0


def test_credibility_shrinkage_large_n_approaches_observed():
    """n >> k: PF ≈ observed, credibility ≈ 1.0"""
    games = [
        *_mk_games("Team A", 7, 5, 500),
        *_mk_games("Team X", 4, 4, 500),
    ]
    factors = compute_park_factors_from_games(games, prior_strength=30)
    # observed = 12/10 = 1.2; shrunk = 500/530 * 1.2 + 30/530 * 1.0 ≈ 1.189
    assert abs(factors["Team A"] - 1.189) < 0.01
    # n=500, k=30 → credibility = 500/530 ≈ 0.943, not quite 1.0
    # but the factor is close to the empirical 1.2
    assert 1.18 < factors["Team A"] < 1.21


def test_prior_strength_controls_shrinkage_rate():
    """A larger prior_strength shrinks harder toward 1.0."""
    games = [
        *_mk_games("Team A", 7, 5, 10),
        *_mk_games("Team X", 4, 4, 100),
    ]
    strong = compute_park_factors_from_games(games, prior_strength=100)
    weak = compute_park_factors_from_games(games, prior_strength=10)
    # With a stronger prior (k=100 vs k=10), Team A with only 10 games
    # should be pulled closer to 1.0
    assert abs(strong["Team A"] - 1.0) < abs(weak["Team A"] - 1.0)


def test_date_filtering_is_strictly_before():
    """Games on the target date are excluded."""
    games = [
        _FakeGame("Team A", 10, 0, "2025-04-15T19:00:00Z"),
    ]
    # Target date is April 15 — the game is April 15, not before → excluded
    result = park_factor_at("Team A", "2025-04-15", games_data=games)
    assert result["park_factor"] == 1.0
    assert result["status"] == "unavailable_from_source"

    # Target date is April 16 — April 15 game is before → included
    result2 = park_factor_at("Team A", "2025-04-16", games_data=games)
    assert result2["status"] == "available"


def test_undated_game_is_excluded_not_treated_as_prior():
    """An unresolvable (empty) date string must NOT be treated as "before"
    every ISO date. The empty string sorts before any real date, so an
    undated game used to leak its scores into every PIT window (real bug
    found 2026-08-13); it must be excluded from the prior set entirely."""
    games = [
        _FakeGame("Team A", 10, 0, "2025-04-01T19:00:00Z"),
        # Undated -- if treated as prior, its 100-run total would pull
        # Team A's factor far above 1.0.
        _FakeGame("Team A", 100, 0, ""),
        _FakeGame("Team X", 4, 4, "2025-04-01T19:00:00Z"),
    ]
    result = park_factor_at("Team A", "2025-05-01", games_data=games)
    expected = park_factor_at(
        "Team A", "2025-05-01", games_data=[games[0], games[2]]
    )
    assert result == expected


# ── helpers ──────────────────────────────────────────────────────────────────


def _mk_games(home_team: str, home_score: int, away_score: int, n: int):
    """Generate n identical _FakeGame instances for a team."""
    return [
        _FakeGame(home_team, home_score, away_score, "2025-04-01T19:00:00Z")
        for _ in range(n)
    ]
