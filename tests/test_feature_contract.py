import pytest

from model_prediction.feature_contract import (
    FeatureObservation,
    filter_usable,
    validate_observation,
)


def _observation(**overrides) -> FeatureObservation:
    defaults = {
        "event_id": "401816159",
        "entity_id": "mlb-pit",
        "feature_name": "starter_era",
        "value": 3.21,
        "effective_at_utc": "2026-07-18T19:00:00Z",
        "observed_at_utc": "2026-07-18T18:00:00Z",
        "source": "espn",
        "source_version": "v1",
    }
    defaults.update(overrides)
    return FeatureObservation(**defaults)


def test_valid_observation_computes_a_stable_snapshot_hash() -> None:
    obs = _observation()
    assert len(obs.snapshot_hash) == 64
    same = _observation()
    assert same.snapshot_hash == obs.snapshot_hash


def test_snapshot_hash_changes_with_value() -> None:
    a = _observation(value=3.21)
    b = _observation(value=4.00)
    assert a.snapshot_hash != b.snapshot_hash


def test_missing_reason_required_when_unavailable() -> None:
    with pytest.raises(ValueError):
        _observation(available=False, missing_reason=None)
    with pytest.raises(ValueError):
        _observation(available=False, missing_reason="not_a_real_reason")
    # valid
    obs = _observation(available=False, missing_reason="stale", value=None)
    assert obs.missing_reason == "stale"


def test_missing_reason_forbidden_when_available() -> None:
    with pytest.raises(ValueError):
        _observation(available=True, missing_reason="stale")


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        _observation(observed_at_utc="2026-07-18T18:00:00")  # no tz


def test_is_usable_at_enforces_point_in_time_rule() -> None:
    obs = _observation(observed_at_utc="2026-07-18T18:00:00Z")
    assert obs.is_usable_at("2026-07-18T19:00:00Z")  # observed before decision time
    assert obs.is_usable_at("2026-07-18T18:00:00Z")  # exactly at cutoff
    assert not obs.is_usable_at("2026-07-18T17:00:00Z")  # decision before observation


def test_unavailable_observation_never_usable() -> None:
    obs = _observation(available=False, missing_reason="not_published", value=None)
    assert not obs.is_usable_at("2099-01-01T00:00:00Z")


def test_filter_usable_keeps_only_point_in_time_safe_observations() -> None:
    early = _observation(event_id="a", observed_at_utc="2026-07-18T10:00:00Z")
    late = _observation(event_id="b", observed_at_utc="2026-07-18T20:00:00Z")
    kept = filter_usable([early, late], "2026-07-18T15:00:00Z")
    assert kept == [early]


def test_validate_observation_reports_missing_fields() -> None:
    violations = validate_observation({"event_id": "x"})
    assert any("missing required field" in v for v in violations)


def test_validate_observation_accepts_a_full_dict() -> None:
    payload = {
        "event_id": "1", "entity_id": "e", "feature_name": "f", "value": 1.0,
        "effective_at_utc": "2026-07-18T19:00:00Z", "observed_at_utc": "2026-07-18T18:00:00Z",
        "source": "espn", "source_version": "v1", "available": True, "missing_reason": None,
    }
    assert validate_observation(payload) == []


def test_validate_observation_passthrough_for_constructed_instance() -> None:
    assert validate_observation(_observation()) == []
