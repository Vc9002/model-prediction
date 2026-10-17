"""Unit tests for Darts exact Markov leg progression model and forward pipeline."""

from __future__ import annotations

import pytest

from model_prediction.darts_forward import _parse_players_from_title, _player_matches, build_darts_slate
from model_prediction.models.darts import (
    DARTS_MODEL_VERSION,
    DartsLegMarkovEngine,
    DartsMatchRecord,
    DartsModel,
    UpcomingDartsMatch,
    darts_model,
)


def test_darts_markov_engine_probabilities():
    engine = DartsLegMarkovEngine()

    # Equal players (1500 vs 1500)
    p_hold_a, p_break_a, p_hold_b, p_break_b = engine.compute_leg_probabilities(1500.0, 1500.0)

    # Throw advantage: hold prob should be > 0.50
    assert 0.60 < p_hold_a < 0.65
    assert 0.60 < p_hold_b < 0.65
    assert pytest.approx(p_hold_a + p_break_b) == 1.0
    assert pytest.approx(p_hold_b + p_break_a) == 1.0

    # Stronger player A vs weaker player B (1700 vs 1400)
    p_hold_a_strong, p_break_a_strong, p_hold_b_weak, _ = engine.compute_leg_probabilities(1700.0, 1400.0)
    assert p_hold_a_strong > p_hold_a
    assert p_break_a_strong > p_break_a
    assert p_hold_b_weak < p_hold_b


def test_darts_score_distribution_properties():
    engine = DartsLegMarkovEngine()
    best_of_legs = 11  # First to 6 legs
    dist = engine.score_distribution(1550.0, 1450.0, best_of_legs=best_of_legs, first_throw="A")

    # Probabilities must sum to 1.0
    total_prob = sum(dist.values())
    assert pytest.approx(total_prob, abs=1e-5) == 1.0

    # In best-of-11, winning player must have 6 legs
    legs_to_win = 6
    for (i, j), prob in dist.items():
        assert prob > 0.0
        assert i == legs_to_win or j == legs_to_win
        assert min(i, j) < legs_to_win


def test_darts_market_evaluation_symmetry():
    engine = DartsLegMarkovEngine()

    # Against equal opponent with 50/50 toss prior, match win prob must be exactly 0.50
    res = engine.evaluate_markets(1500.0, 1500.0, best_of_legs=11)
    assert pytest.approx(res["p_win_a"], abs=1e-5) == 0.50
    assert pytest.approx(res["p_win_b"], abs=1e-5) == 0.50
    assert pytest.approx(res["expected_legs_a"], abs=1e-4) == res["expected_legs_b"]


def test_darts_model_spread_and_totals():
    model = darts_model()
    assert model.version == DARTS_MODEL_VERSION

    pred = model.predict_match(
        player_a="Michael van Gerwen",
        player_b="Luke Littler",
        best_of_legs=11,
        spread_line=-1.5,
        total_line=9.5,
    )

    # Moneyline
    assert 0.0 < pred["moneyline"]["a"] < 1.0
    assert pytest.approx(pred["moneyline"]["a"] + pred["moneyline"]["b"]) == 1.0

    # Spread
    spread_dict = pred["spread"][-1.5]
    assert 0.0 < spread_dict["a"] < 1.0
    assert pytest.approx(spread_dict["a"] + spread_dict["b"]) == 1.0

    # Totals
    total_dict = pred["total"][9.5]
    assert 0.0 < total_dict["over"] < 1.0
    assert pytest.approx(total_dict["over"] + total_dict["under"]) == 1.0


def test_darts_ratings_update():
    model = DartsModel()
    init_ra = model.get_rating("Luke Humphries")
    init_rb = model.get_rating("Michael Smith")
    assert init_ra == 1500.0
    assert init_rb == 1500.0

    match = DartsMatchRecord(
        match_id="m1",
        match_date="2026-10-01",
        league="DARTS_PDC",
        player_a="Luke Humphries",
        player_b="Michael Smith",
        legs_a=6,
        legs_b=2,
        winner="Luke Humphries",
    )
    model.update_match(match)

    assert model.get_rating("Luke Humphries") > 1500.0
    assert model.get_rating("Michael Smith") < 1500.0


def test_darts_batch_predict_games():
    model = darts_model()
    upcoming = [
        UpcomingDartsMatch(
            event_id="ev1",
            event_start_utc="2026-10-16T18:00:00Z",
            player_a="Gerwyn Price",
            player_b="Peter Wright",
            best_of_legs=11,
            league="DARTS_PDC",
        ),
        UpcomingDartsMatch(
            event_id="ev2",
            event_start_utc="2026-10-16T19:00:00Z",
            player_a="Niko Springer",
            player_b="Scott Williams",
            best_of_legs=7,
            league="DARTS_MODUS",
        ),
    ]

    preds = model.predict_games([], upcoming)
    assert len(preds) > 0

    ml_preds = [p for p in preds if p.market_type == "moneyline"]
    assert len(ml_preds) == 2
    for p in ml_preds:
        assert abs(p.probabilities["home"] + p.probabilities["away"] - 1.0) < 1e-5


def test_darts_helpers():
    # Title parser
    parsed = _parse_players_from_title("Niko Springer vs. Scott Williams")
    assert parsed == ("Niko Springer", "Scott Williams")

    parsed_v = _parse_players_from_title("Gary Anderson v Rob Cross")
    assert parsed_v == ("Gary Anderson", "Rob Cross")

    # Player matches
    assert _player_matches("Niko Springer", "Niko Springer")
    assert _player_matches("N. Springer", "Niko Springer")
    assert not _player_matches("Niko Springer", "Scott Williams")


def test_build_darts_slate_empty(tmp_path):
    class EmptyClient:
        def slate(self, league, game_date):
            return []

    slate = build_darts_slate(data_root=tmp_path, game_date="2026-10-16", client=EmptyClient())
    assert slate["sport"] == "darts"
    assert slate["events_count"] == 0
    assert slate["priced_contracts"] == 0
    assert slate["priced"] == []
