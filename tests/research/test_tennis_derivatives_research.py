"""Tests for tennis derivatives research module and game-score distribution.

Covers:
- ``set_game_distribution``: mass sums to 1, marginal equals
  ``set_win_probability``, tiebreak-direction handling, mirror symmetry.
- ``match_game_distribution``: mass sums to 1, match-win marginal equals
  ``forecast_match``, parity at equal strength, spread/total monotonicity.
- Moneyline-path consistency of the blended-rating bridge and the
  unrounded engine aggregation helper.
- Point-in-time pinning: prices for match T are identical whether or not
  matches after T exist in history (repo invariant #1).
- Ledger contract extraction: tier dedup and irregular-result exclusion.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from model_prediction.features.elo_ratings import expected_win_probability
from model_prediction.models.tennis import TennisModel
from model_prediction.models.tennis_markov import (
    TennisMarkovEngine,
    match_game_distribution,
    set_game_distribution,
    set_win_probability,
)
from model_prediction.tennis_derivatives_research import (
    MatchContext,
    _blended_elo_ratings,
    _engine_match_probability,
    _match_format,
    _normal_full_match,
    _read_settled_derivative_contracts,
    point_stats_for_ratings,
    price_contract,
)


def _stats(engine: TennisMarkovEngine, elo_a: float, elo_b: float, scale: float = 0.03):
    return point_stats_for_ratings(engine, elo_a, elo_b, "Hard", scale)


# ---------------------------------------------------------------- set-level


@pytest.mark.parametrize(
    ("p_hold_a", "p_hold_b", "p_tb_a"),
    [
        (0.65, 0.65, 0.5),
        (0.70, 0.60, 0.62),
        (0.55, 0.75, 0.20),
        (0.64, 0.66, 0.48),
    ],
)
def test_set_game_distribution_mass_and_marginal(p_hold_a, p_hold_b, p_tb_a):
    dist = set_game_distribution(p_hold_a, p_hold_b, p_tb_a)
    mass = sum(dist.values())
    assert mass == pytest.approx(1.0, abs=1e-12)
    p_a = sum(p for (ga, gb), p in dist.items() if ga > gb)
    p_sf, p_rf = set_win_probability(p_hold_a, p_hold_b, p_tb_a)
    assert p_a == pytest.approx(p_sf, abs=1e-12)
    # complementary marginal (B wins the set) must be the mirror of p_a
    p_b = sum(p for (ga, gb), p in dist.items() if ga < gb)
    assert p_a + p_b == pytest.approx(1.0, abs=1e-12)
    # engine-consistent role-swap identity: A's return-first orientation
    # equals the complement of B's serve-first orientation
    assert p_rf == pytest.approx(1.0 - set_win_probability(p_hold_b, p_hold_a, 1.0 - p_tb_a)[0], abs=1e-12)
    # mirror symmetry: swapping players swaps the distribution ONLY when the
    # serve-first role also swaps (the joint distribution depends on who
    # serves game 1; the marginal above averages that out)
    dist_swapped = set_game_distribution(p_hold_b, p_hold_a, 1.0 - p_tb_a, a_serves_first=False)
    for (ga, gb), p in dist.items():
        assert dist_swapped[(gb, ga)] == pytest.approx(p, abs=1e-12)


def test_set_distribution_tiebreak_handling():
    # equal holds, tiebreak heavily toward A -> 7-6 must beat 6-7
    dist_a = set_game_distribution(0.65, 0.65, p_tb_a=0.95)
    dist_b = set_game_distribution(0.65, 0.65, p_tb_a=0.05)
    assert dist_a[(7, 6)] > dist_a[(6, 7)]
    assert dist_b[(6, 7)] > dist_b[(7, 6)]
    # the 6-6 pair's mass is split exactly by p_tb_a
    tb_mass_a = dist_a[(7, 6)] + dist_a[(6, 7)]
    assert dist_a[(7, 6)] == pytest.approx(tb_mass_a * 0.95, abs=1e-12)
    assert dist_a[(6, 7)] == pytest.approx(tb_mass_a * 0.05, abs=1e-12)


# ------------------------------------------------------------- match-level


def test_match_distribution_mass_and_pmatch():
    engine = TennisMarkovEngine()
    stats_a, stats_b = _stats(engine, 1520.0, 1480.0)
    forecast = engine.forecast_match(stats_a, stats_b, "Hard", "Bo3")
    dist = match_game_distribution(
        forecast.p_game_hold_a, forecast.p_game_hold_b, forecast.p_tiebreak_a, "Bo3"
    )
    assert sum(dist.states.values()) == pytest.approx(1.0, abs=1e-9)
    assert dist.p_match_a == pytest.approx(forecast.p_match_a, abs=1e-3)


def test_match_distribution_parity_at_equal_strength():
    engine = TennisMarkovEngine()
    stats_a, stats_b = _stats(engine, 1500.0, 1500.0)
    forecast = engine.forecast_match(stats_a, stats_b, "Hard", "Bo3")
    dist = match_game_distribution(
        forecast.p_game_hold_a,
        forecast.p_game_hold_b,
        forecast.p_tiebreak_a,
        "Bo3",
    )
    assert dist.p_match_a == pytest.approx(0.5, abs=1e-9)
    assert dist.expected_games_a == pytest.approx(dist.expected_games_b, abs=1e-9)


def test_favorite_cover_and_total_monotonicity():
    engine = TennisMarkovEngine()
    base = 1500.0
    prices = []
    for delta in (0.0, 0.05, 0.10):
        stats_a, stats_b = _stats(engine, base + delta * 1000.0, base, scale=0.03)
        forecast = engine.forecast_match(stats_a, stats_b, "Hard", "Bo3")
        dist = match_game_distribution(
            forecast.p_game_hold_a, forecast.p_game_hold_b, forecast.p_tiebreak_a, "Bo3"
        )
        prices.append(
            (
                dist.p_cover("away", -2.5),
                dist.p_cover("home", 2.5),
                dist.p_total_over(22.5),
                dist.expected_total_games,
            )
        )
    cover_away, cover_home, over, expected = zip(*prices)
    # stronger favorite: more likely to cover a negative away spread ...
    assert cover_away[0] < cover_away[1] < cover_away[2]
    # ... less likely for the underdog to cover the positive home line ...
    assert cover_home[0] > cover_home[1] > cover_home[2]
    # ... and fewer games expected (blowouts are short)
    assert over[0] > over[1] > over[2]
    assert expected[0] > expected[1] > expected[2]


# ------------------------------------------------------- bridge consistency


def test_blended_ratings_match_moneyline_path():
    matches = [
        {"winner": "A", "loser": "B", "surface": "Hard", "match_date": "2026-01-01T00:00:00Z"},
        {"winner": "A", "loser": "C", "surface": "Hard", "match_date": "2026-01-10T00:00:00Z"},
        {"winner": "A", "loser": "B", "surface": "Clay", "match_date": "2026-02-01T00:00:00Z"},
        {"winner": "C", "loser": "A", "surface": "Clay", "match_date": "2026-03-01T00:00:00Z"},
        {"winner": "B", "loser": "C", "surface": "Hard", "match_date": "2026-03-15T00:00:00Z"},
    ]
    model = TennisModel()
    overall, by_surface, _, surface_counts = model.build_elo(matches)
    for player_one, player_two, surface in (("A", "C", "Hard"), ("B", "A", "Clay"), ("C", "B", "Hard")):
        expected = model.match_probability(
            overall, by_surface, surface_counts, player_one, player_two, surface
        )
        blend_one = _blended_elo_ratings(overall, by_surface, surface_counts, player_one, surface)
        blend_two = _blended_elo_ratings(overall, by_surface, surface_counts, player_two, surface)
        assert expected_win_probability(blend_one, blend_two) == pytest.approx(expected, abs=1e-12)


def test_engine_match_probability_matches_forecast():
    engine = TennisMarkovEngine()
    for elo_a, elo_b in ((1520.0, 1480.0), (1500.0, 1500.0), (1580.0, 1500.0)):
        stats_a, stats_b = _stats(engine, elo_a, elo_b)
        forecast = engine.forecast_match(stats_a, stats_b, "Hard", "Bo3")
        raw = _engine_match_probability(engine, stats_a, stats_b, "Hard", "Bo3")
        assert raw == pytest.approx(forecast.p_match_a, abs=5e-4)


# ------------------------------------------------------------ PIT pinning


def _game_row(event_id, start, winner, loser, league="ATP", surface="Hard", tournament="T1"):
    return {
        "event_id": event_id,
        "event_start_utc": start,
        "league": league,
        "loser": loser,
        "match_date": start,
        "surface": surface,
        "status": "completed",
        "tournament": tournament,
        "winner": winner,
    }


def _contract_dict(event_id="E-T", away="A", home="B", line=22.5):
    return {
        "event_id": event_id,
        "market_type": "total",
        "selection": "over",
        "line": line,
        "result": "win",
        "event_start_utc": "2026-08-20T18:00:00Z",
        "away_team": away,
        "home_team": home,
        "away_games": 13,
        "home_games": 11,
    }


def test_pit_pinning_prices_unchanged_by_future_matches(tmp_path: Path):
    data_root = tmp_path / "data"
    games_dir = data_root / "processed" / "tennis"
    games_dir.mkdir(parents=True)

    prior = [
        _game_row("E1", "2026-07-01T18:00:00Z", "A", "X"),
        _game_row("E2", "2026-07-05T18:00:00Z", "X", "B"),
        _game_row("E3", "2026-07-10T18:00:00Z", "A", "Y"),
        _game_row("E4", "2026-07-20T18:00:00Z", "Y", "B"),
    ]
    future = [
        _game_row("E9", "2026-08-25T18:00:00Z", "A", "B"),
        _game_row("E10", "2026-08-26T18:00:00Z", "B", "A"),
    ]
    games_path = games_dir / "games.jsonl"
    games_path.write_text("".join(json.dumps(row) + "\n" for row in prior), encoding="utf-8")

    context = MatchContext(
        away_team="A",
        home_team="B",
        surface="Hard",
        league="ATP",
        tournament="T1",
        match_format="Bo3",
        event_start_utc="2026-08-20T18:00:00Z",
        as_of_date="2026-08-20",
    )

    def price_once() -> float:
        return price_contract(
            _contract_dict(),
            context,
            data_root=data_root,
            history_cache={},
            elo_cache={},
            engine=TennisMarkovEngine(),
        ).probability

    before = price_once()
    assert before != pytest.approx(0.5, abs=1e-6)  # ratings actually did something
    games_path.write_text("".join(json.dumps(row) + "\n" for row in prior + future), encoding="utf-8")
    after = price_once()
    assert after == pytest.approx(before, abs=1e-12)


def test_historical_ratings_are_strictly_prior(tmp_path: Path):
    """The match being priced must itself be excluded from its own ratings."""
    data_root = tmp_path / "data"
    games_dir = data_root / "processed" / "tennis"
    games_dir.mkdir(parents=True)
    rows = [
        _game_row("E1", "2026-07-01T18:00:00Z", "A", "B"),  # the priced match itself
        _game_row("E2", "2026-07-02T18:00:00Z", "A", "B"),
    ]
    (games_dir / "games.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from model_prediction.tennis_forward import _tennis_history_before

    history = _tennis_history_before(data_root, "2026-07-01")
    assert all(row["event_id"] != "E1" for row in history)
    assert len(history) == 0  # E1 is the only prior-eligible row and it is the match itself


# --------------------------------------------------------- ledger contracts


def _ledger_row(event_id, market_type, selection, line, result, payload, tier="main"):
    return {
        "pick_id": f"{event_id}-{tier}-{selection}-{line}",
        "ledger_tier": tier,
        "sport": "tennis",
        "event_id": event_id,
        "event_start_utc": "2026-08-23T19:00:00Z",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "result": result,
        "status": "settled",
        "decision_payload_json": json.dumps(payload),
    }


def test_ledger_contract_extraction_dedupes_tiers_and_excludes_irregular(tmp_path: Path):
    db_path = tmp_path / "ledgers.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE ledger_records (
            pick_id TEXT NOT NULL, ledger_tier TEXT NOT NULL, sport TEXT NOT NULL,
            event_id TEXT, event_start_utc TEXT, market_type TEXT, selection TEXT,
            line REAL, result TEXT, status TEXT NOT NULL DEFAULT 'open',
            decision_payload_json TEXT
        )
        """
    )
    regular = {"away_team": "A", "home_team": "B", "away_score": 12, "home_score": 7}
    push = {"away_team": "V", "home_team": "C", "away_score": 1, "home_score": 0}
    connection.executemany(
        "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            tuple(_ledger_row("E1", "total", "over", 22.5, "win", regular, tier="main").values()),
            tuple(_ledger_row("E1", "total", "over", 22.5, "win", regular, tier="flat").values()),
            tuple(_ledger_row("E2", "total", "over", 23.5, "push", push, tier="main").values()),
            tuple(_ledger_row("E3", "spread", "away", -2.5, "loss", regular, tier="main").values()),
        ],
    )
    connection.commit()
    connection.close()

    contracts = _read_settled_derivative_contracts(db_path)
    assert len(contracts) == 3  # tier duplicates collapsed
    full = [c for c in contracts if _normal_full_match(c)]
    assert len(full) == 2  # push row excluded by irregular-result rule
    assert {c["event_id"] for c in full} == {"E1", "E3"}


# ------------------------------------------------------------- inference


def test_match_format_inference():
    assert _match_format("Wimbledon", "ATP") == "Bo5"
    assert _match_format("Winston-Salem Open", "ATP") == "Bo3"
    assert _match_format(None, "ATP") == "Bo3"
