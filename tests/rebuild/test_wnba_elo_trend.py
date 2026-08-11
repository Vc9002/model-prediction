"""Tests for the freshly-built WNBA Elo + trend construction
(`rebuild/wnba/elo_trend.py`), the model-family foundation for
`wnba-elo-trend-lr-rebuild-v1`.

Covers: EloBook mechanics (symmetric zero-sum update, cold-start default,
offseason regression), the day-bucketed walk-forward loop's PIT invariant
(mirroring `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`'s audited
methodology -- a rating/trend snapshot used to predict game G must never
incorporate G's own result or any same-day game's result), and a real-data
smoke test against the actual 2022-2025 backfill (skipped honestly if that
data isn't present in this environment, matching
`tests/rebuild/test_wnba_features.py`'s existing convention).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from model_prediction.rebuild.wnba.elo_trend import (
    DEFAULT_ELO,
    WNBA_ELO_CONFIG,
    EloBook,
    WNBAGameRow,
    build_dataset,
    build_walk_forward_rows,
    expected_win_probability,
    load_completed_games,
)
from model_prediction.rebuild.wnba.store import WNBANormalizedStore

# ── EloBook mechanics ────────────────────────────────────────────────────


def test_cold_start_returns_default_rating() -> None:
    book = EloBook()
    assert book.rating("never-seen-team") == DEFAULT_ELO
    # A game between two unseen teams is priced from home_advantage alone.
    expected = expected_win_probability(DEFAULT_ELO, DEFAULT_ELO, WNBA_ELO_CONFIG["home_advantage"])
    assert book.expected_home_win("A", "B") == pytest.approx(expected)
    assert book.expected_home_win("A", "B") > 0.5  # home advantage favors home


def test_update_is_zero_sum() -> None:
    book = EloBook()
    home_before, away_before = book.rating("A"), book.rating("B")
    book.update("A", "B", home_score=80, away_score=70)
    home_after, away_after = book.rating("A"), book.rating("B")
    assert (home_after - home_before) == pytest.approx(-(away_after - away_before))
    assert home_after > home_before  # home won, rating rises


def test_upset_moves_rating_more_than_a_expected_result() -> None:
    """A big underdog win should move ratings by more than a favorite win of
    the same margin -- the whole point of an Elo update (proportional to
    surprise, not just to margin)."""
    favorite_wins = EloBook()
    favorite_wins.ratings["A"] = 1700.0
    favorite_wins.ratings["B"] = 1300.0
    favorite_wins.update("A", "B", home_score=85, away_score=75)
    favorite_delta = favorite_wins.rating("A") - 1700.0

    upset = EloBook()
    upset.ratings["A"] = 1300.0
    upset.ratings["B"] = 1700.0
    upset.update("A", "B", home_score=85, away_score=75)
    upset_delta = upset.rating("A") - 1300.0

    assert upset_delta > favorite_delta > 0


def test_offseason_regression_pulls_toward_default() -> None:
    book = EloBook()
    book.ratings["A"] = 1700.0
    book.ratings["B"] = 1300.0
    book.regress_to_mean(0.40)
    assert book.rating("A") == pytest.approx(1700.0 * 0.60 + DEFAULT_ELO * 0.40)
    assert book.rating("B") == pytest.approx(1300.0 * 0.60 + DEFAULT_ELO * 0.40)
    # Regression narrows the gap but does not erase it.
    assert book.rating("A") > book.rating("B")


def test_regress_to_mean_is_a_no_op_for_zero_fraction() -> None:
    book = EloBook()
    book.ratings["A"] = 1700.0
    book.regress_to_mean(0.0)
    assert book.rating("A") == 1700.0


# ── Walk-forward PIT invariant ──────────────────────────────────────────


def _game(
    event_id: str, day: str, home: str, away: str, home_score: int, away_score: int, season: int = 2024,
) -> WNBAGameRow:
    return WNBAGameRow(
        event_id=event_id,
        event_start_utc=f"{day}T23:00:00+00:00",
        sports_event_date=day,
        season=season,
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
    )


def _synthetic_season(num_teams: int = 6, games_per_pair: int = 6) -> list[WNBAGameRow]:
    """A real round-robin-ish synthetic schedule, deterministic scores, many
    games per calendar day (to genuinely exercise same-day exclusion) and
    enough volume to clear the default minimum_history_games/team_games
    floors."""
    teams = [f"T{i}" for i in range(num_teams)]
    games: list[WNBAGameRow] = []
    day_counter = 0
    event_counter = 0
    for _round in range(games_per_pair):
        day = date(2024, 5, 1)
        day = day.fromordinal(day.toordinal() + day_counter)
        day_str = day.isoformat()
        for i in range(0, num_teams, 2):
            home, away = teams[i], teams[i + 1]
            # Deterministic, non-tied scores that vary by round/team so Elo
            # actually moves.
            home_score = 70 + (event_counter % 13)
            away_score = 65 + ((event_counter * 3) % 11)
            if home_score == away_score:
                away_score += 1
            games.append(_game(f"g{event_counter}", day_str, home, away, home_score, away_score))
            event_counter += 1
        day_counter += 2  # two real days apart, several games sharing one day
    return games


def test_walk_forward_never_leaks_same_day_or_future_results() -> None:
    games = _synthetic_season(num_teams=8, games_per_pair=10)
    result = build_walk_forward_rows(games, minimum_history_games=5, minimum_team_games=1)
    assert result.rows, "synthetic dataset should produce at least some rows past the bootstrap floor"

    for row in result.rows:
        event_start = datetime.fromisoformat(row.event_start_utc)
        if row.last_home_update_utc is not None:
            assert datetime.fromisoformat(row.last_home_update_utc) < event_start, (
                f"home rating for {row.event_id} incorporates a game not strictly before its own start"
            )
        if row.last_away_update_utc is not None:
            assert datetime.fromisoformat(row.last_away_update_utc) < event_start


def test_walk_forward_same_day_games_do_not_see_each_other() -> None:
    """Two games on the identical WNBA slate day: neither's Elo snapshot may
    reflect the other's result, even though within-python-list order might
    otherwise suggest one was "processed first"."""
    day = "2024-06-01"
    bootstrap = _synthetic_season(num_teams=8, games_per_pair=8)
    same_day_1 = _game("same_day_1", day, "T0", "T1", 90, 60)  # blowout, would move ratings a lot if leaked
    same_day_2 = _game("same_day_2", day, "T2", "T3", 70, 69)
    games = [*bootstrap, same_day_1, same_day_2]
    result = build_walk_forward_rows(games, minimum_history_games=5, minimum_team_games=1)

    rows_by_id = {r.event_id: r for r in result.rows}
    assert "same_day_1" in rows_by_id and "same_day_2" in rows_by_id
    row1, row2 = rows_by_id["same_day_1"], rows_by_id["same_day_2"]
    # Neither row's snapshot can have a last_update timestamp ON this same day.
    for row in (row1, row2):
        for ts in (row.last_home_update_utc, row.last_away_update_utc):
            if ts is not None:
                assert ts[:10] < day


def test_bootstrap_and_cold_start_gates_skip_rows_rather_than_faking_them() -> None:
    games = _synthetic_season(num_teams=6, games_per_pair=3)
    result = build_walk_forward_rows(games, minimum_history_games=1000, minimum_team_games=1)
    # An impossibly high history floor means every row is skipped as bootstrap.
    assert result.rows == []
    assert result.skipped_bootstrap == len(games)


def test_cold_start_team_gate_skips_until_minimum_games_played() -> None:
    games = _synthetic_season(num_teams=8, games_per_pair=8)
    lenient = build_walk_forward_rows(games, minimum_history_games=5, minimum_team_games=1)
    strict = build_walk_forward_rows(games, minimum_history_games=5, minimum_team_games=5)
    assert len(strict.rows) < len(lenient.rows)
    assert strict.skipped_cold_start_team > 0


def test_home_advantage_produces_directionally_sane_probabilities() -> None:
    """Sanity, not a claim of real predictive power: a team on a big real
    winning streak should end up favored over one on a big losing streak,
    with all else equal."""
    games: list[WNBAGameRow] = []
    event_counter = 0
    for round_idx in range(15):
        day = date(2024, 5, 1)
        day = day.fromordinal(day.toordinal() + round_idx * 2)
        day_str = day.isoformat()
        # Strong: always beats Weak by a lot. Filler teams keep the pool warm.
        games.append(_game(f"g{event_counter}", day_str, "Strong", "Filler1", 100, 60))
        event_counter += 1
        games.append(_game(f"g{event_counter}", day_str, "Filler2", "Weak", 90, 55))
        event_counter += 1
    games.append(_game(f"g{event_counter}", "2024-08-01", "Strong", "Weak", 80, 75))

    result = build_walk_forward_rows(games, minimum_history_games=5, minimum_team_games=3)
    final_row = next(r for r in result.rows if r.home_team_id == "Strong" and r.away_team_id == "Weak")
    assert final_row.elo_probability > 0.5


# ── Data loading ─────────────────────────────────────────────────────────


def test_load_completed_games_drops_incomplete_and_ties(tmp_path) -> None:
    import polars as pl

    store = WNBANormalizedStore(tmp_path)
    frame = pl.DataFrame({
        "event_id": ["a", "b", "c"],
        "season": [2024, 2024, 2024],
        "event_start_utc": [
            "2024-05-01T00:00:00+00:00",
            "2024-05-02T00:00:00+00:00",
            "2024-05-03T00:00:00+00:00",
        ],
        "sports_event_date": ["2024-04-30", "2024-05-01", "2024-05-02"],
        "home_team_id": ["T0", "T1", "T2"],
        "away_team_id": ["T1", "T2", "T3"],
        "home_score": [80, 70, None],
        "away_score": [75, 70, 60],  # b is a tie, c has a null score
        "completed": [True, True, True],
        "observed_at_utc": ["2026-08-11T00:00:00+00:00"] * 3,
        "raw_snapshot_hash": ["h1", "h2", "h3"],
        "retrieved_at_utc": ["2026-08-11T00:00:00+00:00"] * 3,
        "availability_basis": ["capture_time_only"] * 3,
        "commercial_use_status": ["unresolved"] * 3,
        "production_allowed": [False] * 3,
        "pit_eligible": [True] * 3,
        "home_team_canonical_id": ["c0", "c1", "c2"],
        "away_team_canonical_id": ["c1", "c2", "c3"],
        "event_canonical_id": ["e1", "e2", "e3"],
        "status": ["STATUS_FINAL"] * 3,
    })
    store.write("games", 2024, frame)

    rows = load_completed_games(store, [2024])
    assert [r.event_id for r in rows] == ["a"]
    assert load_completed_games.last_drop_counts == {"dropped_incomplete": 1, "dropped_ties": 1}  # type: ignore[attr-defined]


# ── Real backfilled data ─────────────────────────────────────────────────


def test_real_backfilled_data_walk_forward_has_zero_pit_violations() -> None:
    """Direct real-data equivalent of
    docs/model_audit/models/NBA_ELO_TREND_LR_V4.md's leakage trace: run the
    actual day-bucketed walk-forward loop against the real 2022-2025
    backfilled WNBA games and assert the PIT invariant holds for every
    single produced row, not just a synthetic example."""
    try:
        result = build_dataset("data/rebuild", [2022, 2023, 2024, 2025])
    except FileNotFoundError:
        pytest.skip("real WNBA rebuild data not backfilled in this environment")
    if not result.rows:
        pytest.skip("real WNBA rebuild data not backfilled in this environment")

    assert len(result.rows) > 500  # sanity: this should be the full 4-season dataset, not a stub

    violations = []
    for row in result.rows:
        event_start = datetime.fromisoformat(row.event_start_utc)
        if row.last_home_update_utc is not None and not (
            datetime.fromisoformat(row.last_home_update_utc) < event_start
        ):
            violations.append((row.event_id, "home"))
        if row.last_away_update_utc is not None and not (
            datetime.fromisoformat(row.last_away_update_utc) < event_start
        ):
            violations.append((row.event_id, "away"))
    assert violations == []
