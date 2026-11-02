from model_prediction.sports.tennis.moneyline import (
    TENNIS_V4_VERSION,
    TENNIS_V5_VERSION,
    TENNIS_V6_VERSION,
    TennisMarkovEngine,
    TennisModel,
    TennisV2Model,
)


def test_tennis_moneyline_imports():
    assert TennisV2Model is not None
    assert TennisMarkovEngine is not None
    assert TennisModel is not None
    assert TENNIS_V4_VERSION == "tennis-surface-elo-v4"
    assert TENNIS_V5_VERSION == "tennis-surface-elo-v5"
    assert TENNIS_V6_VERSION == "tennis-surface-elo-v6"


def test_tennis_surface_elo_v4_anti_symmetry():
    model = TennisModel(version=TENNIS_V4_VERSION)
    matches = [
        {"winner": "Player A", "loser": "Player B", "surface": "Clay", "match_date": "2026-05-01T12:00:00Z"},
        {"winner": "Player A", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-02T12:00:00Z"},
        {"winner": "Player B", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-03T12:00:00Z"},
    ]
    overall, by_surface, counts, surface_counts = model.build_elo(matches)
    p_ab = model.match_probability_v4(
        overall, by_surface, surface_counts, counts, "Player A", "Player B", "Clay", "2026-05-04T12:00:00Z"
    )
    p_ba = model.match_probability_v4(
        overall, by_surface, surface_counts, counts, "Player B", "Player A", "Clay", "2026-05-04T12:00:00Z"
    )
    assert p_ab > 0.50
    assert abs((p_ab + p_ba) - 1.0) < 1e-6


def test_tennis_surface_elo_v5_anti_symmetry():
    model = TennisModel(version=TENNIS_V5_VERSION)
    matches = [
        {"winner": "Player A", "loser": "Player B", "surface": "Clay", "match_date": "2026-05-01T12:00:00Z"},
        {"winner": "Player A", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-02T12:00:00Z"},
        {"winner": "Player B", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-03T12:00:00Z"},
    ]
    overall, by_surface, counts, surface_counts = model.build_elo(matches)
    p_ab = model.match_probability_v5(
        overall, by_surface, surface_counts, counts, "Player A", "Player B", "Clay", "2026-05-04T12:00:00Z"
    )
    p_ba = model.match_probability_v5(
        overall, by_surface, surface_counts, counts, "Player B", "Player A", "Clay", "2026-05-04T12:00:00Z"
    )
    assert p_ab > 0.50
    assert abs((p_ab + p_ba) - 1.0) < 1e-6


def test_tennis_surface_elo_v6_anti_symmetry_and_unsquashed():
    model = TennisModel(version=TENNIS_V6_VERSION)
    matches = [
        {"winner": "Player A", "loser": "Player B", "surface": "Clay", "match_date": "2026-05-01T12:00:00Z"},
        {"winner": "Player A", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-02T12:00:00Z"},
        {"winner": "Player B", "loser": "Player C", "surface": "Clay", "match_date": "2026-05-03T12:00:00Z"},
    ]
    overall, by_surface, counts, surface_counts = model.build_elo(matches)
    p_ab_v6 = model.match_probability_v6(
        overall, by_surface, surface_counts, counts, "Player A", "Player B", "Clay", "2026-05-04T12:00:00Z"
    )
    p_ba_v6 = model.match_probability_v6(
        overall, by_surface, surface_counts, counts, "Player B", "Player A", "Clay", "2026-05-04T12:00:00Z"
    )
    p_ab_v5 = model.match_probability_v5(
        overall, by_surface, surface_counts, counts, "Player A", "Player B", "Clay", "2026-05-04T12:00:00Z"
    )
    assert p_ab_v6 > 0.50
    assert abs((p_ab_v6 + p_ba_v6) - 1.0) < 1e-6
    # v6 (tau=1.0) is unsquashed compared to v5 (tau=1.18), so favorite prob must be higher in v6
    assert p_ab_v6 > p_ab_v5
