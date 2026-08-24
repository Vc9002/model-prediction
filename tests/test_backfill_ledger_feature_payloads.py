from __future__ import annotations

import json

from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from scripts.backfill_ledger_feature_payloads import (
    feature_payload_for,
    missing_feature_records,
    mutation_for,
)


def _record(*, payload: dict | None = None) -> dict:
    return {
        "pick_id": "pick-1",
        "ledger_tier": "main",
        "sport": "mlb",
        "created_at_utc": "2026-08-23T12:00:00+00:00",
        "status": "settled",
        "result": "win",
        "pnl_units": 0.91,
        "decision_payload_json": json.dumps(payload or {}),
    }


def test_feature_payload_uses_only_observed_decision_fields() -> None:
    payload = feature_payload_for(
        _record(
            payload={
                "model_version": "mlb-v8",
                "feature_schema_version": "mlb-features-v3",
                "elo_probability": "0.571",
                "unavailable_features": "weather, bullpen",
            }
        )
    )

    assert payload["features"] == {"elo_probability": "0.571"}
    assert payload["observed_feature_count"] == 1
    assert payload["availability_status"] == "partial_with_unavailable_features"
    assert "weather" not in payload["features"]


def test_empty_feature_payload_is_explicitly_unavailable() -> None:
    payload = feature_payload_for(_record(payload={"model_version": "legacy-v1"}))

    assert payload["features"] == {}
    assert payload["observed_feature_count"] == 0
    assert payload["availability_status"] == "unavailable_not_recorded"


def test_mutation_preserves_canonical_lifecycle_and_is_deterministic() -> None:
    record = _record(payload={"elo_away": "1499"})
    first = mutation_for(record)
    second = mutation_for(record)

    assert first == second
    assert first.event_type == "update"
    assert first.status == "settled"
    assert first.result == "win"
    assert first.pnl_units == 0.91
    assert first.note and "not synthesized" in first.note


def test_backfill_applies_once_and_clears_missing_candidate(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    try:
        original = LedgerMutation(
            pick_id="pick-1",
            operation_id="op-append-1",
            ledger_tier="main",
            sport="mlb",
            event_type="append",
            created_at_utc="2026-08-23T12:00:00+00:00",
            status="settled",
            result="win",
            pnl_units=0.91,
            decision_payload={"elo_probability": "0.571"},
        )
        assert store.apply(original)
        candidates = missing_feature_records(store)
        assert len(candidates) == 1

        mutation = mutation_for(candidates[0])
        assert store.apply(mutation)
        assert not store.apply(mutation)
        assert missing_feature_records(store) == []
        record = store.records()[0]
        payload = json.loads(record["feature_payload_json"])
        assert payload["features"] == {"elo_probability": "0.571"}
        assert payload["availability_status"] == "available"
        assert store.verify_integrity() == (True, [])
    finally:
        store.close()
