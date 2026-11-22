"""Pure-function tests for the inert NCAAB v7 shadow challenger (quick 261115-1m7)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from model_prediction.models.ncaab_spread_v6 import ARTIFACT_PATH, load_verified_artifact
from model_prediction.models.ncaab_v7_shadow import (
    MARGIN_FEATURES,
    V7Components,
    V7Config,
    decline_reason,
    fit_efficiency_pace,
    fit_least_squares,
    margin_feature,
    margin_from_components,
    predict_efficiency_pace_total,
    ridge_team_strengths,
    scale_bucket,
    season_weights,
    shrunk_mean,
    spread_distribution,
    student_t_scale_mle,
    thin_factor,
    total_from_components,
    win_probability,
)

ROOT = Path(__file__).resolve().parents[1]


def _comp(**over) -> V7Components:
    base = {
        "event_id": "e1",
        "home": "H",
        "away": "A",
        "neutral": False,
        "d1_home": True,
        "d1_away": True,
        "games_home": 10,
        "games_away": 10,
        "v6_mean": 8.0,
        "v6_scale": 10.5,
        "v6_sd": 10.5,
        "v6_p_home": 0.7,
        "elo_margin": 6.0,
        "trend_margin": 10.0,
        "rest_diff": 0.0,
        "sample": 10,
        "ridge_c0": 7.0,
        "ridge_c05": 7.5,
        "ridge_c10": 8.0,
        "ridge_hca": 3.5,
        "team_hca_sum_home": 5.0,
        "team_hca_n_home": 5,
        "rest_days_home": 3.0,
        "rest_days_away": 1.0,
        "games4_home": 1,
        "games4_away": 2,
        "v6_total": 140.0,
        "v6_total_sd": 16.5,
        "effpace_total": 150.0,
    }
    base.update(over)
    return V7Components(**base)


def test_ridge_recovers_strengths_and_home_edge() -> None:
    rng = np.random.default_rng(3)
    n_teams = 12
    true = rng.normal(0, 6, n_teams)
    home = rng.integers(0, n_teams, 900)
    away = (home + rng.integers(1, n_teams, 900)) % n_teams
    margin = true[home] - true[away] + 4.0 + rng.normal(0, 3, 900)
    ratings, hca = ridge_team_strengths(
        home, away, margin, np.ones(900), np.zeros(900, bool), n_teams, np.zeros(n_teams), lam=1.0
    )
    # ratings are identified up to a constant; compare differences
    assert np.corrcoef(ratings - ratings.mean(), true - true.mean())[0, 1] > 0.99
    assert hca == pytest.approx(4.0, abs=0.3)


def test_ridge_with_no_games_returns_the_prior() -> None:
    prior = np.array([3.0, -2.0, 0.5])
    ratings, hca = ridge_team_strengths(
        np.array([], int),
        np.array([], int),
        np.array([]),
        np.array([]),
        np.array([], bool),
        3,
        prior,
        lam=5.0,
    )
    assert np.allclose(ratings, prior)
    assert hca == pytest.approx(3.5)


def test_ridge_prior_is_the_early_season_fallback() -> None:
    # one blowout cannot move a lam=5 prior all the way to the observed margin
    ratings, _ = ridge_team_strengths(
        np.array([0]),
        np.array([1]),
        np.array([30.0]),
        np.ones(1),
        np.array([False]),
        2,
        np.array([5.0, -5.0]),
        5.0,
    )
    assert 5.0 < ratings[0] - ratings[1] < 30.0


def test_neutral_games_carry_no_home_indicator() -> None:
    home = np.array([0, 1] * 40)
    away = np.array([1, 0] * 40)
    margin = np.zeros(80)  # symmetric neutral results: no home edge should be learned
    _, hca = ridge_team_strengths(home, away, margin, np.ones(80), np.ones(80, bool), 2, np.zeros(2), 1.0)
    assert hca == pytest.approx(3.5)  # untouched prior: no non-neutral game informs it


def test_season_weights_carry() -> None:
    w = season_weights(np.array([2026, 2027, 2027]), 2027, 0.5)
    assert w.tolist() == [0.5, 1.0, 1.0]
    assert season_weights(np.array([2026]), 2027, 0.0).tolist() == [0.0]


def test_shrunk_mean_and_thin_factor() -> None:
    assert shrunk_mean(10.0, 10, 10.0) == pytest.approx(0.5)
    assert shrunk_mean(0.0, 0, 0.0) == 0.0
    assert thin_factor(0) == 1.0
    assert thin_factor(4) == pytest.approx(0.5)
    assert thin_factor(20) == 0.0


def test_student_t_scale_mle_recovers_scale() -> None:
    draws = stats.t.rvs(df=8, scale=11.0, size=20000, random_state=5)
    assert student_t_scale_mle(draws) == pytest.approx(11.0, rel=0.03)
    with pytest.raises(ValueError):
        student_t_scale_mle([1.0, 2.0])


def test_least_squares_recovers_coefficients() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(500, 3))
    y = x @ np.array([1.5, -0.5, 0.25])
    assert np.allclose(fit_least_squares(x, y), [1.5, -0.5, 0.25], atol=1e-4)


def test_win_probability_is_symmetric_and_integer_margin_aware() -> None:
    assert win_probability(0.0, 10.0) == pytest.approx(0.5, abs=0.03)
    assert win_probability(8.0, 10.0) + win_probability(-8.0, 10.0) == pytest.approx(1.0, abs=0.04)
    assert win_probability(30.0, 5.0) <= 0.9999


def test_efficiency_pace_decomposition_is_exact_without_noise() -> None:
    rng = np.random.default_rng(2)
    n_teams, n = 8, 600
    pace = rng.normal(0, 3, n_teams)
    off = rng.normal(0, 0.07, n_teams)
    dfn = rng.normal(0, 0.07, n_teams)
    home = rng.integers(0, n_teams, n)
    away = (home + rng.integers(1, n_teams, n)) % n_teams
    neutral = np.zeros(n, bool)
    poss = 68.0 + pace[home] + pace[away]
    hp = poss * (1.0 + off[home] + dfn[away] + 0.03)
    ap = poss * (1.0 + off[away] + dfn[home])
    fit = fit_efficiency_pace(home, away, neutral, poss, hp, ap, n_teams, lam_pace=1e-3, lam_eff=1e-3)
    pred = predict_efficiency_pace_total(fit, 2, 5, False)
    truth = (68.0 + pace[2] + pace[5]) * (2.0 + off[2] + dfn[5] + 0.03 + off[5] + dfn[2])
    assert pred == pytest.approx(truth, rel=0.01)
    # neutral drops the home edge from the home side only
    assert predict_efficiency_pace_total(fit, 2, 5, True) < pred


def test_every_registered_margin_feature_evaluates() -> None:
    comp = _comp()
    for name in MARGIN_FEATURES:
        assert math.isfinite(margin_feature(comp, name)), name
    with pytest.raises(KeyError):
        margin_feature(comp, "nope")


def test_margin_feature_semantics() -> None:
    comp = _comp(v6_mean=-20.0, sample=0)
    assert margin_feature(comp, "afav") == -20.0
    assert margin_feature(_comp(v6_mean=5.0), "afav") == 0.0
    assert margin_feature(comp, "v6_tail") == pytest.approx(-8.0)  # |20| - 12 knot, sign kept
    assert margin_feature(_comp(v6_mean=10.0), "v6_tail") == 0.0
    assert margin_feature(comp, "v6_thin") == pytest.approx(-20.0)  # sample 0 -> full thin factor
    assert margin_feature(_comp(neutral=True), "team_hca_k10") == 0.0  # neutral: no venue effect
    assert margin_feature(_comp(), "team_hca_k10") == pytest.approx(5.0 / 15.0)
    # away on a back-to-back and home rested: home-positive
    assert margin_feature(_comp(rest_days_away=1.0, rest_days_home=3.0), "b2b") == 1.0


def test_combiner_routes_d1_and_non_d1() -> None:
    cfg = V7Config(
        margin_names=("ridge_c05", "v6_tail"),
        margin_coef=(0.9, 1.0),
        nond1_rule="linear",
        nond1_names=("v6", "const"),
        nond1_coef=(0.5, 20.0),
        scale_values=(13.0, 12.0, 11.0, 10.0),
        nond1_scale=17.0,
    )
    d1 = _comp(v6_mean=14.0)
    assert margin_from_components(d1, cfg) == pytest.approx(0.9 * 7.5 + 1.0 * 2.0)
    nd = _comp(d1_away=False, v6_mean=14.0)
    assert margin_from_components(nd, cfg) == pytest.approx(0.5 * 14.0 + 20.0)
    _, scale_nd, _ = spread_distribution(nd, cfg)
    assert scale_nd == 17.0
    _, scale_d1, _ = spread_distribution(_comp(sample=0), cfg)
    assert scale_d1 == 13.0
    assert spread_distribution(_comp(sample=9), cfg)[1] == 10.0


def test_non_d1_v6_rule_and_decline_reasons() -> None:
    cfg = V7Config(margin_names=("v6",), margin_coef=(1.0,), nond1_rule="decline", min_games_decline=3)
    nd = _comp(d1_home=False)
    assert margin_from_components(nd, cfg) == nd.v6_mean  # declined games still carry the v6 number
    assert decline_reason(nd, cfg) == "NO_CALL_NON_D1"
    assert decline_reason(_comp(games_home=2), cfg) == "NO_CALL_THIN_SAMPLE"
    assert decline_reason(_comp(), cfg) is None
    assert decline_reason(nd, V7Config(margin_names=("v6",), margin_coef=(1.0,))) is None


def test_scale_bucket_edges() -> None:
    edges = (0, 1, 3, 8)
    assert [scale_bucket(s, edges) for s in (0, 1, 2, 3, 7, 8, 30)] == [0, 1, 1, 2, 2, 3, 3]


def test_total_combiner_falls_back_to_v6_when_effpace_missing() -> None:
    cfg = V7Config(
        margin_names=("v6",),
        margin_coef=(1.0,),
        total_names=("v6_total", "effpace_total"),
        total_coef=(0.5, 0.5),
    )
    assert total_from_components(_comp(), cfg) == pytest.approx(145.0)
    assert total_from_components(_comp(effpace_total=None), cfg) == 140.0
    assert total_from_components(_comp(v6_total=None), cfg) is None


def test_v7_is_inert_and_v6_is_untouched() -> None:
    # not registered as a production route, no artifact in config/models
    assert "ncaab-v7" not in (ROOT / "config" / "production.yaml").read_text(encoding="utf-8")
    assert "ncaab-v7" not in (ROOT / "config" / "model.yaml").read_text(encoding="utf-8")
    assert not list((ROOT / "config" / "models").glob("ncaab-v7*"))
    # the v6 champion artifact still verifies against its embedded hash
    assert load_verified_artifact(ARTIFACT_PATH)["model_id"] == "ncaab-spread-v6"


def test_carryover_is_active_on_the_season_opener() -> None:
    """Regression: the fit guard once counted only current-season games, so ridge_c05/c10 silently
    equalled the bare prior on opening day, when prior-season games are all there is."""
    from datetime import UTC, datetime, timedelta

    from model_prediction.features.base import GameRecord
    from model_prediction.models.basketball import UpcomingGame
    from model_prediction.models.ncaab_v7_shadow import NCAABV7ShadowModel

    model = NCAABV7ShadowModel(with_totals=False)
    teams = sorted(model._d1)[:2]
    home, away = teams
    start = datetime(2026, 1, 3, 20, tzinfo=UTC)
    history = []
    for i in range(40):  # 40 prior-season games, the "home" team always wins big
        when = (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%MZ")
        history.append(
            GameRecord(
                event_id=f"g{i}",
                event_start_utc=when,
                league="NCAAB",
                away_team=away,
                home_team=home,
                away_score=60,
                home_score=90,
                season_year=2026,
            )
        )
    opener = (start + timedelta(days=300)).strftime("%Y-%m-%dT%H:%MZ")
    (comp,) = model.components(history, [UpcomingGame("opener", opener, away, home, league="NCAAB")])
    assert comp.games_home == 0  # nothing from the new season yet
    assert comp.ridge_c05 != pytest.approx(comp.ridge_c0)  # carryover moved the rating
    assert comp.ridge_c10 > comp.ridge_c0  # the prior season said the home team is much better
