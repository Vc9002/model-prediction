"""Unit tests for tennis matching and market-boundary helpers."""

from __future__ import annotations

import pytest

from model_prediction.models.tennis import TennisModel, UpcomingMatch
from model_prediction.tennis_forward import (
    _is_tennis_subperiod_slug,
    _name_matches,
    _select_one_tennis_line_per_market,
)


def test_name_matching_accents_and_surnames() -> None:
    assert _name_matches("Jessica Pegula", "Jessica Pegula")
    assert _name_matches("Coco Gauff", "Gauff C.")
    assert _name_matches("Pablo Carreno Busta", "Pablo Carreño Busta")
    assert _name_matches("Francisco Comesana", "Francisco Comesaña")
    assert not _name_matches("Jessica Pegula", "Coco Gauff")


def test_tennis_model_prediction() -> None:
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


@pytest.mark.parametrize(
    "slug",
    [
        "wta-player-a-player-b-ss-2pt5",
        "atp-player-a-player-b-st-22pt5",
        "wta-player-a-player-b-set-1-winner",
        "wta-player-a-player-b-h1-total",
    ],
)
def test_tennis_subperiod_slug_detection(slug: str) -> None:
    assert _is_tennis_subperiod_slug(slug)


def test_full_match_moneyline_slug_is_not_a_subperiod() -> None:
    assert not _is_tennis_subperiod_slug("wta-player-a-player-b-2026-08-23")


def test_tennis_selects_one_spread_and_total_without_using_results() -> None:
    contracts = [
        {
            "event_id": "match-1",
            "market_type": "moneyline",
            "market_slug": "ml",
            "model_probability": 0.58,
            "executable_ask": 0.50,
        },
        {
            "event_id": "match-1",
            "market_type": "total",
            "market_slug": "over-17.5",
            "model_probability": 0.977,
            "executable_ask": 0.89,
            "result": "win",
        },
        {
            "event_id": "match-1",
            "market_type": "total",
            "market_slug": "over-22.5",
            "model_probability": 0.788,
            "executable_ask": 0.49,
            "result": "loss",
        },
        {
            "event_id": "match-1",
            "market_type": "spread",
            "market_slug": "player-minus-1.5",
            "model_probability": 0.66,
            "executable_ask": 0.58,
        },
        {
            "event_id": "match-1",
            "market_type": "spread",
            "market_slug": "player-minus-2.5",
            "model_probability": 0.72,
            "executable_ask": 0.55,
        },
    ]

    selected, skipped = _select_one_tennis_line_per_market(contracts)

    assert [row["market_slug"] for row in selected] == ["ml", "over-22.5", "player-minus-2.5"]
    assert len(skipped) == 2
    assert {row["reason"] for row in skipped} == {"TENNIS_CORRELATED_LINE_SUPERSEDED"}
