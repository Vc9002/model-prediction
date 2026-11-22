"""Addendum v4 spread grader and the P1/P2 player candidates (quick 261121-hmc, lane P). Never a real read:
the read log is redirected to a temp file and every outcome here is synthetic."""

from __future__ import annotations

import json
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
import grader_v4
import p_player_261121 as pp


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log = tmp_path / "read_log.jsonl"
    monkeypatch.setattr(grader_v4, "READ_LOG", log)
    return log


# ------------------------------------------------------------------------------------------ pins
def test_addendum_v4_pin_k_and_grid():
    add = grader_v4.load_addendum_v4()
    assert grader.canonical_sha256(add) == grader_v4.ADDENDUM_V4_SHA256
    assert add["multiplicity"]["K"] == {
        "spread_market_relative": 25,
        "total": 26,
        "cover_calibration": 16,
        "moneyline": 16,
    }
    assert set(add["candidate_grid"]) == set(grader_v4.GRID)
    proto = grader_v4.proto_v4()
    assert proto["multiplicity"]["K"]["spread_market_relative"] == 25
    # the v1 protocol itself is untouched: K spread 21
    assert grader.load_protocol()["multiplicity"]["K"]["spread_market_relative"] == 21


def test_tampered_addendum_refused(tmp_path):
    add = json.loads(grader_v4.ADDENDUM_V4_PATH.read_text(encoding="utf-8"))
    add["multiplicity"]["K"]["spread_market_relative"] = 24
    p = tmp_path / "v4.json"
    p.write_text(json.dumps(add), encoding="utf-8")
    with pytest.raises(grader.ProtocolMismatchError):
        grader_v4.load_addendum_v4(p)


def test_pinned_input_mismatch_refused(tmp_path):
    good = tmp_path / "table.jsonl"
    good.write_text('{"a": 1}\n', encoding="utf-8")
    pins = {"table.jsonl": grader.nr.sha256_file(good)}
    assert grader_v4.verify_pinned_inputs_v4(pins, root=tmp_path) == pins
    good.write_text('{"a": 2}\n', encoding="utf-8")  # a rebuilt table
    with pytest.raises(grader.ProtocolMismatchError, match="new addendum"):
        grader_v4.verify_pinned_inputs_v4(pins, root=tmp_path)
    with pytest.raises(grader.ProtocolMismatchError, match="missing"):
        grader_v4.verify_pinned_inputs_v4({"gone.jsonl": "0" * 64}, root=tmp_path)


def test_pinned_input_keys_skip_rule():
    pins = grader_v4.pinned_input_shas(grader_v4.load_addendum_v4())
    assert "rule" not in pins and grader_v4.FEATURE_TABLE_REL in pins and len(pins) == 3


# ------------------------------------------------------------------------------------------ sidecar
def _meta(**kw):
    pins = {grader_v4.FEATURE_TABLE_REL: "a" * 64}
    m = {
        "candidate": "x",
        "grid_name": "P1_player_availability",
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v4_sha256": grader_v4.ADDENDUM_V4_SHA256,
        "code_sha256": "0" * 64,
        "fit_seasons_by_predicted_season": {str(k): v for k, v in pp.fit_seasons_map().items()},
        "data_sources": dict(pins),
    }
    m.update(kw)
    return m, pins


def test_fit_map_never_holds_2022_or_predicted_season():
    fits = pp.fit_seasons_map()
    assert sorted(fits) == [2023, 2024, 2025, 2026]
    for held, f in fits.items():
        assert held not in f and 2022 not in f and len(f) == 3
    meta, pins = _meta()
    info = grader_v4.validate_meta_v4(meta, [2023, 2024, 2025, 2026], pins)
    assert info["registered"] and info["family"] == "mean"


@pytest.mark.parametrize(
    "fits, err",
    [
        ({"2023": [2022, 2024, 2025]}, "sealed"),
        ({"2023": [2023, 2024, 2025]}, "leak"),
        ({"2023": [2021, 2024, 2025]}, "development window"),
    ],
)
def test_meta_refuses_bad_fit_seasons(fits, err):
    meta, pins = _meta(fit_seasons_by_predicted_season=fits)
    with pytest.raises(grader.SubmissionError, match=err):
        grader_v4.validate_meta_v4(meta, [2023], pins)


def test_meta_refuses_wrong_pins():
    meta, pins = _meta(addendum_v4_sha256="f" * 64)
    with pytest.raises(grader.SubmissionError, match="addendum_v4"):
        grader_v4.validate_meta_v4(meta, [2023], pins)
    meta, pins = _meta(data_sources={grader_v4.FEATURE_TABLE_REL: "b" * 64})
    with pytest.raises(grader.SubmissionError, match="feature table"):
        grader_v4.validate_meta_v4(meta, [2023], pins)


def test_confirmation_window_sealed():
    with pytest.raises(grader.LockedWindowError):
        grader_v4.score_v4(pd.DataFrame(), {}, window="confirmation")


# ------------------------------------------------------------------------------------------ read log
def test_reread_guard_fires(tmp_log):
    base = {"grid_name": "P2_returning_production_early", "candidate": "c", "window": "development"}
    assert grader_v4.append_read_log({**base, "predictions_sha256": "a"}) == 1
    assert grader_v4.append_read_log({**base, "predictions_sha256": "a"}) == 1  # same file again
    n = grader_v4.append_read_log({**base, "predictions_sha256": "b"})
    assert n == 2
    v = grader_v4.reread_guard({"status": "DEV_ELIGIBLE"}, True, n, base["grid_name"])
    assert v["status"] == "REREAD_NO_VERDICT" and v["would_have_been"]["status"] == "DEV_ELIGIBLE"
    assert grader_v4.reread_guard({"status": "NOT_ELIGIBLE"}, True, 1, "x")["status"] == "NOT_ELIGIBLE"
    rows = [json.loads(x) for x in tmp_log.read_text(encoding="utf-8").splitlines()]
    assert all(r["addendum_v4_sha256"] == grader_v4.ADDENDUM_V4_SHA256 for r in rows)


# ------------------------------------------------------------------------------------------ candidate math
def _frame(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f = pd.DataFrame(
        {
            "event_id": [str(i) for i in range(n)],
            "season": rng.choice([2023, 2024, 2025, 2026], n),
            "game_date": "2024-01-01",
            "home_team": "H",
            "away_team": "A",
            "neutral": False,
            "games_played_min": rng.integers(0, 15, n),
            "mu_v7": rng.normal(0, 8, n),
            "scale_v7": 10.0,
            "cohort_eligible": True,
        }
    )
    for side in ("home", "away"):
        for c in pp.FEATURE_COLS:
            f[f"{side}_{c}"] = rng.uniform(0, 1, n)
    return f


def test_p1_ols_recovers_coefficients_no_intercept():
    f = _frame()
    x, _ = pp.design(f, "P1_player_availability")
    f["margin"] = f["mu_v7"] + x @ np.array([6.0, -4.0])
    p = pp.fit(f, "P1_player_availability")
    assert np.allclose(p["coef"], [6.0, -4.0], atol=1e-8) and p["n_train"] == len(f)


@pytest.mark.parametrize("grid", sorted(grader_v4.GRID))
def test_null_rows_equal_v7_exactly(grid, monkeypatch):
    monkeypatch.setattr(grader, "served_ml_blend", lambda ids, mu, sc, d: np.full(len(mu), 0.5))
    f = _frame()
    f.loc[f.index[:50], "home_avail_top7_prev1"] = np.nan
    f.loc[f.index[50:100], "away_prior_gmsc40_cur_weighted"] = np.nan
    f.loc[f.index[100:150], "away_top7_minutes_share_out_3plus"] = np.nan
    f.loc[f.index[150:200], "home_ret_gmsc_share_transfer_in"] = np.nan
    out = pp.predict(f, {"coef": [3.0, 2.0]}, grid)
    _, valid = pp.design(f, grid)
    assert (~valid).sum() >= 100
    np.testing.assert_array_equal(out["mu_margin"].to_numpy()[~valid], f["mu_v7"].to_numpy()[~valid])
    assert (out["mu_margin"].to_numpy()[valid] != f["mu_v7"].to_numpy()[valid]).mean() > 0.9
    np.testing.assert_array_equal(out["scale_margin"], f["scale_v7"])


def test_p2_weight_zero_from_ten_games():
    f = _frame()
    f["games_played_min"] = [0, 5, 10, 14] * (len(f) // 4)
    x, valid = pp.design(f, "P2_returning_production_early")
    s = f["games_played_min"].to_numpy()
    assert not valid[s >= 10].any() and valid[s < 10].all()
    d_g = f["home_prior_gmsc40_cur_weighted"] - f["away_prior_gmsc40_cur_weighted"]
    np.testing.assert_allclose(x[s == 5, 1], 0.5 * d_g.to_numpy()[s == 5])


def test_loso_never_fits_on_predicted_or_sealed_season(monkeypatch):
    monkeypatch.setattr(grader, "served_ml_blend", lambda ids, mu, sc, d: np.full(len(mu), 0.5))
    f = _frame()
    f["margin"] = f["mu_v7"]
    seen: dict[int, set[int]] = {}

    def spy(train, grid):
        seen[len(seen)] = set(train["season"])
        return {"coef": [0.0, 0.0]}

    monkeypatch.setattr(pp, "fit", spy)
    _, params = pp.loso(f, "P1_player_availability")
    for i, held in enumerate(pp.fit_seasons_map()):
        assert held not in seen[i] and 2022 not in seen[i]
        assert params[held]["fit_seasons"] == [s for s in (2023, 2024, 2025, 2026) if s != held]
    f22 = f.assign(season=2022)
    with pytest.raises(AssertionError, match="sealed"):
        pp.loso(f22, "P1_player_availability")
