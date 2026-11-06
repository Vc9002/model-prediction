"""Walk-forward and unit tests for NCAAB v4 (dedicated spread/total models)."""

from __future__ import annotations

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v4 import NCAABSpreadModelV4
from model_prediction.models.ncaab_total_v4 import NCAABTotalModelV4


def _game(event_id, start, away, home, away_score, home_score):
    return GameRecord(
        event_id=event_id,
        event_start_utc=start,
        league="NCAAB",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
    )


def test_ncaab_spread_v4_uses_current_season_sample_not_lifetime():
    """A team with a large lifetime game count but only 1 current-season game
    must get WIDE uncertainty (thin-sample), not tight uncertainty -- this is
    the exact bug found in the 2026-11-05 live incident (basketball.py's
    shared `sample` variable used lifetime games_played)."""
    model = NCAABSpreadModelV4()

    # 30 games last season (2025-26), then an 80+ day gap (offseason), then
    # 1 game this season (2026-27) for both teams -- large lifetime count,
    # thin current-season count.
    history = []
    for i in range(30):
        history.append(
            _game(
                f"last_season_{i}",
                f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "Team A",
                "Team B",
                65 + i % 5,
                70 + i % 5,
            )
        )
    history.append(_game("this_season_1", "2026-11-03T00:00:00Z", "Team A", "Team B", 68, 72))

    upcoming = UpcomingGame(
        event_id="test_game",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team A",
        home_team="Team B",
        spread_away_line=5.5,
    )
    preds = model.predict_spreads(history, [upcoming])
    spread_pred = next(p for p in preds if p.market_type == "spread")

    # Lifetime sample here is 31 (>=15), which under the OLD buggy formula
    # (0.20 - 0.005*31 = 0.045) would floor near max confidence. Current-season
    # sample is 1, which must produce uncertainty near the 0.20 cap instead.
    assert spread_pred.uncertainty >= 0.15, (
        f"expected wide (thin-sample) uncertainty, got {spread_pred.uncertainty} "
        "-- this means the model is reading lifetime games_played instead of "
        "current_season_games_played"
    )
    # The widened uncertainty must actually reach the margin_sd feeding the
    # CDF -- not just the reported metadata (the exact mechanism gap C1/the
    # dynamic_volatility fix addresses).
    assert spread_pred.inference_inputs["sd"] >= 15.0


def test_ncaab_spread_v4_season_opener_gets_max_uncertainty():
    """A true season opener (0 games played so far this season, prior
    season's games all more than offseason_gap_days in the past) must get
    uncertainty at/near the 0.20 maximum. TeamTrend.current_season_games_played
    alone can't see this -- it only sees gaps WITHIN the history list, not the
    gap between the last historical game and the game being predicted now."""
    model = NCAABSpreadModelV4()

    # 30 games last season, all ending more than 150 days (offseason_gap_days)
    # before the predicted game -- a true opener, nothing in the new season.
    history = []
    for i in range(30):
        history.append(
            _game(
                f"last_season_opener_{i}",
                f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "Team A",
                "Team B",
                65 + i % 5,
                70 + i % 5,
            )
        )

    upcoming = UpcomingGame(
        event_id="test_opener_game",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team A",
        home_team="Team B",
        spread_away_line=5.5,
    )
    preds = model.predict_spreads(history, [upcoming])
    spread_pred = next(p for p in preds if p.market_type == "spread")

    assert spread_pred.feature_basis["current_season_sample"] == 0
    assert spread_pred.uncertainty >= 0.19, (
        f"expected max uncertainty for a true season opener, got {spread_pred.uncertainty}"
    )


def test_ncaab_spread_v4_standalone():
    model = NCAABSpreadModelV4()
    game = UpcomingGame(
        event_id="test_standalone_spread",
        event_start_utc="2026-11-05T20:00:00Z",
        away_team="Duke",
        home_team="Kansas",
        spread_away_line=-3.5,
    )
    preds = model.predict_spreads([], [game])
    assert len(preds) == 2
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    spread_pred = next(p for p in preds if p.market_type == "spread")
    assert 0.0 < ml_pred.probabilities["home"] < 1.0
    assert 0.0 < spread_pred.probabilities["away"] < 1.0
    assert abs(spread_pred.probabilities["away"] + spread_pred.probabilities["home"] - 1.0) < 1e-4


def test_ncaab_total_v4_uses_current_season_sample_not_lifetime():
    model = NCAABTotalModelV4()
    history = []
    for i in range(30):
        history.append(
            _game(
                f"last_season_total_{i}",
                f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "Team C",
                "Team D",
                65 + i % 5,
                70 + i % 5,
            )
        )
    history.append(_game("this_season_total_1", "2026-11-03T00:00:00Z", "Team C", "Team D", 68, 72))

    upcoming = UpcomingGame(
        event_id="test_total_game",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team C",
        home_team="Team D",
        total_line=145.5,
    )
    preds = model.predict_totals(history, [upcoming])
    total_pred = next(p for p in preds if p.market_type == "total")
    assert total_pred.uncertainty >= 0.15
    # The widened uncertainty must actually reach the total_sd feeding the
    # CDF -- not just the reported metadata.
    assert total_pred.inference_inputs["sd"] >= 15.0


def test_ncaab_total_v4_season_opener_gets_max_uncertainty():
    """True season opener for the total model -- see
    test_ncaab_spread_v4_season_opener_gets_max_uncertainty for why
    current_season_games_played alone can't detect this case."""
    model = NCAABTotalModelV4()

    history = []
    for i in range(30):
        history.append(
            _game(
                f"last_season_total_opener_{i}",
                f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "Team C",
                "Team D",
                65 + i % 5,
                70 + i % 5,
            )
        )

    upcoming = UpcomingGame(
        event_id="test_total_opener_game",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team C",
        home_team="Team D",
        total_line=145.5,
    )
    preds = model.predict_totals(history, [upcoming])
    total_pred = next(p for p in preds if p.market_type == "total")

    assert total_pred.feature_basis["current_season_sample"] == 0
    assert total_pred.uncertainty >= 0.19, (
        f"expected max uncertainty for a true season opener, got {total_pred.uncertainty}"
    )


def test_ncaab_total_v4_standalone():
    model = NCAABTotalModelV4()
    game = UpcomingGame(
        event_id="test_standalone_total",
        event_start_utc="2026-11-05T20:00:00Z",
        away_team="Duke",
        home_team="Kansas",
        total_line=148.5,
    )
    preds = model.predict_totals([], [game])
    assert len(preds) == 1
    total_pred = preds[0]
    assert total_pred.market_type == "total"
    assert 0.0 < total_pred.probabilities["over"] < 1.0
    assert abs(total_pred.probabilities["over"] + total_pred.probabilities["under"] - 1.0) < 1e-4
    assert total_pred.feature_basis["est_possessions"] > 55.0
