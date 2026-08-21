"""Tests for the basic sport-agnostic Elo model (basic_elo.py) -- the
control-tier baseline CLAUDE.md itself names for NFL/tennis/KBO/NPB,
reused as the shared "basic prediction" for NBA/WNBA/NFL/Soccer/Tennis's
foundation adapters."""

from __future__ import annotations

import polars as pl

from model_prediction.rebuild.basic_elo import EloModel


def _games(pairs: list[tuple[str, str, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"home_team": h, "away_team": a, "home_score": hs, "away_score": as_} for h, a, hs, as_ in pairs]
    )


class TestEloModel:
    def test_unseen_teams_default_to_initial_rating_plus_home_advantage(self):
        model = EloModel()
        home_prob, away_prob = model.predict("Never Seen A", "Never Seen B")
        # Equal initial ratings -> home_advantage alone tilts the prediction.
        assert home_prob > 0.5
        assert abs(home_prob + away_prob - 1.0) < 1e-9

    def test_a_team_that_always_wins_earns_a_higher_rating(self):
        model = EloModel()
        model.fit(_games([("Alpha", "Beta", 100, 80) for _ in range(15)]))
        assert model.ratings["Alpha"] > model.ratings["Beta"]
        home_prob, _ = model.predict("Alpha", "Beta")
        assert home_prob > 0.7  # confidently favors the real repeated winner

    def test_predicted_winner_matches_the_higher_probability_side(self):
        model = EloModel()
        model.fit(_games([("Alpha", "Beta", 100, 80) for _ in range(15)]))
        assert model.predicted_winner("Alpha", "Beta") == "home"
        assert model.predicted_winner("Beta", "Alpha") == "away"

    def test_a_real_tie_updates_both_teams_toward_half_not_skipped(self):
        model = EloModel()
        model.fit(_games([("Alpha", "Beta", 1, 1)]))
        # A tie against an equally-rated team (with home_advantage tilting
        # the pre-game expectation above 0.5) pulls the home team's rating
        # down and the away team's rating up -- proves the tie branch ran,
        # not that fit() silently skipped a scoreless-diff row.
        assert model.games_fit == 1
        assert model.ratings["Alpha"] < model.initial_rating
        assert model.ratings["Beta"] > model.initial_rating

    def test_fit_is_order_sensitive_chronological_not_batch(self):
        # Elo's real defining property: rating after game N depends on the
        # order games were fit in, not just the aggregate win/loss tally --
        # fitting the same 3 games in a different order must NOT produce
        # identical final ratings in general.
        model_a = EloModel()
        model_a.fit(_games([("A", "B", 100, 90), ("B", "C", 100, 90), ("A", "C", 100, 90)]))
        model_b = EloModel()
        model_b.fit(_games([("A", "C", 100, 90), ("B", "C", 100, 90), ("A", "B", 100, 90)]))
        assert model_a.ratings != model_b.ratings
