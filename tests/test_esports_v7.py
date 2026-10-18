"""Tests for Esports Contextual v7 Models."""

from model_prediction.models.esports_contextual_v7 import (
    EsportsContextFeatures,
    EsportsContextualV7Model,
)


def test_esports_v7_blue_side_advantage_in_lol():
    model = EsportsContextualV7Model()
    feat_blue = EsportsContextFeatures(
        game_title="lol",
        team_a="T1",
        team_b="GenG",
        elo_prob_a=0.50,
        side_a="blue",
    )
    fc = model.forecast_match(feat_blue)

    assert fc.prob_a_wins > 0.50
    assert fc.edge_vs_elo_prior > 0.0


def test_esports_v7_bo5_format_magnification():
    model = EsportsContextualV7Model()
    feat_bo1 = EsportsContextFeatures(
        game_title="cs2",
        team_a="Vitality",
        team_b="FaZe",
        elo_prob_a=0.60,
        series_format="Bo1",
    )
    feat_bo5 = EsportsContextFeatures(
        game_title="cs2",
        team_a="Vitality",
        team_b="FaZe",
        elo_prob_a=0.60,
        series_format="Bo5",
    )
    fc1 = model.forecast_match(feat_bo1)
    fc5 = model.forecast_match(feat_bo5)

    assert fc1.prob_a_wins > 0.50
    assert fc5.prob_a_wins > 0.50


def test_gated_research_eligibility_downgrades_large_disagreement():
    from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest
    from model_prediction.eligibility import evaluate_gated_research_eligibility
    from model_prediction.units import Exposure, UnitPolicy

    req = PickRequest(
        event_start_utc="2099-01-01T00:00:00Z",
        event_id="e1",
        league=League.LOL,
        away_team="GenG",
        home_team="T1",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="Book",
        american_odds=-110,  # implied ~0.5238
        model_probability=0.75,  # edge = 0.75 - 0.5238 = 0.2262 (> 0.10)
        model_uncertainty=0.05,
        model_version="v7",
        rationale="test",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
        observed_at_utc="2026-07-13T11:00:00Z",
        model_artifact_hash="hash",
        calibration_artifact_hash="cal-hash",
        code_revision="rev",
    )
    res = evaluate_gated_research_eligibility(
        req,
        Exposure(),
        UnitPolicy(),
        model_inputs_valid=True,
        minimum_edge=0.02,
        maximum_unreviewed_disagreement=0.10,
    )
    assert res.decision == "CALL"
    assert res.reason_code == "PAPER_CALL_LARGE_DISAGREEMENT_REVIEW"
    assert res.record_type.value == "RESEARCH_OBSERVATION"
