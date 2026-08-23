"""Unit tests for expanded tennis multi-market pricing and matching engine."""

from __future__ import annotations

from model_prediction.models.tennis import TennisModel, UpcomingMatch
from model_prediction.tennis_forward import _name_matches, _norm_cdf


def test_name_matching_accents_and_surnames() -> None:
    assert _name_matches("Jessica Pegula", "Jessica Pegula")
    assert _name_matches("Coco Gauff", "Gauff C.")
    assert _name_matches("Pablo Carreno Busta", "Pablo Carreño Busta")
    assert _name_matches("Francisco Comesana", "Francisco Comesaña")
    assert not _name_matches("Jessica Pegula", "Coco Gauff")


def test_tennis_model_prediction_and_markov_cdf() -> None:
    model = TennisModel()
    history = [
        {
            "winner": "Player A",
            "loser": "Player B",
            "surface": "Hard",
            "match_date": "2026-01-01",
            "league": "ATP",
        },
        {
            "winner": "Player A",
            "loser": "Player B",
            "surface": "Hard",
            "match_date": "2026-01-02",
            "league": "ATP",
        },
        {
            "winner": "Player A",
            "loser": "Player B",
            "surface": "Hard",
            "match_date": "2026-01-03",
            "league": "ATP",
        },
    ]
    upcoming = [
        UpcomingMatch(
            event_id="test_1",
            event_start_utc="2026-08-23T20:00:00Z",
            player_one="Player A",
            player_two="Player B",
            surface="Hard",
            tour="ATP",
        )
    ]
    preds = model.predict_games(history, upcoming)
    assert len(preds) == 1
    assert preds[0].probabilities["away"] > 0.50

    # CDF bounds check
    assert 0.49 < _norm_cdf(0.0) < 0.51
    assert _norm_cdf(2.0) > 0.97
    assert _norm_cdf(-2.0) < 0.03


def test_tennis_spread_and_total_prob_bounds() -> None:
    p_away = 0.65
    line_spread = -2.5
    mu_delta = 6.0 * (p_away - 0.5)
    sigma_delta = 4.0
    p_away_cover = _norm_cdf((mu_delta + line_spread) / sigma_delta)
    assert 0.0 < p_away_cover < 1.0

    exp_games = 22.5 + 3.5 * (1.0 - abs(p_away - 0.5) * 2.0)
    line_total = 21.5
    sigma_total = 4.2
    p_over = 1.0 - _norm_cdf((line_total - exp_games) / sigma_total)
    assert 0.0 < p_over < 1.0
