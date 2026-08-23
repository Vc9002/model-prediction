"""Unit tests for WNBA Player Impact and Hierarchical Lineup Engine."""

from model_prediction.features.wnba_player_impact import (
    WNBAPlayerProfile,
    compute_lineup_impact,
    shrink_player_rating,
)


def test_shrink_player_rating_zero_games():
    off, def_rtg, net = shrink_player_rating(115.0, 95.0, games_played=0)
    assert off == 101.5
    assert def_rtg == 101.5
    assert net == 0.0


def test_shrink_player_rating_small_sample():
    off, def_rtg, net = shrink_player_rating(120.0, 90.0, games_played=2, prior_weight=8.0)
    # Shrunk toward 101.5
    assert 101.5 < off < 120.0
    assert 90.0 < def_rtg < 101.5
    assert net > 0.0


def test_compute_lineup_impact_equal_teams():
    p1 = WNBAPlayerProfile("Star 1", "Team A", 32.0, 110.0, 100.0, 10.0, 0.25, 0.58, 1.02, 20)
    p2 = WNBAPlayerProfile("Star 2", "Team B", 32.0, 110.0, 100.0, 10.0, 0.25, 0.58, 1.02, 20)

    impact = compute_lineup_impact([p1], [p2])
    # Home court advantage gives home positive net rating
    assert impact.lineup_net_advantage > 0.0
    assert impact.projected_pace == 81.1
    assert impact.home_projected_ppp > impact.away_projected_ppp


def test_compute_lineup_impact_injury_penalty():
    p1 = WNBAPlayerProfile("Star A", "Team A", 34.0, 115.0, 98.0, 17.0, 0.28, 0.60, 1.0, 25)
    p2 = WNBAPlayerProfile("Star B", "Team B", 34.0, 115.0, 98.0, 17.0, 0.28, 0.60, 1.0, 25)

    # Away team loses their star
    impact = compute_lineup_impact([p1], [p2], away_inactive_players=["Star B"])
    assert impact.injury_impact_gap > 0.0
    assert impact.lineup_net_advantage > 3.2  # Home advantage exceeds baseline HFA
