from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba import nba_model
from model_prediction.models.soccer import UpcomingMatch, soccer_model
from model_prediction.models.tennis import tennis_model
from model_prediction.models.tennis import UpcomingMatch as TennisMatch


def basketball_history() -> list[GameRecord]:
    games = []
    for index in range(20):
        # Giants beat Minnows consistently by double digits.
        games.append(
            GameRecord(
                event_id=f"b{index}",
                event_start_utc=f"2026-05-{index % 28 + 1:02d}T23:00:00Z",
                league="NBA",
                away_team="Minnows",
                home_team="Giants",
                away_score=95,
                home_score=110,
            )
        )
        games.append(
            GameRecord(
                event_id=f"c{index}",
                event_start_utc=f"2026-05-{index % 28 + 1:02d}T21:00:00Z",
                league="NBA",
                away_team="Giants",
                home_team="Minnows",
                away_score=112,
                home_score=98,
            )
        )
    return games


def test_basketball_model_prefers_the_stronger_team_and_covers_markets() -> None:
    model = nba_model()
    predictions = model.predict_games(
        basketball_history(),
        [
            UpcomingGame(
                event_id="up1",
                event_start_utc="2026-06-01T23:00:00Z",
                away_team="Minnows",
                home_team="Giants",
                spread_away_line=12.5,
                total_line=205.5,
            )
        ],
    )
    by_market = {prediction.market_type: prediction for prediction in predictions}
    assert by_market["moneyline"].probabilities["home"] > 0.6
    assert abs(sum(by_market["moneyline"].probabilities.values()) - 1) < 1e-5
    assert set(by_market["spread"].probabilities) == {"away", "home"}
    assert set(by_market["total"].probabilities) == {"over", "under"}


def soccer_history() -> list[GameRecord]:
    games = []
    for index in range(16):
        games.append(
            GameRecord(
                event_id=f"s{index}",
                event_start_utc=f"2026-04-{index % 28 + 1:02d}T15:00:00Z",
                league="EPL",
                away_team="Relegation FC",
                home_team="Champions FC",
                away_score=0 if index % 3 else 1,
                home_score=2 + index % 2,
            )
        )
    return games


def test_soccer_model_three_way_and_totals_are_coherent() -> None:
    predictions = soccer_model().predict_games(
        soccer_history(),
        [
            UpcomingMatch(
                event_id="m1",
                event_start_utc="2026-05-01T15:00:00Z",
                away_team="Relegation FC",
                home_team="Champions FC",
                league="EPL",
            )
        ],
    )
    moneyline = next(p for p in predictions if p.market_type == "moneyline")
    total = next(p for p in predictions if p.market_type == "total")
    btts = next(p for p in predictions if p.market_type == "btts")
    assert abs(sum(moneyline.probabilities.values()) - 1) < 1e-5
    assert moneyline.probabilities["home"] > moneyline.probabilities["away"]
    assert abs(total.probabilities["over"] + total.probabilities["under"] - 1) < 1e-6
    assert abs(btts.probabilities["yes"] + btts.probabilities["no"] - 1) < 1e-6


def test_tennis_model_flat_call_from_surface_elo() -> None:
    matches = [
        {"winner": "Ace", "loser": "Journeyman", "surface": "Clay", "match_date": f"2026-03-{d:02d}"}
        for d in range(1, 15)
    ]
    predictions = tennis_model().predict_matches(
        matches,
        [
            TennisMatch(
                event_id="t1",
                event_start_utc="2026-05-01T12:00:00Z",
                player_one="Ace",
                player_two="Journeyman",
                surface="Clay",
                tour="WTA",
            )
        ],
    )
    assert predictions[0].probabilities["away"] > 0.6  # player_one slot
    assert abs(sum(predictions[0].probabilities.values()) - 1) < 1e-5
