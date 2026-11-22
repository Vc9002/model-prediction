"""Pure-function tests for the protocol-261120 confirmation lane (replay_261120.py, confirm_a_ml_w0.py).

Run: .venv/Scripts/python -m pytest tests/research/test_ncaab_confirmation_261120.py -o addopts='' -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (
    REPO / "src",
    REPO / "scripts",
    REPO / "scripts" / "ncaab_research",
    REPO / "scripts" / "ncaab_research" / "confirmation",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import confirm_a_ml_w0 as ca
import replay_261120 as rp


def test_cbbd_start_utc_matches_scoreboard_format() -> None:
    assert rp.cbbd_start_utc("2014-11-14T16:00:00.000Z") == "2014-11-14T16:00Z"
    assert rp.cbbd_start_utc("2015-03-01T00:30:00.000Z") == "2015-03-01T00:30Z"


def test_scoreboard_window_is_nov1_to_apr10() -> None:
    assert rp.in_scoreboard_window(2015, "2014-11-01")
    assert rp.in_scoreboard_window(2015, "2015-04-10")
    assert not rp.in_scoreboard_window(2015, "2014-10-31")
    assert not rp.in_scoreboard_window(2015, "2015-04-11")


def test_first_by_event_keeps_first_and_sorts() -> None:
    evs = [
        {"event_id": "2", "start_utc": "2015-01-02T00:00Z", "tag": "a"},
        {"event_id": "1", "start_utc": "2015-01-01T00:00Z", "tag": "b"},
        {"event_id": "2", "start_utc": "2015-01-02T00:00Z", "tag": "c"},
    ]
    out = rp.first_by_event(evs)
    assert [e["event_id"] for e in out] == ["1", "2"]
    assert out[1]["tag"] == "a"


def test_unanimous_crosswalk_flags_conflicts_and_ignores_none() -> None:
    xw, conflicts = rp.unanimous_crosswalk([("ACC", "2"), ("ACC", "2"), ("ACC", None), ("Big East", "4")])
    assert xw == {"ACC": "2", "Big East": "4"} and conflicts == {}
    xw, conflicts = rp.unanimous_crosswalk([("Metro", "13"), ("Metro", "14")])
    assert "Metro" not in xw and "Metro" in conflicts


def _ev(eid: str, season: int, home: str, away: str, hc: str | None, ac: str | None) -> dict:
    return {
        "event_id": eid,
        "season_year": season,
        "home_team": home,
        "away_team": away,
        "home_conference_id": hc,
        "away_conference_id": ac,
    }


def test_demote_non_d1_stubs_clears_only_low_count_team_seasons() -> None:
    evs = [_ev(str(i), 2019, "Real", f"Opp{i}", "20", "20") for i in range(5)]
    evs.append(_ev("99", 2019, "Real", "Stub", "20", "20"))  # Stub carries an id in one game only
    out, demoted = rp.demote_non_d1_stubs(evs, max_games=3)
    stub = next(e for e in out if e["event_id"] == "99")
    assert stub["away_conference_id"] is None and stub["home_conference_id"] == "20"
    assert {"season_year": 2019, "team": "Stub", "games": 1} in demoted
    assert all(d["team"] != "Real" for d in demoted)
    # the input is not mutated
    assert evs[-1]["away_conference_id"] == "20"


def test_convert_cbbd_maps_ids_names_conferences_and_fails_closed() -> None:
    games = [
        {
            "id": 1,
            "sourceId": "400585875",
            "season": 2015,
            "seasonType": "regular",
            "startDate": "2014-11-14T16:00:00.000Z",
            "neutralSite": False,
            "conferenceGame": False,
            "gameType": "STD",
            "status": "final",
            "homeTeamId": 80,
            "homeConference": "Big Sky",
            "homePoints": 86,
            "awayTeamId": 296,
            "awayConference": "SWAC",
            "awayPoints": 62,
        },
        {  # unknown conference label -> dropped and counted
            "id": 2,
            "sourceId": "400585876",
            "season": 2015,
            "seasonType": "regular",
            "startDate": "2014-11-15T16:00:00.000Z",
            "neutralSite": True,
            "conferenceGame": False,
            "gameType": "TRNMNT",
            "status": "final",
            "homeTeamId": 80,
            "homeConference": "Nowhere",
            "homePoints": 70,
            "awayTeamId": 296,
            "awayConference": "SWAC",
            "awayPoints": 60,
        },
        {  # not final -> dropped
            "id": 3,
            "sourceId": "400585877",
            "season": 2015,
            "seasonType": "regular",
            "startDate": "2014-11-16T16:00:00.000Z",
            "neutralSite": False,
            "gameType": "STD",
            "status": "cancelled",
            "homeTeamId": 80,
            "homeConference": "Big Sky",
            "homePoints": None,
            "awayTeamId": 296,
            "awayConference": "SWAC",
            "awayPoints": None,
        },
    ]
    teams = [
        {"id": 80, "sourceId": "331", "displayName": "Eastern Washington Eagles"},
        {"id": 296, "sourceId": "2640", "displayName": "Texas Southern Tigers (cbbd)"},
    ]
    box = [
        {
            "gameId": 1,
            "isHome": True,
            "teamStats": {"fieldGoals": {"made": 30, "attempted": 60}, "assists": 15},
        },
    ]
    names = {"331": "Eastern Washington Eagles", "2640": "Texas Southern Tigers"}
    xw = {"Big Sky": "5", "SWAC": "26"}
    out, info = rp.convert_cbbd(games, teams, box, names, xw, seasons=(2015,))
    assert len(out) == 1
    e = out[0]
    assert e["event_id"] == "400585875" and e["season_type"] == "regular-season"
    assert e["home_team"] == "Eastern Washington Eagles" and e["away_team"] == "Texas Southern Tigers"
    assert (e["home_conference_id"], e["away_conference_id"]) == ("5", "26")
    assert e["home_fieldGoalsMade"] == 30.0 and e["home_assists"] == 15.0 and e["away_assists"] is None
    assert e["home_ap_rank"] is None and e["start_utc"] == "2014-11-14T16:00Z"
    assert info["dropped"] == {"unknown_conference:Nowhere": 1, "not_final": 1}


def test_arms_w0_is_pure_margin_and_served_is_half_blend() -> None:
    from model_prediction.models.ncaab_v7_serving import combine_f1

    ridge = np.array([5.0, -3.0, 20.0])
    v6m = np.array([6.0, -2.0, 18.0])
    v6s = np.array([10.0, 10.5, 9.8])
    v6p = np.array([0.70, 0.42, 0.93])
    sample = np.array([0, 4, 12])
    arms = ca.arm_probabilities(ridge, v6m, v6s, v6p, sample)
    mu, sc = combine_f1(20.0, 18.0, 12)
    assert arms["mu"][2] == pytest.approx(mu) and arms["scale"][2] == pytest.approx(sc)
    from scipy import stats

    pm = 1.0 - stats.t.cdf((0.5 - arms["mu"]) / arms["scale"], df=8)
    assert np.allclose(arms["p_cand"], np.clip(pm, 1e-4, 1 - 1e-4))
    assert np.allclose(arms["p_v7"], np.clip(0.5 * arms["elo"] + 0.5 * pm, 1e-4, 1 - 1e-4))
    # Elo half reconstructs v6's blend exactly: 0.5 * elo + 0.5 * P(v6 margin) = v6_p_home
    pm6 = 1.0 - stats.t.cdf((0.5 - v6m) / v6s, df=8)
    assert np.allclose(0.5 * arms["elo"] + 0.5 * pm6, v6p)


def test_one_sided_upper_is_95th_percentile() -> None:
    s = np.arange(4001, dtype=float)  # 0..4000
    assert ca.one_sided_upper(s) == pytest.approx(3800.0)


def test_verdict_rule() -> None:
    assert ca.verdict({"upper_one_sided_95": -1e-4}, True) == "PASS"
    assert ca.verdict({"upper_one_sided_95": 0.0}, True) == "FAIL"
    assert ca.verdict({"upper_one_sided_95": 2e-3}, True) == "FAIL"
    assert ca.verdict({"upper_one_sided_95": -1.0}, False) == "INCOMPLETE"


def test_coverage_by_season_requires_99_percent_everywhere() -> None:
    cov, ok = ca.coverage_by_season({2015: 990, 2016: 1000}, {2015: 1000, 2016: 1000}, (2015, 2016))
    assert ok and cov == {2015: 0.99, 2016: 1.0}
    cov, ok = ca.coverage_by_season({2015: 989}, {2015: 1000, 2016: 1000}, (2015, 2016))
    assert not ok and cov[2016] == 0.0


def test_confirmation_already_read_only_matches_confirmation_window() -> None:
    dev = json.dumps({"grid_name": "A_ml_w0", "window": "development"})
    conf = json.dumps({"grid_name": "A_ml_w0", "window": "confirmation"})
    other = json.dumps({"grid_name": "B1_scale_linear_mu", "window": "confirmation"})
    assert not ca.confirmation_already_read([dev, other, ""])
    assert ca.confirmation_already_read([dev, conf])


def test_logloss_delta_sign_and_bounds() -> None:
    rng = np.random.default_rng(0)
    n = 400
    win = (rng.random(n) < 0.6).astype(float)
    good = np.where(win == 1, 0.8, 0.2)
    flat = np.full(n, 0.6)
    days = [f"2016-01-{1 + i % 28:02d}" for i in range(n)]
    out = ca.logloss_delta(good, flat, win, days)
    assert out["delta"] < 0 and out["upper_one_sided_95"] < 0
    assert out["ci95_two_sided"][0] <= out["delta"] <= out["upper_one_sided_95"] <= out["ci95_two_sided"][1]


def test_d1_denominators_counts_both_sides_with_conference_only() -> None:
    evs = [
        _ev("1", 2015, "A", "B", "2", "4"),
        _ev("2", 2015, "A", "C", "2", None),
        _ev("3", 2016, "A", "B", "2", "4"),
        _ev("4", 2021, "A", "B", "2", "4"),
    ]
    assert ca.d1_denominators(evs, (2015, 2016)) == {2015: 1, 2016: 1}
