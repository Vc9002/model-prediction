"""Pure-function tests for the NCAAB 261120 closing-line grader (synthetic data, no archive reads)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research"))

import grader as g


@pytest.fixture(scope="module")
def proto() -> dict:
    return g.load_protocol()


def test_protocol_hash_is_pinned(proto: dict) -> None:
    assert g.canonical_sha256(proto) == g.PROTOCOL_SHA256
    assert proto["multiplicity"]["K"]["spread_market_relative"] == (
        proto["multiplicity"]["prior_reads"]["spread_market_relative"] + proto["multiplicity"]["grid_size"]
    )
    assert len(proto["candidate_grid"]) == proto["multiplicity"]["grid_size"]


def test_tampered_protocol_is_refused(tmp_path: Path, proto: dict) -> None:
    bad = dict(proto, id="tampered")
    path = tmp_path / "p.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(g.ProtocolMismatchError):
        g.load_protocol(path)


@pytest.mark.parametrize(
    ("season", "day", "locked"),
    [
        (2027, "2026-11-20", True),
        (2026, "2026-07-01", True),
        (2026, "2026-03-15", False),
        (2023, "2022-11-07", False),
    ],
)
def test_lock_by_season_and_date(season: int, day: str, locked: bool) -> None:
    assert g.is_locked(season, day) is locked
    if locked:
        with pytest.raises(g.LockedWindowError):
            g.assert_unlocked(season, day)


def _pred_rows(n: int = 3, season: int = 2025, day: str = "2025-01-10") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": [str(i) for i in range(n)],
            "season": season,
            "game_date": day,
            "home_team": "H",
            "away_team": "A",
            "neutral": False,
            "mu_margin": 3.0,
            "scale_margin": 11.0,
            "dist": "t8",
            "games_played_min": 5,
        }
    )


def test_locked_prediction_row_fails_whole_call() -> None:
    df = _pred_rows()
    df.loc[2, ["season", "game_date"]] = [2027, "2026-11-15"]
    with pytest.raises(g.LockedWindowError):
        g.load_predictions(df)


def test_submission_validation() -> None:
    ok = g.load_predictions(_pred_rows())
    assert ok["dist_total"].eq("t8").all() and ok["p_home_ml"].isna().all()
    with pytest.raises(g.SubmissionError):
        g.load_predictions(_pred_rows().drop(columns=["dist"]))
    with pytest.raises(g.SubmissionError):
        g.load_predictions(_pred_rows().assign(dist="cauchy"))
    with pytest.raises(g.SubmissionError):
        g.load_predictions(_pred_rows().assign(scale_margin=0.0))
    with pytest.raises(g.SubmissionError):
        g.load_predictions(pd.concat([_pred_rows(), _pred_rows()]))
    with pytest.raises(g.SubmissionError):
        g.load_predictions(_pred_rows().assign(mu_total=140.0))  # mu_total without scale_total


def test_meta_validation(proto: dict) -> None:
    meta = {
        "candidate": "x",
        "grid_name": "B1_scale_linear_mu",
        "protocol_sha256": g.PROTOCOL_SHA256,
        "code_sha256": "0",
        "fit_seasons_by_predicted_season": {"2025": [2023, 2024, 2026]},
    }
    info = g.validate_meta(meta, proto, [2025])
    assert info["registered"] and info["family"] == "scale"
    with pytest.raises(g.SubmissionError):
        g.validate_meta({**meta, "fit_seasons_by_predicted_season": {"2025": [2025, 2026]}}, proto, [2025])
    with pytest.raises(g.LockedWindowError):
        g.validate_meta({**meta, "fit_seasons_by_predicted_season": {"2025": [2027]}}, proto, [2025])
    with pytest.raises(g.SubmissionError):
        g.validate_meta({**meta, "protocol_sha256": "old"}, proto, [2025])
    assert not g.validate_meta({**meta, "grid_name": "my_idea"}, proto, [2025])["registered"]


def test_line_sign() -> None:
    assert g.implied_home_margin(-7.5) == 7.5  # home favoured by 7.5 -> market expects home +7.5
    assert g.implied_home_margin(3.0) == -3.0
    assert np.isnan(g.implied_home_margin(None))


def test_cover_probability_half_and_integer_lines() -> None:
    t8 = np.array(["t8", "t8"])
    assert np.allclose(g.p_above_line([7.5, 7.0], [11.0, 11.0], [7.5, 7.0], t8), 0.5)
    # integer line: push mass excluded, so a mean on the line is exactly 50%
    p_int = g.p_above_line([8.0], [11.0], [7.0], np.array(["t8"]))[0]
    p_half = g.p_above_line([8.0], [11.0], [7.5], np.array(["t8"]))[0]
    assert 0.5 < p_half < p_int < 0.6
    p_norm = g.p_above_line([8.0], [11.0], [7.5], np.array(["normal"]))[0]
    assert p_norm != pytest.approx(p_half, abs=1e-6)  # the dist field is honoured


def test_win_probability_from_margin() -> None:
    d = np.array(["t8", "normal", "t8"])
    p = g.p_home_from_margin([0.0, 0.0, 40.0], [11.0, 11.0, 11.0], d)
    assert p[0] < 0.5 and p[1] < 0.5  # P(margin >= 1) at a pick'em is below one half
    assert p[2] > 0.99


def test_elo_reconstruction_inverts_the_served_blend() -> None:
    elo = np.array([0.2, 0.55, 0.9])
    mu, sc = np.array([-4.0, 1.0, 12.0]), np.array([12.0, 11.0, 10.0])
    pm = g.p_home_from_margin(mu, sc, np.array(["t8"] * 3))
    p_home = 0.5 * elo + 0.5 * pm
    assert np.allclose(g.reconstruct_elo_win(p_home, mu, sc), elo)
    assert g.elo_win_from_margin(np.array([0.0]))[0] == pytest.approx(0.5)


def _synthetic(n_days: int = 160, per_day: int = 20, signal: float = 0.0, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_days * per_day
    days = np.repeat([f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}" for d in range(n_days)], per_day)
    x = rng.normal(0, 2, n)
    y = signal * x + rng.normal(0, 11, n)
    seasons = np.repeat([2023, 2024, 2025, 2026], n // 4)
    return pd.DataFrame({"game_date": days, "season": seasons, "x": x, "y": y})


def test_market_plain_and_rule1_detect_a_real_signal() -> None:
    df = _synthetic(signal=1.5)
    mp = g.market_plain(df["x"].to_numpy(), df["y"].to_numpy(), df["game_date"].tolist())
    assert mp["slope_ci95"][0] > 0.8 and mp["corr"] > 0.15
    r1 = g.rule1_loso(df, "x", "y", k=21)
    assert r1["edge_eligible"] and set(r1["folds"]) == {2023, 2024, 2025, 2026}


def test_rule1_refuses_noise() -> None:
    r1 = g.rule1_loso(_synthetic(signal=0.0), "x", "y", k=21)
    assert not r1["edge_eligible"]


def test_paired_corr_delta_favours_the_informative_model() -> None:
    rng = np.random.default_rng(9)
    df = _synthetic(signal=0.0)
    n = len(df)
    line = rng.normal(0, 8, n)
    info = rng.normal(0, 2, n)
    margin = line + 1.5 * info + rng.normal(0, 11, n)
    v7 = line + rng.normal(0, 2, n)
    frame = pd.DataFrame(
        {
            "game_date": df["game_date"],
            "season": df["season"],
            "margin": margin,
            "line": line,
            "cand_mu_margin": line + info,
            "v7_served_mu_margin": v7,
            "cand_p_cover": 0.5,
            "v7_served_p_cover": 0.5,
            "cand_p_ml": 0.6,
            "v7_served_p_ml": 0.6,
        }
    )
    k = {"spread_market_relative": 21, "cover_calibration": 14, "moneyline": 14}
    out = g.paired_vs_v7(frame, k)
    assert out["spread_corr_delta_vs_v7"]["ci_adj"][0] > 0
    assert out["spread_corr_delta_vs_v7"]["late_folds_point"] > 0
    assert out["cover_logloss_delta_vs_v7"]["delta"] == pytest.approx(0.0)
    assert not out["mu_equals_v7"]


def _paired(corr_lo: float = 0.01, rmse: float = -0.1, late: float = 0.01, mu_eq: bool = False) -> dict:
    zero = {"delta": 0.0, "ci95": [-0.01, 0.01], "ci_adj": [-0.02, 0.02], "late_folds_point": 0.0}
    return {
        "spread_corr_delta_vs_v7": {
            "delta": 0.02,
            "ci95": [0.0, 0.04],
            "ci_adj": [corr_lo, 0.05],
            "late_folds_point": late,
        },
        "margin_rmse_delta_vs_v7": {"delta": rmse, "ci95": [-0.2, 0.0]},
        "cover_logloss_delta_vs_v7": dict(zero),
        "moneyline_logloss_delta_vs_v7": dict(zero),
        "moneyline_brier_delta_vs_v7": dict(zero),
        "mu_equals_v7": mu_eq,
    }


def test_verdict_rules() -> None:
    assert g.verdict("mean", True, _paired(), True, [])["status"] == "DEV_ELIGIBLE"
    assert g.verdict("mean", True, _paired(corr_lo=-0.001), True, [])["status"] == "NOT_ELIGIBLE"
    assert g.verdict("mean", True, _paired(rmse=0.01), True, [])["status"] == "NOT_ELIGIBLE"
    assert g.verdict("mean", True, _paired(late=-0.01), True, [])["status"] == "NOT_ELIGIBLE"
    assert g.verdict("mean", True, _paired(), False, [])["status"] == "INCOMPLETE"  # coverage rule
    assert g.verdict("mean", True, _paired(), True, ["rmse_below_line"])["status"] == "LEAK_SUSPECT"
    assert g.verdict("mean", False, _paired(), True, [])["status"] == "UNREGISTERED"
    # a 'scale' candidate that moved the mean is scored as a mean candidate
    assert g.verdict("scale", True, _paired(), True, [])["family_scored"] == "mean"
    assert g.verdict("scale", True, _paired(mu_eq=True), True, [])["status"] == "NOT_ELIGIBLE"


def test_leak_flags() -> None:
    blocks = {
        "cand": {
            "margin_by_season": {2025: {"rmse": 11.0}},
            "market_spread": {"by_season": {2025: {"corr": 0.2}}},
        },
        "closing_line": {"margin_by_season": {2025: {"rmse": 11.2}}},
    }
    flags = g.leak_flags(blocks, "cand")
    assert len(flags) == 2


def test_addendum_is_pinned_and_line_sources_are_gated() -> None:
    add = g.load_addendum()
    assert g.canonical_sha256(add) == g.ADDENDUM_SHA256
    assert 2021 in add["changes"]["confirmation_window"]["history_only_season_years"]
    with pytest.raises(g.SubmissionError):
        g.line_source_info("cbbd")  # no pinned CBBD selection rule yet
    with pytest.raises(ValueError):
        g.line_source_info("some_feed")
    with pytest.raises(g.SubmissionError):
        g.load_archive([2025], line_source="espn_backfill_261120")  # development is ESPN archive only
    with pytest.raises(g.LockedWindowError):
        g.load_archive([2027])
