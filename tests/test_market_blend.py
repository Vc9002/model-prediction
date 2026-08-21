from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.market_blend import (
    MarketBlendBlockedError,
    MarketBlendPolicy,
    SettledBlendEvidence,
    build_policy_artifact,
    canonical_hash,
    fit_oof_market_blend,
    load_stage1_experiment_spec,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "config/research/market_blend_stage1_v1.json"
SPEC_HASH_PATH = ROOT / "config/research/market_blend_stage1_v1.sha256"
MODEL_HASH = "a" * 64
CONFIG_HASH = "b" * 64


def _spec():
    return load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH)


def _evidence(n: int = 100) -> list[SettledBlendEvidence]:
    rows = []
    base = datetime(2026, 4, 1, 19, tzinfo=UTC)
    for index in range(n):
        event_start = base + timedelta(days=index // 4)
        outcome = index % 2
        rows.append(
            SettledBlendEvidence(
                pick_id=f"pick-{index}",
                event_id=f"event-{index}",
                event_start_utc=event_start.isoformat(),
                sport="mlb",
                market="total",
                model_probability=0.60 if outcome else 0.40,
                market_probability=0.80 if outcome else 0.20,
                outcome=outcome,
                model_artifact_hash=MODEL_HASH,
                config_hash=CONFIG_HASH,
                config_byte_sha256="1" * 64,
                config_path="/tmp/config",
                model_artifact_byte_sha256="2" * 64,
                model_artifact_path="/tmp/model",
                quote_observed_at_utc=(event_start - timedelta(hours=1)).isoformat(),
                timestamp_valid=True,
                market_source="polymarket_us",
                market_provenance="decision_time_executable_quote",
                is_reconstructed=False,
                decision_observed_at_utc=(event_start - timedelta(minutes=30)).isoformat(),
                ledger_created_at_utc=(event_start - timedelta(minutes=20)).isoformat(),
                record_source="live_forecast",
                ledger_decision="CALL",
                reason_code="QUALIFIED",
                record_type="QUALIFIED_SHADOW_CALL",
                call_type="model_qualified",
                corrective_action="",
                is_backfill=False,
                model_artifact_bytes_verified=True,
                config_bytes_verified=True,
                model_logical_hash_manifest_verified=True,
                config_logical_hash_manifest_verified=True,
                model_lineage_binding_verified=True,
                config_lineage_binding_verified=True,
                market_snapshot_hash=hashlib.sha256(f"market-snapshot-{index}".encode()).hexdigest(),
                market_snapshot_archive_path="/tmp/odds.jsonl",
                market_snapshot_record_id=f"rec-{index}",
                market_snapshot_archive_verified=True,
            )
        )
    return rows


def test_oof_gate_learns_weight_only_from_prior_dates_and_beats_model() -> None:
    report = fit_oof_market_blend(_evidence(), _spec())

    assert report["status"] == "passed"
    assert report["final_serving_weight"] == 0.0
    assert report["oof_metrics"]["brier_delta"] < 0
    assert report["oof_metrics"]["log_loss_delta"] < 0
    assert report["oof_metrics"]["bootstrap"]["p_better"] == 1.0
    assert all(fold["train_end"] < fold["test_start"] for fold in report["folds"])


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        ({"config_hash": None}, "missing_historical_config_hash_lineage"),
        ({"quote_observed_at_utc": "2026-05-01T20:00:00+00:00"}, "market_quote_not_prestart"),
        ({"timestamp_valid": False}, "invalid_market_quote_timestamp"),
        ({"is_reconstructed": True}, "reconstructed_market_quote"),
        ({"market_provenance": None}, "missing_market_quote_provenance"),
        ({"market_snapshot_hash": None}, "missing_market_snapshot_hash"),
        ({"market_source": None}, "missing_market_quote_source"),
        ({"model_artifact_bytes_verified": False}, "model_artifact_byte_sha256_unverifiable"),
        ({"config_bytes_verified": False}, "config_byte_sha256_unverifiable"),
        ({"ledger_decision": "NO_CALL"}, "unacceptable_ledger_decision:NO_CALL"),
        ({"corrective_action": "repair"}, "corrective_row"),
        ({"is_backfill": True}, "backfill_row"),
    ],
)
def test_integrity_failures_block_before_weight_learning(changes, blocker) -> None:
    rows = [replace(row, **changes) for row in _evidence()]
    report = fit_oof_market_blend(rows, _spec())

    assert report["status"] == "blocked"
    assert blocker in report["blockers"]
    assert "final_serving_weight" not in report
    assert report["descriptive_diagnostic"]["policy_eligible"] is False


@pytest.mark.parametrize(
    ("timestamp", "blocker"),
    [
        ("not-a-time", "malformed_event_start_utc"),
        ("2026-04-01T19:00:00", "naive_event_start_utc"),
        ("9999-12-31T23:59:59.999999-23:59", "non_normalizable_event_start_utc"),
    ],
)
def test_event_chronology_rejects_invalid_timestamps(timestamp, blocker) -> None:
    rows = _evidence()
    rows[0] = replace(rows[0], event_start_utc=timestamp)
    report = fit_oof_market_blend(rows, _spec())
    assert blocker in report["blockers"]


def test_offset_timestamps_are_normalized_and_sorted_by_instant() -> None:
    rows = list(reversed(_evidence()))
    rows[0] = replace(
        rows[0],
        event_start_utc="2026-04-26T01:00:00+06:00",
        quote_observed_at_utc="2026-04-26T00:00:00+06:00",
        decision_observed_at_utc="2026-04-26T00:15:00+06:00",
        ledger_created_at_utc="2026-04-26T00:20:00+06:00",
    )
    report = fit_oof_market_blend(rows, _spec())
    assert report["status"] == "passed"
    assert all(fold["train_end"] < fold["test_start"] for fold in report["folds"])


def test_spec_hash_is_exact_byte_verified_and_spec_is_mandatory(tmp_path: Path) -> None:
    payload = SPEC_PATH.read_bytes()
    bad_hash = tmp_path / "bad.sha256"
    bad_hash.write_text("0" * 64)
    with pytest.raises(MarketBlendBlockedError, match="exact-byte hash mismatch"):
        load_stage1_experiment_spec(SPEC_PATH, bad_hash)
    assert hashlib.sha256(payload).hexdigest() == SPEC_HASH_PATH.read_text().strip()
    with pytest.raises(TypeError):
        fit_oof_market_blend(_evidence())  # type: ignore[call-arg]


def test_model_artifact_column_payload_mismatch_blocks_gate() -> None:
    rows = [replace(row, model_artifact_lineage_verified=False) for row in _evidence()]
    report = fit_oof_market_blend(rows, _spec())
    assert "model_artifact_hash_payload_mismatch" in report["blockers"]


def test_policy_artifact_verifies_hash_lineage_and_audit_identity() -> None:
    report = fit_oof_market_blend(_evidence(), _spec())
    report.update(
        sport="mlb",
        market="total",
        implementation_hash="e" * 64,
        lineage_manifest_hash="f" * 64,
    )
    raw = build_policy_artifact("mlb-total-blend-v1", [report])
    assert raw["entries"][0]["implementation_hash"] == "e" * 64
    assert raw["entries"][0]["lineage_manifest_hash"] == "f" * 64
    policy = MarketBlendPolicy.from_dict(raw)
    audit = policy.apply(
        sport="mlb",
        market="total",
        model_probability=0.60,
        market_probability=0.40,
        model_artifact_hash=MODEL_HASH,
        config_hash=CONFIG_HASH,
    )
    assert audit.blended_probability == pytest.approx(0.40)
    assert audit.experiment_spec_hash == _spec().exact_bytes_sha256
    assert policy.experiment_spec_hash_for("mlb", "total") == audit.experiment_spec_hash
    with pytest.raises(MarketBlendBlockedError, match="config hash does not match"):
        policy.apply(
            sport="mlb",
            market="total",
            model_probability=0.60,
            market_probability=0.40,
            model_artifact_hash=MODEL_HASH,
            config_hash="c" * 64,
        )


def test_tampered_policy_artifact_is_rejected() -> None:
    report = fit_oof_market_blend(_evidence(), _spec())
    report.update(
        sport="mlb",
        market="total",
        implementation_hash="e" * 64,
        lineage_manifest_hash="f" * 64,
    )
    raw = build_policy_artifact("mlb-total-blend-v1", [report])
    raw["entries"][0]["weight"] = 0.5
    assert raw["artifact_hash"] != canonical_hash(raw)
    with pytest.raises(MarketBlendBlockedError, match="artifact hash mismatch"):
        MarketBlendPolicy.from_dict(raw)


def test_uncleared_gate_cannot_emit_policy_artifact() -> None:
    report = fit_oof_market_blend([replace(row, config_hash=None) for row in _evidence()], _spec())
    report.update(sport="mlb", market="total")
    with pytest.raises(MarketBlendBlockedError, match="uncleared gate"):
        build_policy_artifact("blocked", [report])
