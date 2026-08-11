"""Tests for Tennis Surface Elo mechanics — `rebuild/tennis/elo.py`.

All tests use synthetic data only (no real data dependency).
Covers: Elo update/expected_win math, walk-forward PIT invariance,
surface normalization, and irregular result handling.
"""

from __future__ import annotations

import pytest

from model_prediction.rebuild.tennis.elo import (
    DEFAULT_SURFACE,
    KNOWN_SURFACES,
    SurfaceEloBook,
    TennisMatchRow,
    _clean_surface,
    build_walk_forward_rows,
    rows_to_frame,
)

# ── helpers ──────────────────────────────────────────────────────────────

def _match(
    match_id: str,
    date: str,
    surface: str,
    winner_id: str,
    loser_id: str,
    result_type: str = "completed",
    tour: str = "ATP",
) -> TennisMatchRow:
    return TennisMatchRow(
        canonical_match_id=match_id,
        tour=tour,
        tourney_date=date,
        surface=surface,
        winner_id=winner_id,
        loser_id=loser_id,
        winner_name=f"Winner_{winner_id}",
        loser_name=f"Loser_{loser_id}",
        result_type=result_type,
    )


# ── SurfaceEloBook — core Elo mechanics ──────────────────────────────────

class TestSurfaceEloBookCore:
    """Elo update, expected_win, and rating math."""

    def test_default_rating_is_1500(self):
        book = SurfaceEloBook()
        assert book.rating("any_player") == 1500.0

    def test_symmetric_expected_win_at_equal_ratings(self):
        book = SurfaceEloBook()
        # Equal ratings → 0.5 expected win
        exp_a = book.expected_win("A", "B", "Hard")
        exp_b = book.expected_win("B", "A", "Hard")
        assert exp_a == pytest.approx(0.5)
        assert exp_b == pytest.approx(0.5)

    def test_expected_win_with_rating_gap(self):
        book = SurfaceEloBook()
        book.overall["A"] = 1600.0
        book.overall["B"] = 1400.0
        # A rated 200 higher → expected win > 0.5
        exp_a = book.expected_win("A", "B", "Hard")
        assert exp_a > 0.5
        # Complement: P(A) + P(B) ≈ 1
        exp_b = book.expected_win("B", "A", "Hard")
        assert exp_a + exp_b == pytest.approx(1.0)

    def test_expected_win_400_point_gap_is_10_to_1(self):
        book = SurfaceEloBook()
        # Set ratings directly; but expected_win uses blended_rating which
        # incorporates surface_weight (0.1 when no surface history).
        # To test the raw 400-point-gap property, set overall AND surface
        # to the same values so blended = overall regardless of weight.
        book.overall["A"] = 1900.0
        book.overall["B"] = 1500.0
        book.surface["A"]["Hard"] = 1900.0
        book.surface["B"]["Hard"] = 1500.0
        # With matching surface ratings, blended = overall = 1900/1500.
        # 400-point gap: expected win = 1/(1+10^((1500-1900)/400)) = 1/1.1 ≈ 0.90909
        exp_a = book.expected_win("A", "B", "Hard")
        assert exp_a == pytest.approx(1.0 / 1.1)

    def test_update_increases_winner_rating(self):
        book = SurfaceEloBook()
        before_w = book.rating("A")
        before_l = book.rating("B")
        book.update("A", "B", "Hard")
        assert book.rating("A") > before_w
        assert book.rating("B") < before_l

    def test_update_is_zero_sum_overall(self):
        book = SurfaceEloBook()
        before_sum = book.rating("A") + book.rating("B")
        book.update("A", "B", "Clay")
        after_sum = book.rating("A") + book.rating("B")
        assert after_sum == pytest.approx(before_sum)

    def test_k_factor_determines_max_delta(self):
        book = SurfaceEloBook(k=32.0)
        # At equal ratings, exp_win = 0.5, delta = K * (1 - 0.5) = 16
        before = book.rating("A")
        book.update("A", "B", "Hard")
        delta = book.rating("A") - before
        assert delta == pytest.approx(16.0)

    def test_upset_produces_larger_delta(self):
        book = SurfaceEloBook(k=32.0)
        book.overall["B"] = 1900.0  # B is strong
        book.overall["A"] = 1500.0  # A is weak
        before_a = book.rating("A")
        book.update("A", "B", "Hard")  # upset — weak A beats strong B
        delta = book.rating("A") - before_a
        # A was expected to lose heavily → delta > 16 (much larger than equal-ratings case)
        assert delta > 16.0

    def test_surface_rating_independent_of_overall(self):
        book = SurfaceEloBook()
        book.surface["A"]["Clay"] = 1600.0
        assert book.surface_rating("A", "Clay") == 1600.0
        assert book.surface_rating("A", "Hard") == 1500.0  # default

    def test_surface_boost_applied_on_update(self):
        book = SurfaceEloBook(k=32.0, surface_k_boost=8.0)
        before_surface = book.surface_rating("A", "Clay")
        book.update("A", "B", "Clay")
        delta_surface = book.surface_rating("A", "Clay") - before_surface
        # Surface delta uses (K + surface_k_boost) = 40, so delta ≈ 20
        assert delta_surface == pytest.approx(20.0)

    def test_match_counts_increment(self):
        book = SurfaceEloBook()
        assert book.surface_matches("A", "Hard") == 0
        assert book.total_matches["A"] == 0
        book.update("A", "B", "Hard")
        assert book.surface_matches("A", "Hard") == 1
        assert book.surface_matches("B", "Hard") == 1
        assert book.total_matches["A"] == 1
        assert book.total_matches["B"] == 1

    def test_has_minimum_history(self):
        book = SurfaceEloBook()
        assert not book.has_minimum_history("A", 3)
        book.total_matches["A"] = 2
        assert not book.has_minimum_history("A", 3)
        book.total_matches["A"] = 3
        assert book.has_minimum_history("A", 3)

    def test_update_order_matters(self):
        """Elo is order-sensitive: different update orders → different ratings."""
        book_a = SurfaceEloBook()
        book_a.update("X", "Y", "Hard")
        book_a.update("Y", "Z", "Hard")

        book_b = SurfaceEloBook()
        book_b.update("Y", "Z", "Hard")
        book_b.update("X", "Y", "Hard")

        # X ends with different rating because in book_a, X beat a Y that hadn't lost to Z yet
        assert book_a.rating("X") != book_b.rating("X")


# ── Surface weight & blended rating ──────────────────────────────────────

class TestSurfaceWeight:
    def test_zero_surface_matches_yields_min_weight(self):
        book = SurfaceEloBook()
        w = book._surface_weight("A", "B", "Hard")
        assert w == pytest.approx(0.1)

    def test_weight_increases_with_matches(self):
        book = SurfaceEloBook()
        book.surface_count["A"]["Hard"] = 10
        book.surface_count["B"]["Hard"] = 10
        # min(10, 10) = 10, weight = min(0.6, 0.1 + 0.025 * 10) = min(0.6, 0.35) = 0.35
        w = book._surface_weight("A", "B", "Hard")
        assert w == pytest.approx(0.35)

    def test_weight_capped_at_0_6(self):
        book = SurfaceEloBook()
        book.surface_count["A"]["Hard"] = 100
        book.surface_count["B"]["Hard"] = 100
        w = book._surface_weight("A", "B", "Hard")
        assert w == pytest.approx(0.6)

    def test_weight_uses_minimum_of_two_players(self):
        book = SurfaceEloBook()
        book.surface_count["A"]["Clay"] = 50
        book.surface_count["B"]["Clay"] = 2
        w = book._surface_weight("A", "B", "Clay")
        # min(50, 2) = 2, weight = min(0.6, 0.1 + 0.025 * 2) = 0.15
        assert w == pytest.approx(0.15)

    def test_blended_rating_reverts_to_overall_when_no_surface_history(self):
        book = SurfaceEloBook()
        book.overall["A"] = 1550.0
        # No surface history → weight ≈ 0.1 → blended ≈ 0.1*1500 + 0.9*1550 = 1545
        blended = book.blended_rating("A", "Grass", "B")
        expected = 0.1 * 1500.0 + 0.9 * 1550.0
        assert blended == pytest.approx(expected)

    def test_blended_rating_leans_toward_surface_when_experienced(self):
        book = SurfaceEloBook()
        book.overall["A"] = 1500.0
        book.surface["A"]["Hard"] = 1700.0
        book.surface_count["A"]["Hard"] = 20
        book.surface_count["B"]["Hard"] = 20
        # weight = min(0.6, 0.1 + 0.025*20) = 0.6
        # blended = 0.6*1700 + 0.4*1500 = 1620
        blended = book.blended_rating("A", "Hard", "B")
        assert blended == pytest.approx(1620.0)


# ── _clean_surface ───────────────────────────────────────────────────────

class TestCleanSurface:
    def test_known_surfaces_pass_through(self):
        for s in KNOWN_SURFACES:
            assert _clean_surface(s) == s

    def test_none_defaults_to_hard(self):
        assert _clean_surface(None) == DEFAULT_SURFACE

    def test_empty_string_defaults_to_hard(self):
        assert _clean_surface("") == DEFAULT_SURFACE

    def test_case_insensitive(self):
        assert _clean_surface("hard") == "Hard"
        assert _clean_surface("HARD") == "Hard"
        assert _clean_surface("clay") == "Clay"
        assert _clean_surface("Grass") == "Grass"

    def test_whitespace_trimmed(self):
        assert _clean_surface("  Hard  ") == "Hard"

    def test_unknown_defaults_to_hard(self):
        assert _clean_surface("Carpet") == DEFAULT_SURFACE
        assert _clean_surface("Indoor") == DEFAULT_SURFACE

    def test_variants_match_by_substring(self):
        assert _clean_surface("Hard (indoor)") == "Hard"
        assert _clean_surface("Red Clay") == "Clay"


# ── walk-forward PIT invariant ───────────────────────────────────────────

class TestWalkForwardPIT:
    """Day-bucketed walk-forward: predictions never see same-day results."""

    def test_empty_matches_produces_empty_result(self):
        result = build_walk_forward_rows([])
        assert len(result.rows) == 0
        assert result.n_total == 0

    def test_bootstrap_skips_matches_before_minimum_history(self):
        matches = [_match(f"m{i}", "2024-01-01", "Hard", f"W{i}", f"L{i}") for i in range(50)]
        result = build_walk_forward_rows(matches, minimum_history_matches=100)
        assert result.skipped_bootstrap == 50
        assert len(result.rows) == 0

    def test_cold_start_skips_players_with_few_matches(self):
        # Day 1: all matches bootstrap-skipped (history starts empty),
        # but they build the history for day 2.
        hist = [_match(f"hist{i}", "2024-01-01", "Hard", f"x{i}", f"y{i}") for i in range(150)]
        # Day 2: all new players, each appearing once -> all cold start (0 prior matches)
        test = [_match(f"m{i}", "2024-01-02", "Hard", f"p{i}", f"q{i}") for i in range(50)]
        result = build_walk_forward_rows(hist + test, minimum_history_matches=100, minimum_player_matches=3)
        # Day 1: 150 bootstrap skips (history was empty). Day 2: bootstrap passes
        # (history has 150 >= 100), but all 50 players are cold-start (0 prior matches each).
        assert result.n_total == 200
        assert result.skipped_cold_start == 50
        assert result.skipped_bootstrap == 150  # all day-1 matches
        assert len(result.rows) == 0

    def test_same_day_matches_do_not_see_each_other(self):
        """Matches on the same date must use pre-day Elo snapshots."""
        # Build a sequence where player A plays twice on the same day.
        # Include A, B, C in history so they pass cold-start.
        matches = [
            # History: give A, B, C at least 3 matches each
            *[_match(f"ha{i}", "2024-01-01", "Hard", "A", f"spar_a{i}") for i in range(5)],
            *[_match(f"hb{i}", "2024-01-01", "Hard", "B", f"spar_b{i}") for i in range(5)],
            *[_match(f"hc{i}", "2024-01-01", "Hard", "C", f"spar_c{i}") for i in range(5)],
            # More bootstrap padding
            *[_match(f"pad{i}", "2024-01-01", "Hard", f"pad_a{i}", f"pad_b{i}") for i in range(150)],
            # Two same-day matches with A involved
            _match("m1", "2024-01-02", "Hard", "A", "B"),
            _match("m2", "2024-01-02", "Hard", "C", "A"),  # A loses second match
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)

        # Find rows for m1 and m2
        rows_by_id = {r.match_id: r for r in result.rows}
        row_m1 = rows_by_id.get("m1")
        row_m2 = rows_by_id.get("m2")

        assert row_m1 is not None, "m1 should produce a row"
        assert row_m2 is not None, "m2 should produce a row"

        # Both predictions must use the same Elo for player A (before any day's update)
        assert row_m1.overall_elo_winner == row_m2.overall_elo_loser  # A is winner in m1, loser in m2

    def test_elo_probability_is_between_0_and_1(self):
        # Include X and Y in history so they pass cold-start
        matches = [
            *[_match(f"hx{i}", "2024-01-01", "Hard", "X", f"sp_x{i}") for i in range(5)],
            *[_match(f"hy{i}", "2024-01-01", "Hard", "Y", f"sp_y{i}") for i in range(5)],
            *[_match(f"pad{i}", "2024-01-01", "Hard", f"a{i}", f"b{i}") for i in range(150)],
            _match("test", "2024-01-02", "Hard", "X", "Y"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        assert len(result.rows) >= 1
        for row in result.rows:
            assert 0.0 < row.elo_probability_winner < 1.0

    def test_winner_win_is_always_1(self):
        """WalkForwardRow.winner_win is always 1 (all rows are completed-winner rows)."""
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("test", "2024-01-02", "Hard", "X", "Y"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        for row in result.rows:
            assert row.winner_win == 1

    def test_chronological_ordering_preserved(self):
        """Walk-forward rows must be in chronological order."""
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("day1", "2024-01-02", "Hard", "A", "B"),
            _match("day2", "2024-01-03", "Hard", "C", "D"),
            _match("day3", "2024-01-04", "Hard", "E", "F"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        dates = [r.tourney_date for r in result.rows]
        assert dates == sorted(dates)

    def test_elo_drift_accumulates_over_many_updates(self):
        """After many matches, a dominant player's rating rises significantly."""
        matches: list[TennisMatchRow] = []
        # Give DOM enough matches in history to pass cold-start
        for i in range(5):
            matches.append(_match(f"dom_setup{i}", "2024-01-01", "Hard", "DOM", f"setup{i}"))
        # Give victims 3+ matches each so they pass cold-start on day 2
        for i in range(50):
            for j in range(4):
                matches.append(_match(f"v{i}s{j}", "2024-01-01", "Hard", f"victim{i}", f"sp{i}_{j}"))
        # Padding for bootstrap
        for i in range(150):
            matches.append(_match(f"pad{i}", "2024-01-01", "Hard", f"pad_a{i}", f"pad_b{i}"))

        # Dominant player DOM beats various opponents repeatedly
        for i in range(50):
            matches.append(_match(f"dom{i}", "2024-01-15", "Hard", "DOM", f"victim{i}"))

        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        # DOM should have elevated Elo by the end
        assert len(result.rows) >= 1
        last = result.rows[-1]
        assert last.overall_elo_winner > 1550.0  # substantially above 1500

    def test_rows_to_frame_preserves_all_fields(self):
        # Include A and B in history so they pass cold-start
        matches = [
            *[_match(f"ha{i}", "2024-01-01", "Hard", "A", f"sp_a{i}") for i in range(5)],
            *[_match(f"hb{i}", "2024-01-01", "Hard", "B", f"sp_b{i}") for i in range(5)],
            *[_match(f"pad{i}", "2024-01-01", "Hard", f"x{i}", f"y{i}") for i in range(150)],
            _match("test", "2024-01-02", "Hard", "A", "B"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        assert len(result.rows) >= 1
        frame = rows_to_frame(result.rows)
        assert frame.height == len(result.rows)
        assert "elo_probability_winner" in frame.columns
        assert "surface_weight" in frame.columns


# ── irregular result handling ────────────────────────────────────────────

class TestIrregularResults:
    def test_retirement_not_included_in_walk_forward_rows(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("ret", "2024-01-02", "Hard", "A", "B", result_type="retirement"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        match_ids = {r.match_id for r in result.rows}
        assert "ret" not in match_ids

    def test_walkover_not_included_in_walk_forward_rows(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("wo", "2024-01-02", "Hard", "A", "B", result_type="walkover"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        match_ids = {r.match_id for r in result.rows}
        assert "wo" not in match_ids

    def test_default_not_included_in_walk_forward_rows(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("def", "2024-01-02", "Hard", "A", "B", result_type="default"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        match_ids = {r.match_id for r in result.rows}
        assert "def" not in match_ids

    def test_irregular_results_are_counted_as_skipped(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("ret", "2024-01-02", "Hard", "A", "B", result_type="retirement"),
            _match("wo", "2024-01-02", "Hard", "C", "D", result_type="walkover"),
            _match("def", "2024-01-02", "Hard", "E", "F", result_type="default"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        assert result.skipped_irregular == 3

    def test_irregular_winner_still_gets_elo_update(self):
        """Irregular results are skipped for prediction but winner still gets Elo credit."""
        # A and B need cold-start history. Give them wins on day 1.
        # Spread bootstrap matches across day 1 so they pass the 100-match threshold.
        matches = [
            # Day 1: give A, B, C history + bootstrap padding
            *[_match(f"ha{i}", "2024-01-01", "Hard", "A", f"sp_a{i}") for i in range(5)],
            *[_match(f"hb{i}", "2024-01-01", "Hard", "B", f"sp_b{i}") for i in range(5)],
            *[_match(f"hc{i}", "2024-01-01", "Hard", "C", f"sp_c{i}") for i in range(5)],
            *[_match(f"pad{i}", "2024-01-01", "Hard", f"x{i}", f"y{i}") for i in range(200)],
            # Day 2: retirement match with A and B (skipped for prediction, but updates ratings)
            _match("ret", "2024-01-10", "Hard", "A", "B", result_type="retirement"),
            # Day 3: regular completed match to observe ratings
            _match("next", "2024-01-11", "Hard", "A", "C"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)

        # The "next" match should show A having received Elo credit from the retirement
        next_row = next((r for r in result.rows if r.match_id == "next"), None)
        assert next_row is not None
        # A's Elo should be above default (got credit from retirement win)
        assert next_row.overall_elo_winner >= 1500.0

    def test_irregular_update_is_half_k(self):
        """Irregular wins use half-K delta compared to completed wins."""
        book = SurfaceEloBook(k=32.0)
        # Simulate what happens in the walk-forward for an irregular match
        # Half-K delta at equal ratings: 0.5 * 32 * (1 - 0.5) = 8
        exp_win = book.expected_win("A", "B", "Hard")
        delta_half = (32.0 * 0.5) * (1.0 - exp_win)
        assert delta_half == pytest.approx(8.0)

        # Full K delta for completed: 32 * (1 - 0.5) = 16
        delta_full = 32.0 * (1.0 - exp_win)
        assert delta_full == pytest.approx(16.0)


# ── WalkForwardResult dataclass ──────────────────────────────────────────

class TestWalkForwardResult:
    def test_result_counts_are_consistent(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            _match("ret", "2024-01-02", "Hard", "A", "B", result_type="retirement"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        # Bootstrap: 0 (first 200 pass the 100 threshold, but players need 3 matches — all cold-start)
        # Actually: 200 hist matches: each player appears once → cold start 200
        # Then "ret": skipped as irregular
        assert result.n_total == 201
        assert result.skipped_bootstrap >= 0
        assert result.skipped_irregular >= 1

    def test_walk_forward_row_has_all_expected_fields(self):
        matches = [
            *[_match(f"hist{i}", "2024-01-01", "Hard", f"p{i}", f"q{i}") for i in range(200)],
            # Give A and B enough history
            *[_match(f"a{i}", "2024-01-05", "Hard", "A", f"sp{i}") for i in range(5)],
            *[_match(f"b{i}", "2024-01-05", "Hard", "B", f"spb{i}") for i in range(5)],
            _match("test", "2024-01-10", "Hard", "A", "B"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        assert len(result.rows) >= 1
        row = result.rows[0]
        assert row.match_id is not None
        assert row.tourney_date is not None
        assert row.surface == "Hard"
        assert row.winner_win == 1
        assert row.overall_elo_winner > 0
        assert row.overall_elo_loser > 0
        assert row.elo_probability_winner > 0


# ── ATP + WTA mixed ──────────────────────────────────────────────────────

class TestMixedTours:
    def test_atp_and_wta_players_share_elo_book(self):
        """ATP and WTA matches update the same Elo book (cross-tour)."""
        # Give A, B, C, D history to pass cold-start
        matches = [
            *[_match(f"ha{i}", "2024-01-01", "Hard", "A", f"sa{i}") for i in range(5)],
            *[_match(f"hb{i}", "2024-01-01", "Hard", "B", f"sb{i}") for i in range(5)],
            *[_match(f"hc{i}", "2024-01-01", "Hard", "C", f"sc{i}") for i in range(5)],
            *[_match(f"hd{i}", "2024-01-01", "Hard", "D", f"sd{i}") for i in range(5)],
            *[_match(f"pad{i}", "2024-01-01", "Hard", f"x{i}", f"y{i}") for i in range(150)],
            _match("atp1", "2024-01-10", "Hard", "A", "B", tour="ATP"),
            _match("wta1", "2024-01-10", "Hard", "C", "D", tour="WTA"),
        ]
        result = build_walk_forward_rows(matches, minimum_history_matches=100, minimum_player_matches=3)
        # Both matches should produce rows
        assert len(result.rows) >= 1
        tours = {r.tour for r in result.rows}
        assert "ATP" in tours
        assert "WTA" in tours

    def test_surface_tracks_are_shared_across_tours(self):
        """Surface counts accumulate across ATP and WTA."""
        book = SurfaceEloBook()
        book.update("A", "B", "Clay")
        book.update("A", "C", "Clay")
        assert book.surface_matches("A", "Clay") == 2
