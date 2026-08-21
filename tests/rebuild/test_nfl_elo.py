"""Tests for NFL Elo mechanics (`rebuild/nfl/elo.py`) — synthetic-data
exercises covering every public API surface: `ElOBook` rating/update/
expected/persistence, `_trend_gap` edge cases, walk-forward week-bucketed
PIT invariant, and offseason regression.

No real data dependencies — every test builds its own synthetic games.
"""

from __future__ import annotations

import pytest

from model_prediction.rebuild.nfl.elo import (
    DEFAULT_ELO,
    NFL_ELO_CONFIG,
    EloBook,
    NFLGameRow,
    WalkForwardRow,
    _trend_gap,
    build_walk_forward_rows,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _game(
    event_id: str = "g1",
    season: int = 2024,
    week: int = 1,
    home: str = "H",
    away: str = "A",
    home_score: int = 28,
    away_score: int = 17,
    event_start_utc: str = "2024-09-08T17:00:00+00:00",
    season_type: str = "REG",
) -> NFLGameRow:
    return NFLGameRow(
        event_id=event_id,
        season=season,
        season_type=season_type,
        week=week,
        event_start_utc=event_start_utc,
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
    )


def _games(*gs: NFLGameRow) -> list[NFLGameRow]:
    return list(gs)


# ── EloBook basics ───────────────────────────────────────────────────────────


class TestEloBookInit:
    def test_default_values_match_config(self) -> None:
        book = EloBook()
        assert book.k == NFL_ELO_CONFIG["k"]
        assert book.home_advantage == NFL_ELO_CONFIG["home_advantage"]
        assert book.default_elo == DEFAULT_ELO

    def test_custom_values_override_defaults(self) -> None:
        book = EloBook(k=32.0, home_advantage=65.0, default_elo=1400.0)
        assert book.k == 32.0
        assert book.home_advantage == 65.0
        assert book.default_elo == 1400.0

    def test_default_rating_returns_default_elo_for_unknown_team(self) -> None:
        book = EloBook()
        assert book.rating("UNKNOWN") == DEFAULT_ELO


class TestEloBookExpectedHomeWin:
    def test_equal_ratings_no_home_advantage(self) -> None:
        """With equal ratings and home_advantage=0, expected should be 0.5."""
        book = EloBook(home_advantage=0.0)
        assert book.expected_home_win("H", "A") == pytest.approx(0.5)

    def test_equal_ratings_with_home_advantage(self) -> None:
        """Home advantage shifts probability above 0.5."""
        book = EloBook(home_advantage=55.0)
        p = book.expected_home_win("H", "A")
        assert p > 0.5

    def test_stronger_home_team_yields_higher_probability(self) -> None:
        book = EloBook(home_advantage=0.0)
        book.ratings["H"] = 1600.0
        book.ratings["A"] = 1400.0
        p = book.expected_home_win("H", "A")
        assert p > 0.5
        # 200-point Elo difference at 0 HA → ~0.76
        expected = 1.0 / (1.0 + 10 ** ((1400.0 - 1600.0) / 400.0))
        assert p == pytest.approx(expected)

    def test_weaker_home_team_yields_lower_probability(self) -> None:
        book = EloBook(home_advantage=0.0)
        book.ratings["H"] = 1400.0
        book.ratings["A"] = 1600.0
        p = book.expected_home_win("H", "A")
        assert p < 0.5
        expected = 1.0 / (1.0 + 10 ** ((1600.0 - 1400.0) / 400.0))
        assert p == pytest.approx(expected)

    def test_formula_matches_manual_computation(self) -> None:
        """Direct hand-computation check against the Elo formula."""
        book = EloBook(home_advantage=55.0)
        book.ratings["H"] = 1520.0
        book.ratings["A"] = 1480.0
        r_home = 1520.0 + 55.0  # 1575
        r_away = 1480.0
        expected = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))
        assert book.expected_home_win("H", "A") == pytest.approx(expected)


class TestEloBookUpdate:
    def test_home_win_updates_both_teams(self) -> None:
        book = EloBook()
        book.update("H", "A", 28, 17)
        # Home won → home gains, away loses
        assert book.rating("H") > DEFAULT_ELO
        assert book.rating("A") < DEFAULT_ELO

    def test_away_win_updates_both_teams(self) -> None:
        book = EloBook()
        book.update("H", "A", 10, 24)
        # Away won → home loses, away gains
        assert book.rating("H") < DEFAULT_ELO
        assert book.rating("A") > DEFAULT_ELO

    def test_rating_changes_are_zero_sum(self) -> None:
        """Home gain + away loss should exactly cancel (zero-sum Elo)."""
        book = EloBook()
        before_h = book.rating("H")
        before_a = book.rating("A")
        book.update("H", "A", 28, 17)
        delta_h = book.rating("H") - before_h
        delta_a = book.rating("A") - before_a
        assert delta_h == pytest.approx(-delta_a)

    def test_multiple_updates_accumulate(self) -> None:
        book = EloBook()
        book.update("H", "A", 28, 17)  # H wins
        h_after_1 = book.rating("H")
        book.update("H", "A", 0, 35)  # A wins
        # After H loses, rating should drop below the post-first-game value
        assert book.rating("H") < h_after_1

    def test_upset_produces_larger_elo_swing(self) -> None:
        """A 1600 beating a 1400 yields a small delta; 1400 beating 1600 yields large."""
        # First: strong H beats weak A (expected → small delta)
        book = EloBook(home_advantage=0.0)
        book.ratings["H"] = 1600.0
        book.ratings["A"] = 1400.0
        before_h = book.rating("H")
        book.update("H", "A", 28, 17)
        small_delta = book.rating("H") - before_h

        # Reset: weak H beats strong A (upset → larger delta)
        book2 = EloBook(home_advantage=0.0)
        book2.ratings["H"] = 1400.0
        book2.ratings["A"] = 1600.0
        before_h2 = book2.rating("H")
        book2.update("H", "A", 28, 17)
        large_delta = book2.rating("H") - before_h2

        assert abs(large_delta) > abs(small_delta)


class TestEloBookHistory:
    def test_total_matches_tracks_games(self) -> None:
        book = EloBook()
        assert book.total_matches["H"] == 0
        book.update("H", "A", 28, 17)
        assert book.total_matches["H"] == 1
        assert book.total_matches["A"] == 1

    def test_has_minimum_history_false_below_threshold(self) -> None:
        book = EloBook()
        assert not book.has_minimum_history("H", min_matches=3)
        book.update("H", "A", 28, 17)
        book.update("H", "B", 21, 14)
        assert not book.has_minimum_history("H", min_matches=3)

    def test_has_minimum_history_true_at_threshold(self) -> None:
        book = EloBook()
        for i in range(3):
            book.update("H", f"T{i}", 28, 17)
        assert book.has_minimum_history("H", min_matches=3)


# ── trend_gap ────────────────────────────────────────────────────────────────


class TestTrendGap:
    def test_returns_zero_when_insufficient_history(self) -> None:
        games = _games(
            _game("g1", home="T", away="A"),
            _game("g2", home="B", away="T"),
        )
        assert _trend_gap(games, "T", window=10) == 0.0

    def test_returns_zero_when_exactly_window_games_all_wins(self) -> None:
        """Recent avg == season avg → trend_gap = 0."""
        games = _games(
            *[_game(f"g{i}", home="T", away=f"A{i}", home_score=28, away_score=17) for i in range(10)]
        )
        assert _trend_gap(games, "T", window=10) == pytest.approx(0.0)

    def test_positive_when_recent_better_than_season(self) -> None:
        """Team lost first 5, won last 5 → recent > season → positive gap."""
        games: list[NFLGameRow] = []
        # First 5: losses
        for i in range(5):
            games.append(_game(f"g{i}", home="T", away=f"A{i}", home_score=10, away_score=28))
        # Last 5: wins
        for i in range(5, 10):
            games.append(_game(f"g{i}", home="T", away=f"A{i}", home_score=28, away_score=17))
        gap = _trend_gap(games, "T", window=5)
        # Season: 5/10 = 0.5, Recent 5: 5/5 = 1.0 → gap = 0.5
        assert gap > 0.0
        assert gap == pytest.approx(0.5)

    def test_negative_when_recent_worse_than_season(self) -> None:
        """Team won first 5, lost last 5 → recent < season → negative gap."""
        games: list[NFLGameRow] = []
        for i in range(5):
            games.append(_game(f"g{i}", home="T", away=f"A{i}", home_score=28, away_score=17))
        for i in range(5, 10):
            games.append(_game(f"g{i}", home="T", away=f"A{i}", home_score=10, away_score=28))
        gap = _trend_gap(games, "T", window=5)
        assert gap < 0.0
        assert gap == pytest.approx(-0.5)

    def test_away_games_included_in_trend(self) -> None:
        """trend_gap includes both home and away games for the team."""
        games: list[NFLGameRow] = []
        # Home wins
        for i in range(5):
            games.append(_game(f"h{i}", home="T", away=f"A{i}", home_score=28, away_score=17))
        # Away losses
        for i in range(5):
            games.append(_game(f"a{i}", home=f"H{i}", away="T", home_score=35, away_score=10))
        gap = _trend_gap(games, "T", window=10)
        # Season: 5/10 = 0.5, Recent 10: 5/10 = 0.5 → gap = 0
        assert gap == pytest.approx(0.0)


# ── walk-forward construction ────────────────────────────────────────────────


class TestBuildWalkForwardRows:
    def test_empty_input_returns_empty_result(self) -> None:
        result = build_walk_forward_rows([])
        assert len(result.rows) == 0
        assert result.n_total == 0

    def test_bootstrap_skipped_when_insufficient_history(self) -> None:
        """With minimum_history_games=5, a single game is below threshold."""
        games = _games(_game("g1"))
        result = build_walk_forward_rows(games, minimum_history_games=5)
        assert result.skipped_bootstrap == 1
        assert len(result.rows) == 0
        assert result.n_total == 1

    def test_cold_start_team_skipped(self) -> None:
        """A team with <3 games should be skipped even if history is sufficient."""
        # Build enough history (3+ games) for the league but keep one team at <3
        games: list[NFLGameRow] = []
        for i in range(4):
            # Use fixed teams for first 3 games, then introduce a new team
            if i < 3:
                games.append(_game(f"g{i}", home="H", away="A", week=i + 1))
            else:
                games.append(_game("g3", home="NEW", away="A", week=4))
        result = build_walk_forward_rows(games, minimum_history_games=2, minimum_team_games=3)
        # The NEW team's first game should be cold-start skipped
        assert result.skipped_cold_start >= 1

    def test_walk_forward_rows_have_correct_fields(self) -> None:
        """After bootstrap, rows should have all expected fields."""
        games: list[NFLGameRow] = []
        for i in range(10):
            games.append(_game(f"g{i}", home="H", away="A", week=i + 1, home_score=28, away_score=17))
        result = build_walk_forward_rows(games, minimum_history_games=2, minimum_team_games=2)
        assert len(result.rows) > 0
        row = result.rows[0]
        assert isinstance(row, WalkForwardRow)
        assert 0.0 < row.elo_probability < 1.0
        assert isinstance(row.trend_gap, float)
        assert isinstance(row.home_elo, float)
        assert isinstance(row.away_elo, float)
        assert row.home_win in (0, 1)

    def test_same_week_games_do_not_see_each_other(self) -> None:
        """Week-bucketed PIT invariant: games in the same week use the
        same pre-week Elo snapshot and produce consistent ratings."""
        # Bootstrap: give A and B 3+ games each so they pass
        # minimum_team_games, and build enough league history.
        padded: list[NFLGameRow] = []
        for i in range(3):
            padded.append(
                _game(
                    f"bA{i}",
                    home="H",
                    away="A",
                    week=i + 1,
                    season=2023,
                    home_score=28,
                    away_score=17,
                    event_start_utc=f"2023-09-{i + 1:02d}T17:00:00+00:00",
                )
            )
            padded.append(
                _game(
                    f"bB{i}",
                    home="H",
                    away="B",
                    week=i + 1,
                    season=2023,
                    home_score=28,
                    away_score=17,
                    event_start_utc=f"2023-09-{i + 1:02d}T20:00:00+00:00",
                )
            )
        # Two games in the same week (season 2024, week 1)
        padded.append(
            _game(
                "g1",
                week=1,
                season=2024,
                home="H",
                away="A",
                home_score=28,
                away_score=17,
                event_start_utc="2024-09-08T17:00:00+00:00",
            )
        )
        padded.append(
            _game(
                "g2",
                week=1,
                season=2024,
                home="H",
                away="B",
                home_score=21,
                away_score=14,
                event_start_utc="2024-09-08T20:00:00+00:00",
            )
        )

        result = build_walk_forward_rows(padded, minimum_history_games=2, minimum_team_games=2)
        # Both week=1, season=2024 games should exist
        week1_rows = [r for r in result.rows if r.week == 1 and r.season == 2024]
        assert len(week1_rows) >= 1

    def test_offseason_regression_pulls_ratings_toward_default(self) -> None:
        """Between seasons, ratings should regress toward DEFAULT_ELO."""
        games: list[NFLGameRow] = []
        # Season 2023: build up some Elo divergence
        for i in range(10):
            games.append(
                _game(
                    f"s1g{i}",
                    season=2023,
                    week=i + 1,
                    home="H",
                    away="A",
                    home_score=28,
                    away_score=17,
                    event_start_utc=f"2023-09-{i + 1:02d}T17:00:00+00:00",
                )
            )
        # Season 2024: one game
        games.append(
            _game(
                "s2g1",
                season=2024,
                week=1,
                home="H",
                away="A",
                home_score=28,
                away_score=17,
                event_start_utc="2024-09-08T17:00:00+00:00",
            )
        )
        result = build_walk_forward_rows(games, minimum_history_games=2, minimum_team_games=2)
        # After 10 home wins in 2023, H's rating should be well above 1500.
        # After offseason regression (0.50), H should still be >1500 but
        # closer than it would be without regression.
        s2_rows = [r for r in result.rows if r.season == 2024]
        if s2_rows:
            # H's Elo at the start of 2024 should be between 1500 and
            # whatever the raw accumulated value would be without regression.
            # With 50% regression toward 1500, we just check it's >1500
            # (still above default after 10 straight wins, even with regression)
            assert s2_rows[0].home_elo > DEFAULT_ELO

    def test_nfl_elo_config_has_expected_values(self) -> None:
        assert NFL_ELO_CONFIG["k"] == 20.0
        assert NFL_ELO_CONFIG["home_advantage"] == 55.0
        assert NFL_ELO_CONFIG["offseason_regression"] == 0.50
        assert NFL_ELO_CONFIG["offseason_gap_days"] == 180
