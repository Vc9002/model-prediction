from model_prediction.models.tennis import TennisModel, UpcomingMatch
from model_prediction.models.tennis_derivatives import (
    hold_game_probability,
    price_tennis_derivatives,
    set_score_distribution,
    tiebreak_probability,
)


def test_hold_game_probability():
    # Symmetric 50% point win rate -> 50% hold rate
    assert abs(hold_game_probability(0.5) - 0.5) < 1e-6
    # Elite server (e.g. 70% point win) has >85% hold rate
    p_hold_70 = hold_game_probability(0.70)
    assert p_hold_70 > 0.85
    assert p_hold_70 < 1.0


def test_tiebreak_probability():
    # Symmetric servers -> 50% tiebreak win
    assert abs(tiebreak_probability(0.60, 0.60) - 0.5) < 1e-4
    # Stronger server has >50% win rate
    assert tiebreak_probability(0.68, 0.58) > 0.65


def test_set_score_distribution():
    pa_h = hold_game_probability(0.64)
    pb_h = hold_game_probability(0.60)
    p_tb = tiebreak_probability(0.64, 0.60)
    dist = set_score_distribution(pa_h, pb_h, p_tb)

    # Must sum to 1.0
    assert abs(sum(dist.values()) - 1.0) < 1e-5
    # Contains all 14 standard tennis set score lines
    for score in [
        (6, 0),
        (6, 1),
        (6, 2),
        (6, 3),
        (6, 4),
        (7, 5),
        (7, 6),
        (0, 6),
        (1, 6),
        (2, 6),
        (3, 6),
        (4, 6),
        (5, 7),
        (6, 7),
    ]:
        assert score in dist
        assert dist[score] > 0.0


def test_match_game_distribution_and_pricing():
    # Price Best-of-3 match with spread and total lines
    pricing = price_tennis_derivatives(
        p_serve_a=0.65,
        p_serve_b=0.59,
        spread_line=-2.5,
        total_line=21.5,
        best_of=3,
    )
    assert pricing.match_win_a > 0.60
    assert pricing.match_win_b < 0.40
    assert abs(pricing.match_win_a + pricing.match_win_b - 1.0) < 1e-5

    assert pricing.spread_p1_cover is not None
    assert pricing.spread_p2_cover is not None
    assert abs(pricing.spread_p1_cover + pricing.spread_p2_cover - 1.0) < 1e-5

    assert pricing.total_over is not None
    assert pricing.total_under is not None
    assert abs(pricing.total_over + pricing.total_under - 1.0) < 1e-5


def test_tennis_model_predict_derivatives():
    matches = [
        {
            "winner": "Carlos Alcaraz",
            "loser": "Daniil Medvedev",
            "surface": "Hard",
            "match_date": "2026-08-01",
        },
        {
            "winner": "Carlos Alcaraz",
            "loser": "Alexander Zverev",
            "surface": "Hard",
            "match_date": "2026-08-02",
        },
        {
            "winner": "Daniil Medvedev",
            "loser": "Alexander Zverev",
            "surface": "Hard",
            "match_date": "2026-08-03",
        },
    ]
    model = TennisModel()
    upcoming = [
        UpcomingMatch(
            event_id="tennis_match_1",
            event_start_utc="2026-09-01T18:00:00Z",
            player_one="Carlos Alcaraz",
            player_two="Daniil Medvedev",
            surface="Hard",
            tour="ATP",
            spread_player_one_line=-3.5,
            total_games_line=22.5,
        )
    ]
    preds = model.predict_games(matches, upcoming)
    assert len(preds) == 3  # Moneyline, Spread, Total

    ml_p = next(p for p in preds if p.market_type == "moneyline")
    assert ml_p.probabilities["away"] > 0
    assert ml_p.probabilities["home"] > 0

    spread_p = next(p for p in preds if p.market_type == "spread")
    assert spread_p.line == -3.5
    assert spread_p.probabilities["away"] > 0
    assert spread_p.probabilities["home"] > 0
    assert round(spread_p.probabilities["away"] + spread_p.probabilities["home"], 5) == 1.0

    total_p = next(p for p in preds if p.market_type == "total")
    assert total_p.line == 22.5
    assert total_p.probabilities["over"] > 0
    assert total_p.probabilities["under"] > 0
    assert round(total_p.probabilities["over"] + total_p.probabilities["under"], 5) == 1.0


def test_tennis_model_predict_derivatives_with_player_form():
    from model_prediction.models.tennis import TennisPlayerForm

    matches = [
        {
            "winner": "Carlos Alcaraz",
            "loser": "Daniil Medvedev",
            "surface": "Hard",
            "match_date": "2026-08-01",
        },
    ]
    model = TennisModel()
    form_alcaraz = TennisPlayerForm(
        player_id="alcaraz",
        name="Carlos Alcaraz",
        serve_points_won=0.68,
        serve_points=1000,
        return_points_won=0.42,
        return_points=1000,
        surface_elo=1700.0,
        overall_elo=1700.0,
    )
    form_medvedev = TennisPlayerForm(
        player_id="medvedev",
        name="Daniil Medvedev",
        serve_points_won=0.64,
        serve_points=1000,
        return_points_won=0.38,
        return_points=1000,
        surface_elo=1650.0,
        overall_elo=1650.0,
    )
    upcoming = [
        UpcomingMatch(
            event_id="match_with_form",
            event_start_utc="2026-09-01T18:00:00Z",
            player_one="Carlos Alcaraz",
            player_two="Daniil Medvedev",
            surface="Hard",
            tour="ATP",
            spread_player_one_line=-3.5,
            total_games_line=22.5,
            player_one_form=form_alcaraz,
            player_two_form=form_medvedev,
        )
    ]
    preds = model.predict_games(matches, upcoming)
    assert len(preds) == 3
    spread_p = next(p for p in preds if p.market_type == "spread")
    assert spread_p.probabilities["away"] > spread_p.probabilities["home"]


def test_serve_pair_reproduces_target_win_probability_and_total_games():
    from model_prediction.models.tennis_derivatives import (
        TENNIS_FORM_SIGMA,
        price_tennis_derivatives,
        serve_pair_for_win_probability,
    )

    for tour, best_of in (("ATP", 3), ("ATP", 5), ("WTA", 3)):
        for target in (0.5, 0.65, 0.85):
            a, b = serve_pair_for_win_probability(target, tour, best_of)
            priced = price_tennis_derivatives(a, b, best_of=best_of, form_sigma=TENNIS_FORM_SIGMA)
            assert abs(priced.match_win_a - target) < 0.004
            assert (a >= b) == (target >= 0.5)
    # Realistic game counts: the fixed-point Markov chain overstated them by ~2 games.
    a, b = serve_pair_for_win_probability(0.5, "ATP", 3)
    even = price_tennis_derivatives(a, b, best_of=3, form_sigma=TENNIS_FORM_SIGMA)
    a, b = serve_pair_for_win_probability(0.9, "ATP", 3)
    lopsided = price_tennis_derivatives(a, b, best_of=3, form_sigma=TENNIS_FORM_SIGMA)
    assert 22.0 < even.expected_total_games < 25.0
    assert lopsided.expected_total_games < even.expected_total_games - 2.0
