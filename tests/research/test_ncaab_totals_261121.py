"""Addendum v3 totals grader and candidates (quick 261121-hmc, lane T). Never a real read: the read log is
redirected to a temp file and every outcome here is synthetic."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "scripts" / "ncaab_research", REPO / "scripts" / "ncaab_research" / "candidates"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import grader
import grader_v3
import t_totals_261121 as tt


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log = tmp_path / "read_log.jsonl"
    monkeypatch.setattr(grader_v3, "READ_LOG", log)
    return log


# ------------------------------------------------------------------------------------------ pin
def test_addendum_v3_pin_matches_and_k():
    add = grader_v3.load_addendum_v3()
    assert grader.canonical_sha256(add) == grader_v3.ADDENDUM_V3_SHA256
    assert add["multiplicity"]["K"] == {"total": 26, "spread_market_relative": 23}
    assert set(add["candidate_grid"]) == set(grader_v3.GRID)
    assert add["windows"]["confirmation_totals"]["season_years"] == [2018, 2019, 2020, 2022]


def test_tampered_addendum_refused(tmp_path):
    add = json.loads(grader_v3.ADDENDUM_V3_PATH.read_text(encoding="utf-8"))
    add["multiplicity"]["K"]["total"] = 25
    p = tmp_path / "v3.json"
    p.write_text(json.dumps(add), encoding="utf-8")
    with pytest.raises(grader.ProtocolMismatchError):
        grader_v3.load_addendum_v3(p)


def test_v1_and_v2_pins_unchanged():
    proto = grader.load_protocol()  # verifies v1 and addendum v2
    assert proto["multiplicity"]["K"]["total_market_relative"] == 20


# ------------------------------------------------------------------------------------------ submissions
def _meta(**kw):
    m = {
        "candidate": "x",
        "grid_name": "T1_tempo_eff_total",
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v3_sha256": grader_v3.ADDENDUM_V3_SHA256,
        "code_sha256": "0" * 64,
        "fit_seasons_by_predicted_season": {"2023": [2024, 2025, 2026], "2024": [2023, 2025, 2026]},
    }
    m.update(kw)
    return m


def test_meta_refuses_fit_on_predicted_season():
    bad = _meta(fit_seasons_by_predicted_season={"2023": [2023, 2024]})
    with pytest.raises(grader.SubmissionError, match="leak"):
        grader_v3.validate_meta_v3(bad, [2023])


def test_meta_refuses_locked_fit_season_and_wrong_pin():
    with pytest.raises(grader.LockedWindowError):
        grader_v3.validate_meta_v3(_meta(fit_seasons_by_predicted_season={"2023": [2027]}), [2023])
    with pytest.raises(grader.SubmissionError, match="addendum_v3"):
        grader_v3.validate_meta_v3(_meta(addendum_v3_sha256="f" * 64), [2023])


def test_meta_registration():
    assert grader_v3.validate_meta_v3(_meta(), [2023, 2024])["family"] == "mean"
    assert grader_v3.validate_meta_v3(_meta(grid_name="T3_total_scale"), [2023])["family"] == "scale"
    info = grader_v3.validate_meta_v3(_meta(grid_name="T0_total_diagnostics"), [2023])
    assert info["registered"] is False


def test_locked_prediction_row_refused():
    row = {
        "event_id": "1",
        "season": 2027,
        "game_date": "2026-11-20",
        "home_team": "a",
        "away_team": "b",
        "neutral": False,
        "mu_margin": 1.0,
        "scale_margin": 10.0,
        "dist": "t8",
        "games_played_min": 0,
        "mu_total": 140.0,
        "scale_total": 17.0,
    }
    with pytest.raises(grader.LockedWindowError):
        grader_v3.score_totals(pd.DataFrame([row]), _meta(), archive=pd.DataFrame())


# ------------------------------------------------------------------------------------------ read log
def test_read_log_counts_distinct_files_per_grid_and_window(tmp_log):
    e = {"grid_name": "T1_tempo_eff_total", "candidate": "x", "window": "development"}
    assert grader_v3.append_read_log({**e, "predictions_sha256": "a"}) == 1
    assert grader_v3.append_read_log({**e, "predictions_sha256": "a"}) == 1
    assert grader_v3.append_read_log({**e, "predictions_sha256": "b"}) == 2
    assert grader_v3.append_read_log({**e, "window": "confirmation", "predictions_sha256": "c"}) == 1
    lines = [json.loads(x) for x in tmp_log.read_text(encoding="utf-8").splitlines()]
    assert all("grid_name" in x and "predictions_sha256" in x for x in lines)
    assert all(x["addendum_v3_sha256"] == grader_v3.ADDENDUM_V3_SHA256 for x in lines)
    with pytest.raises(ValueError):
        grader_v3.append_read_log({"grid_name": "x", "window": "development", "candidate": "x"})


def test_reread_gives_no_verdict():
    v = {"status": "DEV_ELIGIBLE"}
    assert grader_v3.reread_guard(v, True, 1, "T1_tempo_eff_total") is v
    out = grader_v3.reread_guard(v, True, 2, "T1_tempo_eff_total")
    assert out["status"] == "REREAD_NO_VERDICT" and out["would_have_been"] is v
    assert grader_v3.reread_guard(v, True, None, "T1_tempo_eff_total") is v  # shuffled plumbing


# ------------------------------------------------------------------------------------------ verdicts
def _paired(corr_lo=0.01, rmse=-0.01, late=0.01, ll_hi=-0.001, ln2_hi=-0.001, ll_late=-0.001, brier=-0.0001):
    return {
        "total_corr_delta_vs_v6": {"ci_adj": [corr_lo, 0.1], "late_folds_point": late},
        "total_rmse_delta_vs_v6": {"delta": rmse},
        "over_logloss_delta_vs_v6": {"ci_adj": [-0.1, ll_hi], "late_folds_point": ll_late},
        "over_logloss_vs_ln2": {"ci_adj": [-0.1, ln2_hi]},
        "over_brier_delta_vs_v6": {"delta": brier},
    }


def test_verdict_mean_and_scale_rules():
    assert grader_v3.verdict_v3("mean", True, _paired(), True, [])["status"] == "DEV_ELIGIBLE"
    assert grader_v3.verdict_v3("mean", True, _paired(corr_lo=-0.001), True, [])["status"] == "NOT_ELIGIBLE"
    assert grader_v3.verdict_v3("mean", True, _paired(rmse=0.01), True, [])["status"] == "NOT_ELIGIBLE"
    assert grader_v3.verdict_v3("mean", True, _paired(late=-0.01), True, [])["status"] == "NOT_ELIGIBLE"
    assert grader_v3.verdict_v3("scale", True, _paired(), True, [])["status"] == "DEV_ELIGIBLE"
    # beating the overdispersed served v6 is not enough: it must also beat a coin
    assert grader_v3.verdict_v3("scale", True, _paired(ln2_hi=0.001), True, [])["status"] == "NOT_ELIGIBLE"
    assert grader_v3.verdict_v3("mean", True, _paired(), False, [])["status"] == "INCOMPLETE"
    assert grader_v3.verdict_v3("mean", True, _paired(), True, ["x"])["status"] == "LEAK_SUSPECT"


def test_leak_flags_totals():
    cand = {
        "total_by_season": {2025: {"rmse": 16.0}},
        "market_total": {"by_season": {2025: {"corr": 0.2}}},
    }
    flags = grader_v3.leak_flags_totals(cand, {2025: {"rmse": 16.5}})
    assert len(flags) == 2


def _frame(n=600, seed=3):
    rng = np.random.default_rng(seed)
    line = rng.normal(140, 8, n).round() + 0.5
    total = line + rng.normal(0, 16, n).round()
    days = [f"2025-01-{1 + i % 28:02d}" for i in range(n)]
    v6 = line + rng.normal(1, 4, n)
    t = pd.DataFrame(
        {
            "game_date": days,
            "season": np.where(np.arange(n) % 2 == 0, 2025, 2023),
            "total": total,
            "total_line": line,
            "v6_mu_total": v6,
            "cand_mu_total": v6,
        }
    )
    sd = np.full(n, 17.0)
    t["v6_p_over"] = grader_v3.p_over(v6, sd, line)
    t["cand_p_over"] = t["v6_p_over"]
    return t


def test_paired_identical_candidate_is_zero():
    p = grader_v3.paired_vs_v6(_frame())
    assert p["mu_equals_v6"] is True
    assert abs(p["total_corr_delta_vs_v6"]["delta"]) < 1e-12
    assert abs(p["total_rmse_delta_vs_v6"]["delta"]) < 1e-12
    assert abs(p["over_logloss_delta_vs_v6"]["delta"]) < 1e-12


def test_asof_uses_only_earlier_dates(monkeypatch):
    monkeypatch.setattr(grader_v3, "ASOF_MIN_PRIOR", 1)
    t = pd.DataFrame(
        {"season": [2025] * 4, "game_date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-03"]}
    )
    t["x"] = [1.0, 3.0, 10.0, 100.0]
    got = grader_v3._asof_by_season(t, "x")
    assert np.isnan(got[0]) and np.isnan(got[1])
    assert got[2] == 2.0 and got[3] == pytest.approx(14.0 / 3.0)


def test_load_periods_overtime_and_mismatch(tmp_path, monkeypatch):
    games = [
        {
            "sourceId": "1",
            "homePeriodPoints": [30, 40],
            "awayPeriodPoints": [35, 30],
            "homePoints": 70,
            "awayPoints": 65,
        },
        {
            "sourceId": "2",
            "homePeriodPoints": [30, 35, 8],
            "awayPeriodPoints": [33, 32, 6],
            "homePoints": 73,
            "awayPoints": 71,
        },
        {
            "sourceId": "3",
            "homePeriodPoints": [30, 35],
            "awayPeriodPoints": [33, 32],
            "homePoints": 70,
            "awayPoints": 65,
        },
    ]
    (tmp_path / "g.json").write_text(json.dumps(games), encoding="utf-8")
    monkeypatch.setattr(grader_v3, "CBBD_GAMES", tmp_path)
    per = grader_v3.load_periods(["1", "2", "3", "4"])
    assert per["1"]["n_ot"] == 0 and per["1"]["reg_total"] == 135
    assert per["2"]["n_ot"] == 1 and per["2"]["reg_total"] == 130
    assert "3" not in per and "4" not in per  # sums disagree with the final score / not in cache


# ------------------------------------------------------------------------------------------ candidate math
def test_poss_per_40_and_overtime_total():
    assert tt.poss_per_40(np.array([75.0]), np.array([1]))[0] == pytest.approx(75.0 * 40 / 45)
    assert tt.poss_per_40(np.array([68.0]), np.array([0]))[0] == 68.0
    tot = tt.t1_total(np.array([210.0]), np.array([68.0]), np.array([0.05]), 1.2)[0]
    assert tot == pytest.approx(2.10 * 68.0 * (1 + 1.2 * 0.05 / 8))


def test_p_tie_is_symmetric_and_small():
    a, b = tt.p_tie(np.array([3.0, -3.0]), np.array([11.0, 11.0]))
    assert a == pytest.approx(b)
    even = tt.p_tie(np.array([0.0]), np.array([11.0]))[0]
    assert 0.02 < even < 0.05 and even > a


def test_fit_c_ot_and_ridge_recover():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.01, 0.05, 5000)
    n_ot = (rng.uniform(size=5000) < 1.5 * p).astype(float)
    assert tt.fit_c_ot(n_ot, p) == pytest.approx(1.5, abs=0.15)
    x = rng.normal(size=(4000, 3)) * [10, 5, 1] + [140, 2, 0]
    y = 3 + x @ np.array([0.7, -0.4, 2.0]) + rng.normal(0, 1, 4000)
    fit = tt.ridge_fit(x, y, alpha=1e-6)
    assert np.allclose(np.array(fit["beta"]) / np.array(fit["sd"]), [0.7, -0.4, 2.0], atol=0.02)
    assert np.allclose(tt.ridge_predict(fit, x[:5]), (3 + x[:5] @ np.array([0.7, -0.4, 2.0])), atol=0.5)


def test_fit_scale_t3_recovers():
    rng = np.random.default_rng(2)
    poss = rng.uniform(60, 76, 20000)
    sample = rng.integers(0, 20, 20000)
    sd = tt.t3_sd(16.0, 0.3, poss, sample)
    s0, c1 = tt.fit_scale_t3(rng.normal(0, sd), poss, sample)
    assert s0 == pytest.approx(16.0, rel=0.02) and c1 == pytest.approx(0.3, abs=0.05)


def test_league_levels_point_in_time():
    ev = []
    for i in range(60):  # previous season: totals 120
        ev.append(
            {
                "event_id": f"p{i}",
                "season_year": 2024,
                "start_utc": "2024-01-10T00:00Z",
                "home_score": 60,
                "away_score": 60,
                "home_conference_id": "1",
                "away_conference_id": "2",
            }
        )
    for i in range(60):  # current season games on 2025-01-09 evening ET (UTC next day 00:30)
        ev.append(
            {
                "event_id": f"c{i}",
                "season_year": 2025,
                "start_utc": "2025-01-10T00:30Z",
                "home_score": 80,
                "away_score": 80,
                "home_conference_id": "1",
                "away_conference_id": "2",
            }
        )
    lv = tt.league_levels(ev, [(2025, "2025-01-09"), (2025, "2025-01-10")])
    assert lv[(2025, "2025-01-09")] == 120.0  # same-day games are not history: falls back to last season
    assert lv[(2025, "2025-01-10")] == 160.0


def test_fold_fit_never_sees_held_out(monkeypatch):
    rng = np.random.default_rng(4)
    rows = []
    for s in (2023, 2024, 2025, 2026):
        for i in range(300):
            r = {
                "event_id": f"{s}-{i}",
                "season": s,
                "sample": int(rng.integers(0, 20)),
                "neutral": False,
                "cohort_eligible": True,
                "v6_total": 140 + rng.normal(0, 5),
                "league_level": 140.0,
                "margin": float(rng.normal(0, 11)),
                "total": float(rng.normal(140, 16)),
                "n_ot": 0.0,
            }
            for key in tt.VARIANT_KEYS:
                r[f"m_{key}"] = rng.normal(0, 8)
                r[f"se_{key}"] = 205 + rng.normal(0, 5)
                r[f"p40_{key}"] = 68 + rng.normal(0, 3)
            rows.append(r)
    df = pd.DataFrame(rows)
    df.loc[df["season"] == 2025, ["margin", "total"]] = np.nan  # held-out outcomes unavailable
    f = tt.fold_fit(df, 2025, (2023, 2024, 2025, 2026))
    assert f["fit_seasons"] == [2023, 2024, 2026] and f["t1"]["variant"] in tt.VARIANT_KEYS
    assert math.isfinite(f["t3"]["s0"]) and len(f["t2"]["fit"]["beta"]) == len(tt.T2_FEATURES)
