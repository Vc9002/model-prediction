"""D1_prior_regression candidate (protocol 261120): prior maths, ridge equality, fold guard, point in time."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research" / "candidates"))

import d1_prior_regression as d1

from model_prediction.models.ncaab_v7_multiseason import (
    NCAABV7MultiSeasonModel,
    accumulate_normal_equations,
    ridge_by_accumulation,
)


# ------------------------------------------------------------------------------------------- prior maths
def test_conference_targets_and_regressed_prior() -> None:
    final_prev = np.array([10.0, 4.0, -2.0, 6.0, 1.0, 3.0, 8.0])
    has_prev = np.array([True, True, True, True, True, False, True])
    # conf "2": teams 0,1,2 (mean 4); conf "4": teams 3,4 (only 2 rated -> no target); team 5 unrated;
    # team 6 an independent (id 43) -> no target
    conf = ["2", "2", "2", "4", "4", "2", "43"]
    cm, valid = d1.conference_targets(final_prev, has_prev, conf)
    assert valid.tolist() == [True, True, True, False, False, False, False]
    assert np.allclose(cm[:3], 4.0)
    bonus = np.array([0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    fallback = np.arange(7, dtype=float) * 100.0
    p0 = d1.regressed_prior(final_prev, cm, valid, bonus, fallback, 0.0)
    p1 = d1.regressed_prior(final_prev, cm, valid, bonus, fallback, 1.0)
    ph = d1.regressed_prior(final_prev, cm, valid, bonus, fallback, 0.25)
    assert np.allclose(p0[:3], final_prev[:3] + bonus[:3])  # r = 0: last season's rating as is
    assert np.allclose(p1[:3], 4.0 + bonus[:3])  # r = 1: the conference mean
    assert np.allclose(ph[:3], 4.0 + 0.75 * (final_prev[:3] - 4.0) + bonus[:3])
    for p in (p0, p1, ph):
        assert np.allclose(p[3:], fallback[3:])  # no target -> v7's tier prior


def test_conference_targets_exclude_non_d1_this_season() -> None:
    _cm, valid = d1.conference_targets(np.ones(4), np.ones(4, bool), ["2", "2", "2", None])
    assert valid.tolist() == [True, True, True, False]


def test_solve_many_equals_ridge_by_accumulation() -> None:
    rng = np.random.default_rng(0)
    n, m = 12, 200
    h = rng.integers(0, n, m)
    a = (h + rng.integers(1, n, m)) % n
    margin = rng.normal(0, 12, m)
    w = rng.choice([1.0, 0.1, 0.01], m)
    venue = rng.choice([0.0, 1.0], m)
    priors = rng.normal(0, 5, (n, 3))
    gram = accumulate_normal_equations(h, a, margin, w, venue, n)
    rr, hh = d1.solve_many(gram, priors, d1.LAM)
    for j in range(3):
        r, hca = ridge_by_accumulation(h, a, margin, w, venue, n, priors[:, j], d1.LAM)
        assert np.allclose(rr[:, j], r, atol=1e-10)
        assert abs(hh[j] - hca) < 1e-10


# ------------------------------------------------------------------------------------------- fold fit
def _fold_frame(seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in (2023, 2024, 2025):
        for _ in range(600):
            base = rng.normal(0, 10)
            row = {"season": s, "v6_mean": base + rng.normal(0, 2), "sample": int(rng.integers(0, 20))}
            for r in d1.R_GRID:
                row[d1.r_key(r)] = base + (r - 0.3) * rng.normal(0, 4)
            row["ridge_off"] = base + rng.normal(0, 3)
            row["margin"] = base + rng.normal(0, 3)
            rows.append(row)
    return pd.DataFrame(rows)


def test_fit_fold_refuses_predicted_season() -> None:
    df = _fold_frame()
    with pytest.raises(ValueError, match="leak"):
        d1.fit_fold(df, 2024)


def test_fit_fold_picks_the_informative_r() -> None:
    fold = d1.fit_fold(_fold_frame(), 2026)
    assert fold["r_selected"] == 0.3
    assert fold["fit_seasons"] == [2023, 2024, 2025]
    assert len(fold["config"]["scales"]) == 4
    assert fold["config"]["coef"][0] == pytest.approx(1.0, abs=0.05)


# ------------------------------------------------------------------------------------------- engine, PIT
TEAMS = {"2": ["A1", "A2", "A3", "A4"], "4": ["B1", "B2", "B3", "B4"]}


def _events(seed: int = 3) -> list[dict]:
    rng = np.random.default_rng(seed)
    strength = {t: rng.normal(0, 6) for ts in TEAMS.values() for t in ts}
    conf = {t: c for c, ts in TEAMS.items() for t in ts}
    names = sorted(strength)
    out = []
    for season, start in (
        (2022, datetime(2021, 11, 8, 23, tzinfo=UTC)),
        (2023, datetime(2022, 11, 7, 23, tzinfo=UTC)),
    ):
        for i in range(84):
            h, a = rng.choice(names, 2, replace=False)
            m = round(strength[h] - strength[a] + 3 + rng.normal(0, 10)) or 1
            hs = 70 + max(m, 0)
            aw = 70 + max(-m, 0)
            out.append(
                {
                    "event_id": f"{season}{i:03d}",
                    "start_utc": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%MZ"),
                    "home_team": str(h),
                    "away_team": str(a),
                    "home_score": hs,
                    "away_score": aw,
                    "season_type": 2,
                    "season_year": season,
                    "competition_type": "STD",
                    "neutral": False,
                    "home_conference_id": conf[h],
                    "away_conference_id": conf[a],
                }
            )
    return out


class _Stats:
    def get(self, _eid: str) -> None:
        return None

    def last_known_ranks(self, _hist: object) -> dict:
        return {}


def _engine(events: list[dict]) -> d1.RidgeEngine:
    from model_prediction.models.ncaab_v7_multiseason import build_asof_conferences

    conf_ids = build_asof_conferences(events)
    teams = sorted({t for m in conf_ids.values() for t in m})
    tier = {"2": 1600.0, "4": 1450.0}
    fake = SimpleNamespace(
        team_index={t: i for i, t in enumerate(teams)},
        tier_by_season={s: {t: tier[c] for t, c in m.items()} for s, m in conf_ids.items()},
        seasons=sorted(conf_ids),
        _stats=_Stats(),
        rank_prior_per_slot=8.0,
    )
    fake.current_season = lambda hist, start: NCAABV7MultiSeasonModel.current_season(fake, hist, start)
    return d1.RidgeEngine(events, model=fake)


def _rows(eng: d1.RidgeEngine, day_index: int) -> list[dict]:
    from model_prediction.domain import EASTERN

    g2023 = [g for g in eng.games if g.season_year == 2023]
    day = g2023[day_index].start.astimezone(EASTERN).date().isoformat()
    todays = [g for g in g2023 if g.start.astimezone(EASTERN).date().isoformat() == day]
    return eng.day_rows(day, todays)


def test_day_rows_ignore_games_on_or_after_the_day() -> None:
    ev = _events()
    base = _rows(_engine(ev), 30)
    changed = [dict(e) for e in ev]
    for e in changed:
        if e["season_year"] == 2023 and int(e["event_id"][-3:]) >= 30:  # the day itself and later
            e["home_score"], e["away_score"] = e["away_score"] + 40, e["home_score"]
    assert _rows(_engine(changed), 30) == base
    earlier = [dict(e) for e in ev]
    earlier[84 + 10]["home_score"] += 40  # an earlier current-season game must matter
    assert _rows(_engine(earlier), 30) != base


def test_previous_season_ratings_ignore_current_season_games() -> None:
    ev = _events()
    eng_a = _engine(ev)
    changed = [dict(e) for e in ev]
    for e in changed:
        if e["season_year"] == 2023:
            e["home_score"] += 25
    eng_b = _engine(changed)
    k = len(eng_a.games)
    ra, ha = eng_a.prev_final(2023, k)
    rb, hb = eng_b.prev_final(2023, k)
    assert np.array_equal(ra, rb) and np.array_equal(ha, hb)
    assert ha.all()  # every team played >= MIN_PREV_GAMES in 2022


def test_off_when_no_previous_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d1, "MIN_PREV_GAMES", 10**6)
    rows = _rows(_engine(_events()), 30)
    assert rows
    for r in rows:
        assert not r["has_prev_home"] and not r["has_prev_away"]
        for rv in d1.R_GRID:
            assert r[d1.r_key(rv)] == pytest.approx(r["ridge_off"], abs=1e-4)


def test_regressed_prior_moves_the_early_ridge() -> None:
    rows = _rows(_engine(_events()), 0)  # first day of 2023: no current-season games yet
    assert rows and all(r["has_prev_home"] for r in rows)
    assert any(abs(r[d1.r_key(0.0)] - r[d1.r_key(1.0)]) > 0.1 for r in rows)
