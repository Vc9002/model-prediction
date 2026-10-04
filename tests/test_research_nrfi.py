import copy
import json
from datetime import timedelta

import pytest

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import compute_first_inning_priors
from model_prediction.research_nrfi import (
    FirstInningState,
    build_rows,
    historical_fixture,
    live_features,
    load_snapshots,
    validate_snapshot,
)
from tests.test_mlb_first_inning_live import _snapshot


def snapshot(game=1, start="2026-04-01T17:05:00Z"):
    return _snapshot(game, start, "Home", "Away", 101, "Away Starter", 201, "Home Starter", 0, 1) | {
        "status": "Final",
        "yrfi": 1,
        "observed_at_utc": (parse_utc(start) + timedelta(hours=8)).isoformat(),
    }


@pytest.fixture
def priors(tmp_path):
    return compute_first_inning_priors(tmp_path / "absent.jsonl")


@pytest.mark.parametrize(
    "change",
    [
        "scheduled",
        "canceled",
        "missing_runs",
        "bad_label",
        "fractional",
        "cross_team",
        "missing_starter",
        "unfinished_inning",
    ],
)
def test_invalid_snapshot_is_not_a_zero_run_observation(tmp_path, change):
    raw = snapshot()
    if change == "scheduled":
        raw["status"] = "Scheduled"
    elif change == "canceled":
        raw["status"] = "Cancelled: Rain"
    elif change == "missing_runs":
        raw["first_inning_runs_home"] = None
    elif change == "bad_label":
        raw["yrfi"] = 0
    elif change == "fractional":
        raw["first_inning_runs_home"] = 0.5
    elif change == "cross_team":
        raw["away"]["players"].append(raw["home"]["players"][1])
    elif change == "missing_starter":
        raw["home"]["pitcher_order"] = []
    elif change == "unfinished_inning":
        raw["first_inning_complete"] = False
    with pytest.raises((ValueError, KeyError)):
        validate_snapshot(raw)
    path = tmp_path / "raw.jsonl"
    path.write_text(json.dumps(raw) + "\n")
    found, audit = load_snapshots(path)
    assert not found and len(audit["excluded"]) == 1


def test_opponent_batters_never_enter_own_pool(priors):
    raw = snapshot()
    first = FirstInningState(priors)
    first.observe(raw)
    changed = copy.deepcopy(raw)
    for p in changed["home"]["players"]:
        if p.get("batting"):
            p["batting"]["hits"] = 4
            p["batting"]["totalBases"] = 16
    second = FirstInningState(priors)
    second.observe(changed)
    future = historical_fixture(raw) | {"event_start_utc": "2026-04-05T17:05:00Z"}
    a, b = first.features(future), second.features(future)
    assert a["away_recent_batter_composite"] == b["away_recent_batter_composite"]
    assert a["home_recent_batter_composite"] != b["home_recent_batter_composite"]
    assert set(first.pools["Away"][0][1]) == {9001, 9002}
    assert set(first.pools["Home"][0][1]) == {9003, 9004}


def test_training_and_live_share_embargo_and_scheduled_start_rest(tmp_path, priors):
    old = snapshot()
    recent = snapshot(2, "2026-04-04T17:05:00Z")
    target = snapshot(3, "2026-04-05T17:05:00Z")
    data = [old, recent, target]
    path = tmp_path / "raw.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in data))
    trained = build_rows(data, priors)[-1]
    live, latest = live_features(path, historical_fixture(target), parse_utc("2026-04-05T08:00:00Z"), priors)
    assert tuple(live.values()) == trained.features
    assert latest == trained.feature_max_event_date == "2026-04-01"
    assert live["away_starter_days_rest"] == 4  # Scheduled start, not midnight cutoff.
    changed = copy.deepcopy(data)
    changed[1]["first_inning_runs_home"] = 100
    assert build_rows(changed, priors)[-1].features == trained.features


def test_home_away_defense_roles_and_pitcher_priors_are_correct(priors):
    state = FirstInningState(priors)
    raw = snapshot()
    state.observe(raw)
    f = state.features(historical_fixture(raw) | {"event_start_utc": "2026-04-05T17:05:00Z"})
    assert f["away_team_1st_allowed_away"] == round((1 + 20 * priors["half_home"]) / 21, 4)
    assert f["home_team_1st_allowed_home"] == round(20 * priors["half_away"] / 21, 4)
    assert f["away_starter_opp_1st_runs"] == round((1 + 15 * priors["half_home"]) / 16, 4)


def test_conflicting_records_and_unavailable_future_observations_fail_closed(tmp_path):
    raw = snapshot()
    changed = copy.deepcopy(raw)
    changed["first_inning_runs_home"] = 2
    path = tmp_path / "raw.jsonl"
    path.write_text(json.dumps(raw) + "\n" + json.dumps(changed) + "\n")
    found, audit = load_snapshots(path)
    assert not found and audit["exclusion_counts"] == {"conflicting_event_versions": 1}
    path.write_text(json.dumps(raw) + "\n")
    assert not load_snapshots(path, observed_before=parse_utc("2026-04-01T18:00:00Z"))[0]


def test_ambiguous_name_does_not_merge_two_pitchers(priors):
    state = FirstInningState(priors)
    raw = snapshot()
    state.observe(raw)
    changed = copy.deepcopy(raw)
    changed["home"]["pitcher_order"] = [202]
    changed["home"]["players"][0]["player_id"] = 202
    state.observe(changed)
    with pytest.raises(ValueError, match="ambiguous_starter"):
        state.features(historical_fixture(raw))
