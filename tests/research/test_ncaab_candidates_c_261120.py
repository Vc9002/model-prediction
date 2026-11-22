"""Pure-function tests for the NCAAB 261120 candidates C1_ppp_adjusted / C2_ppp_into_v7 (synthetic data only)."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research" / "candidates"))

import c_ppp_261120 as c


def test_possession_formula() -> None:
    assert c.team_possessions(60, 10, 12, 20) == pytest.approx(60 - 10 + 12 + 8.8)
    home = {"field_goal_attempts": 60, "offensive_rebounds": 10, "turnovers": 12, "free_throw_attempts": 20}
    away = {"field_goal_attempts": 58, "offensive_rebounds": 8, "turnovers": 14, "free_throw_attempts": 10}
    assert c.game_possessions(home, away) == pytest.approx(0.5 * (70.8 + 68.4))


def test_recency_and_blowout_weights() -> None:
    w = c.recency_weight(np.array([0, 40, 60, 80, 200]))
    assert w.tolist() == pytest.approx([1.0, 1.0, 0.8, 0.6, 0.6])
    b = c.blowout_weight(np.array([-5, 20, -30, 60]))
    assert b.tolist() == pytest.approx([1.0, 1.0, 0.8, 0.6])


def test_buckets_and_tail() -> None:
    assert c.sample_bucket(np.array([0, 1, 2, 3, 7, 8, 30])).tolist() == [0, 1, 1, 2, 2, 3, 3]
    assert c.tail_term(np.array([-20.0, 5.0, 14.0])).tolist() == [-8.0, 0.0, 2.0]
    assert c.tier_to_per100(np.array([1500.0 + 28.0 * 6.8]))[0] == pytest.approx(10.0)


def _synthetic_games(rng: np.random.Generator, n_teams: int = 12, n_games: int = 3000):
    o = rng.normal(0, 6, n_teams)
    d = rng.normal(0, 6, n_teams)
    t = rng.normal(0, 3, n_teams)
    hi = rng.integers(0, n_teams, n_games)
    ai = (hi + rng.integers(1, n_teams, n_games)) % n_teams
    venue = (rng.random(n_games) < 0.8).astype(float)
    h, mu, mut = 5.0, 103.0, 68.0
    poss = mut + t[hi] + t[ai] + rng.normal(0, 0.5, n_games)
    eh = mu + o[hi] - d[ai] + venue * h / 2 + rng.normal(0, 1.0, n_games)
    ea = mu + o[ai] - d[hi] - venue * h / 2 + rng.normal(0, 1.0, n_games)
    start0 = datetime(2024, 11, 4, 23, tzinfo=UTC)
    g = c.Games(
        start=[start0 + timedelta(hours=i) for i in range(n_games)],
        day_ord=np.array([(start0 + timedelta(hours=i)).date().toordinal() for i in range(n_games)]),
        season=np.full(n_games, 2025),
        hidx=hi,
        aidx=ai,
        hpts=eh * poss / 100.0,
        apts=ea * poss / 100.0,
        poss=poss,
        venue=venue,
    )
    return g, o, d, t, h


def test_ridge_recovers_known_ratings() -> None:
    rng = np.random.default_rng(3)
    g, o, d, t, h = _synthetic_games(rng)
    n = len(o)
    sel = np.arange(len(g.start))
    G, b, Gt, bt = c.eff_system(g, sel, np.ones(sel.size), n)
    eff = c.solve_eff(G, b, n, 0.01, np.zeros(n), np.zeros(n), 0.0)
    tempo = c.solve_tempo(Gt, bt, n, 0.01, np.zeros(n))
    # O and D are identified up to a shared constant; net ratings and the venue term are exact
    net_true = o + d
    net_fit = eff[:n] + eff[n : 2 * n]
    assert np.allclose(net_fit - net_fit.mean(), net_true - net_true.mean(), atol=0.2)
    assert eff[2 * n] == pytest.approx(h, abs=0.2)
    assert np.allclose(tempo[:n] - tempo[:n].mean(), t - t.mean(), atol=0.1)
    m, tot, p, eh, ea = c.predict_game(eff, tempo, n, 0, 1, neutral=False)
    poss_true = 68.0 + t[0] + t[1]
    gap_true = (o[0] + d[0]) - (o[1] + d[1]) + h
    assert p == pytest.approx(poss_true, abs=0.3)
    assert m == pytest.approx(gap_true * poss_true / 100.0, abs=0.3)
    assert tot == pytest.approx((eh + ea) * p / 100.0)
    m_neutral = c.predict_game(eff, tempo, n, 0, 1, neutral=True)[0]
    assert m - m_neutral == pytest.approx(eff[2 * n] * p / 100.0)


def test_strong_ridge_returns_the_prior() -> None:
    rng = np.random.default_rng(4)
    g, o, *_ = _synthetic_games(rng, n_games=200)
    n = len(o)
    sel = np.arange(len(g.start))
    G, b, _gt, _bt = c.eff_system(g, sel, np.ones(sel.size), n)
    prior = np.linspace(-5, 5, n)
    eff = c.solve_eff(G, b, n, 1e9, prior, -prior, 4.0)
    assert np.allclose(eff[:n], prior, atol=1e-3)
    assert np.allclose(eff[n : 2 * n], -prior, atol=1e-3)


def _events_and_boxes(n_days: int = 30):
    """Synthetic scoreboard events + boxes for four D1 teams, one game per day at 19:00 ET (00:00Z)."""
    rng = np.random.default_rng(5)
    teams = ["1", "2", "3", "4"]
    events, boxes = [], {}
    day0 = date(2024, 11, 10)
    for k in range(n_days):
        h, a = rng.choice(teams, 2, replace=False)
        hp, ap = int(rng.integers(55, 95)), int(rng.integers(55, 95))
        eid = f"e{k}"
        start = datetime.combine(day0 + timedelta(days=k + 1), datetime.min.time()).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
        events.append(
            {
                "event_id": eid,
                "start_utc": start,
                "season_year": 2025,
                "home_team_id": h,
                "away_team_id": a,
                "home_team": f"T{h}",
                "away_team": f"T{a}",
                "home_conference_id": "2",
                "away_conference_id": "2",
                "home_score": hp,
                "away_score": ap,
                "neutral": False,
            }
        )
        side = {
            "field_goal_attempts": 60,
            "offensive_rebounds": 10,
            "turnovers": 12,
            "free_throw_attempts": 20,
        }
        boxes[eid] = {
            "home": {**side, "team_id": h, "points": hp},
            "away": {**side, "team_id": a, "points": ap},
        }
    return events, boxes


def test_point_in_time_future_games_cannot_move_a_prediction() -> None:
    events, boxes = _events_and_boxes()
    variants = [(2.0, 0.6)]  # run_ratings logs season meta for the (LAM_GRID[1], CARRY_GRID[1]) variant
    rows, _ = c.run_ratings(events, boxes, [2025], variants)
    target_day = rows[15]["game_date"]
    base = {r["event_id"]: r for r in rows if r["game_date"] <= target_day}
    # rewrite every game on or after the target day (scores and boxes): earlier predictions must not move
    for e in events:
        if c.eastern_midnight_utc(target_day) <= datetime.strptime(e["start_utc"], "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=UTC
        ):
            e["home_score"] += 40
            boxes[e["event_id"]]["home"]["points"] += 40
    rows2, _ = c.run_ratings(events, boxes, [2025], variants)
    after = {r["event_id"]: r for r in rows2 if r["event_id"] in base}
    key = f"c1_{c.variant_key(*variants[0])}"
    for eid, r in base.items():
        assert after[eid][key] == r[key], eid
    # and the edit does move a later game, so the test can fail
    later = [r for r in rows if r["game_date"] > target_day]
    later2 = {r["event_id"]: r for r in rows2}
    assert any(later2[r["event_id"]][key] != r[key] for r in later)


def test_first_day_of_season_is_pure_prior() -> None:
    events, boxes = _events_and_boxes(5)
    rows, _ = c.run_ratings(events, boxes, [2025], [(2.0, 0.6)])
    first = rows[0]
    assert first["n_fit_games"] == 0
    # same conference tier for every team, no history: only the home edge separates them
    hca_points = c.HCA_PRIOR_POINTS * 100.0 / c.REF_POSS * c.REF_POSS / 100.0
    assert first["c1_l2_c0.6"] == pytest.approx(hca_points, abs=1e-6)


def test_select_variant_and_ols() -> None:
    df = pd.DataFrame({"margin": [1.0, 2.0, 3.0], "c1_a": [1.0, 2.0, 3.5], "c1_b": [0.0, 0.0, 0.0]})
    best, rm = c.select_variant(df, ["a", "b"])
    assert best == "a" and rm["a"] < rm["b"]
    x = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    assert c.ols_no_intercept(x, x @ np.array([2.0, -1.0])).tolist() == pytest.approx([2.0, -1.0])
