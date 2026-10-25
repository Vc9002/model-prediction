"""International v3 serving identities retain strict, pregame replay validation."""

import pytest

from model_prediction.international_baseball import HomeElo
from model_prediction.international_replay import (
    build_snapshot,
    capture_inputs,
    replay_inputs,
    validate_decision_snapshot,
)


def _decision(league, version, observed="2026-10-25T08:00:00Z"):
    inputs = capture_inputs(HomeElo(20, 20, {"a": 1500.0, "h": 1620.0}), "a", "h", "flat", 0.05)
    values = replay_inputs(inputs)["settlement_values"]
    contract = {
        "league": league,
        "model_version": version,
        "artifact_hash": "artifact",
        "event_id": "178257",
        "event_start_utc": "2026-10-25T09:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "inference_inputs": inputs,
        "sides": [
            {"team": "Home", "model_fair_settlement_value": values["home"]},
            {"team": "Away", "model_fair_settlement_value": values["away"]},
        ],
    }
    row = contract | {
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "model_artifact_hash": "artifact",
        "model_probability": values["home"],
    }
    return build_snapshot(contract, observed), row


@pytest.mark.parametrize("league", ["NPB", "KBO"])
@pytest.mark.parametrize("suffix", ["baseball-v3", "tie-aware-elo-v2"])
def test_supported_international_model_identity_replays(league, suffix):
    snapshot, row = _decision(league, f"{league.lower()}-{suffix}")
    assert validate_decision_snapshot(snapshot, row) == row["model_probability"]


@pytest.mark.parametrize("version", ["kbo-baseball-v3", "npb-baseball-v4", "npb-spread-tie-aware-elo-v2"])
def test_unknown_or_wrong_league_model_identity_is_rejected(version):
    snapshot, row = _decision("NPB", version)
    with pytest.raises(ValueError, match="decision_identity_mismatch"):
        validate_decision_snapshot(snapshot, row)


@pytest.mark.parametrize(
    "mutation",
    [
        {"model_version": "npb-tie-aware-elo-v2"},
        {"model_artifact_hash": "other"},
        {"event_id": "other"},
        {"home_team": "Away"},
        {"event_start_utc": "2026-10-25T10:00:00Z"},
    ],
)
def test_v3_identity_checks_remain_strict(mutation):
    snapshot, row = _decision("NPB", "npb-baseball-v3")
    with pytest.raises(ValueError, match="decision_identity_mismatch"):
        validate_decision_snapshot(snapshot, row | mutation)


@pytest.mark.parametrize("observed", ["2026-10-25T09:00:00Z", "2026-10-25T09:00:01Z"])
def test_v3_cannot_fabricate_started_event_snapshot(observed):
    snapshot, row = _decision("NPB", "npb-baseball-v3", observed)
    with pytest.raises(ValueError, match="decision_identity_mismatch"):
        validate_decision_snapshot(snapshot, row)


def test_v3_rejects_changed_probability():
    snapshot, row = _decision("NPB", "npb-baseball-v3")
    with pytest.raises(ValueError, match="probability_mismatch"):
        validate_decision_snapshot(snapshot, row | {"model_probability": 0.51})
