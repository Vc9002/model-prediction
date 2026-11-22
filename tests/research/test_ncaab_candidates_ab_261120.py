"""Pure-function tests for the 261120 A/B grid candidates (synthetic data, no archive reads)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research" / "candidates"))
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research"))

import a_ml_w0 as a_mod
import ab_common_261120 as ab
import b1_scale_linear_mu as b1
import b2_target_cap as b2
import b3_early_scale_floor as b3
import grader as g


def _frame(n: int = 6000, seed: int = 0, seasons=(2023, 2024, 2025, 2026)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    season = rng.choice(seasons, n)
    sample = rng.integers(0, 20, n)
    ridge = rng.normal(0, 10, n)
    v6 = ridge + rng.normal(0, 3, n)
    mu = 0.96 * ridge + 0.16 * ab.tail_term(v6)
    return pd.DataFrame(
        {
            "event_id": [str(i) for i in range(n)],
            "season": season,
            "game_date": "2024-01-01",
            "home_team": "H",
            "away_team": "A",
            "neutral": False,
            "games_played_min": sample,
            "mu_v7": mu,
            "scale_v7": np.array([11.27, 10.87, 10.71, 9.97])[ab.bucket(sample)],
            "elo": rng.uniform(0.05, 0.95, n),
            "v6_mean": v6,
            "ridge": ridge,
            "margin": np.round(mu + 10 * rng.standard_t(8, n)),
        }
    )


def test_bucket_edges() -> None:
    assert ab.bucket([0, 1, 2, 3, 7, 8, 30]).tolist() == [0, 1, 1, 2, 2, 3, 3]


def test_t8_nll_and_scale_mle() -> None:
    rng = np.random.default_rng(1)
    r = 9.0 * rng.standard_t(8, 40000)
    assert ab.t8_nll(r, 9.0) == pytest.approx(-np.sum(stats.t.logpdf(r / 9.0, 8) - np.log(9.0)))
    assert ab.t8_scale_mle(r) == pytest.approx(9.0, rel=0.02)
    assert ab.t8_nll(r, ab.t8_scale_mle(r)) <= ab.t8_nll(r, 9.5)


def test_ml_blend_arms() -> None:
    mu, sc, elo = np.array([3.0, -40.0, 50.0]), np.array([10.0, 11.0, 9.0]), np.array([0.6, 0.01, 0.999])
    pm = g.p_home_from_margin(mu, sc, np.full(3, "t8"))
    assert np.allclose(ab.ml_blend(elo, mu, sc, 0.0), np.clip(pm, 1e-4, 1 - 1e-4))
    assert np.allclose(ab.ml_blend(elo, mu, sc, 0.5), np.clip(0.5 * elo + 0.5 * pm, 1e-4, 1 - 1e-4))


def test_a_predict_keeps_v7_mean_and_scale() -> None:
    f = _frame(200)
    p = a_mod.predict(f)
    assert np.array_equal(p["mu_margin"], f["mu_v7"]) and np.array_equal(p["scale_margin"], f["scale_v7"])
    assert np.allclose(p["p_home_ml"], ab.ml_blend(f["elo"], f["mu_v7"], f["scale_v7"], 0.0))


def test_loso_apply_hides_held_out_margin() -> None:
    f = _frame(400)
    seen = []

    def fit(train: pd.DataFrame) -> dict:
        seen.append(set(train["season"]))
        return {}

    def predict(test: pd.DataFrame, _p: dict) -> pd.DataFrame:
        assert "margin" not in test.columns
        return test[["event_id", "season"]]

    out, params = ab.loso_apply(f, fit, predict)
    assert len(out) == len(f) and sorted(params) == [2023, 2024, 2025, 2026]
    for held, s in zip(sorted(params), seen, strict=True):
        assert held not in s and len(s) == 3


def test_loso_fit_ignores_held_out_outcomes() -> None:
    f = _frame(3000)
    f2 = f.copy()
    f2.loc[f2["season"] == 2025, "margin"] += 100.0  # garbage in the held-out season only
    _, p1 = ab.loso_apply(f, b3.fit, b3.predict)
    _, p2 = ab.loso_apply(f2, b3.fit, b3.predict)
    assert p1[2025] == p2[2025]


def test_b1_scale_fn_and_fit_recovers_params() -> None:
    a, b = np.array([13.0, 12.0, 11.0, 9.5]), 0.08
    assert np.allclose(b1.scale_fn(a, 0.0, np.array([5.0, -30.0]), np.array([0, 9])), [13.0, 9.5])
    assert np.allclose(b1.scale_fn(a, b, np.array([-10.0]), np.array([2])), [12.8])
    rng = np.random.default_rng(3)
    n = 60000
    s, mu = rng.integers(0, 20, n), rng.normal(0, 12, n)
    sc = b1.scale_fn(a, b, mu, s)
    train = pd.DataFrame({"mu_v7": mu, "games_played_min": s, "margin": mu + sc * rng.standard_t(8, n)})
    p = b1.fit(train)
    assert p["b"] == pytest.approx(b, abs=0.015)
    assert np.allclose(p["a"], a, atol=0.35)
    assert p["nll"] <= p["nll_b0"]


def test_b1_b3_predict_keep_served_mean_and_blend() -> None:
    f = _frame(300).drop(columns=["margin"])
    for mod, params in ((b1, {"a": [12, 11, 10, 9], "b": 0.05}), (b3, {"c0": 12.0, "c1": 0.5})):
        p = mod.predict(f, params)
        assert np.array_equal(p["mu_margin"], f["mu_v7"])
        assert np.allclose(p["p_home_ml"], ab.ml_blend(f["elo"], f["mu_v7"], p["scale_margin"], 0.5))


def test_b3_floor_shape_and_switch_off() -> None:
    assert np.allclose(b3.floor_fn(10.0, 0.5, np.array([0, 4, 8, 20])), [15.0, 12.5, 10.0, 10.0])
    v7 = np.array([11.27, 10.87, 9.97])
    assert np.array_equal(b3.scale_fn(v7, 0.0, 0.0, np.array([0, 2, 9])), v7)
    # c0 itself floors the 8+ bucket too (per spec): 10.0 > 9.97
    assert np.allclose(b3.scale_fn(v7, 10.0, 0.5, np.array([0, 2, 9])), [15.0, 13.75, 10.0])


def test_b3_fit_recovers_binding_floor() -> None:
    rng = np.random.default_rng(5)
    n = 60000
    s = rng.integers(0, 20, n)
    sc7 = np.array([11.27, 10.87, 10.71, 9.97])[ab.bucket(s)]
    true = b3.scale_fn(sc7, 10.0, 0.6, s)
    train = pd.DataFrame(
        {"mu_v7": 0.0, "scale_v7": sc7, "games_played_min": s, "margin": true * rng.standard_t(8, n)}
    )
    p = b3.fit(train)
    assert p["nll"] <= p["nll_v7_scale"]
    early = b3.floor_fn(p["c0"], p["c1"], np.array([0.0]))[0]
    assert early == pytest.approx(16.0, abs=0.6)


def test_b2_cap_and_uncapped_ols() -> None:
    f = _frame(5000)
    p0 = b2.fit_f1(f, None)
    x = b2.design(f["ridge"], f["v6_mean"])
    assert np.allclose(p0["coef"], ab.ols(x, f["margin"].to_numpy(float)))
    pc = b2.fit_f1(f, 25.0)
    assert np.allclose(pc["coef"], ab.ols(x, np.clip(f["margin"].to_numpy(float), -25, 25)))
    big = f.assign(margin=f["margin"] * 3)  # blowouts beyond the cap change the capped fit less
    assert abs(b2.fit_f1(big, 25.0)["coef"][0] - pc["coef"][0]) < abs(
        b2.fit_f1(big, None)["coef"][0] - p0["coef"][0]
    )
    pred = b2.predict(f.drop(columns=["margin"]), pc)
    assert np.allclose(pred["mu_margin"], x @ np.array(pc["coef"]))  # applied as is, uncapped


def test_write_submission_refuses_rewrite_after_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ab, "OUT_ROOT", tmp_path)
    monkeypatch.setattr(ab, "REPO", tmp_path)
    log = tmp_path / "read_log.jsonl"
    monkeypatch.setattr(g, "READ_LOG", log)
    for p in ("c.py", "d.jsonl"):
        (tmp_path / p).write_text("x", encoding="utf-8")
    monkeypatch.setattr(g, "COMPONENTS", tmp_path / "d.jsonl")
    monkeypatch.setattr(g, "LOSO_V7", tmp_path / "d.jsonl")
    monkeypatch.setattr(g.nr, "SCOREBOARD", tmp_path / "d.jsonl")
    (tmp_path / "outputs/ncaab").mkdir(parents=True)
    (tmp_path / "outputs/ncaab/ncaab_v7_multiseason_frozen.json").write_text("{}", encoding="utf-8")
    pred = a_mod.predict(_frame(20))
    kw = {
        "grid_name": "A_ml_w0",
        "candidate": "t",
        "code_files": [tmp_path / "c.py"],
        "fit_seasons": ab.fit_seasons_loso(),
        "with_ml": True,
        "extra_meta": {},
    }
    path = ab.write_submission(pred, **kw)
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert (
        meta["protocol_sha256"] == g.PROTOCOL_SHA256
        and "2023" not in meta["fit_seasons_by_predicted_season"]["2023"]
    )
    log.write_text(json.dumps({"grid_name": "A_ml_w0"}) + "\n", encoding="utf-8")
    ab.write_submission(pred, **kw)  # identical bytes: allowed
    with pytest.raises(RuntimeError):
        ab.write_submission(pred.assign(mu_margin=pred["mu_margin"] + 1e-3), **kw)
