from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.experiment_registry import list_experiments, show, void
from model_prediction.market_blend import (
    MarketBlendBlockedError,
    MarketBlendPolicy,
    canonical_config_logical_hash,
    load_stage1_experiment_spec,
)
from model_prediction.models.mlb import canonical_mlb_artifact_hash
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from scripts.market_blend_stage1 import (
    _implementation_manifest,
    _lineage_status,
    _load_lineage_manifest,
    _write_immutable_json,
    run_gate,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "config/research/market_blend_stage1_v1.json"
SPEC_HASH_PATH = ROOT / "config/research/market_blend_stage1_v1.sha256"


def _make_db(
    path: Path,
    *,
    include_invalid_eligible: bool = True,
    model_file: Path | None = None,
    config_file: Path | None = None,
) -> None:
    paths = RuntimePaths(repo_root=ROOT, runtime_root=path.parents[1])
    model_hash = "a" * 64
    if model_file is not None:
        model_raw = json.loads(model_file.read_text())
        model_hash = canonical_mlb_artifact_hash(model_raw)
        assert model_raw["artifact_hash"] == model_hash
    config_hash = (
        canonical_config_logical_hash(config_file.read_bytes()) if config_file is not None else "b" * 64
    )
    n_rows = 65 if include_invalid_eligible else 60
    base = datetime(2026, 5, 1, 19, tzinfo=UTC)
    with RuntimeLedgerStore(paths) as store:
        for index in range(n_rows):
            event_start = base + timedelta(days=index // 2)
            variant = index - 60
            market_snapshot_hash = hashlib.sha256(f"market-snapshot-{index}".encode()).hexdigest()
            payload: dict | str = {
                "model_artifact_hash": model_hash,
                "config_hash": config_hash,
                "market_quote_observed_at_utc": (event_start - timedelta(hours=1)).isoformat(),
                "market_quote_timestamp_valid": True,
                "market_quote_source": "polymarket_us",
                "market_quote_provenance": "decision_time_executable_quote",
                "market_quote_reconstructed": False,
                "market_snapshot_hash": market_snapshot_hash,
                "observed_at_utc": (event_start - timedelta(minutes=30)).isoformat(),
                "record_source": "live_forecast",
                "record_type": "QUALIFIED_SHADOW_CALL",
                "call_type": "research_observation" if variant == 3 else "model_qualified",
                "corrective_action": "repair" if variant == 1 else "",
                "is_backfill": variant == 2,
            }
            if model_file is not None and config_file is not None:
                payload.update(
                    {
                        "model_artifact_byte_sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
                        "model_artifact_path": str(model_file),
                        "config_byte_sha256": hashlib.sha256(config_file.read_bytes()).hexdigest(),
                        "config_path": str(config_file),
                    }
                )
            if variant == 0:
                payload = "not-a-dict"  # type: ignore[assignment]
            created = event_start - timedelta(minutes=20)
            store.apply(
                LedgerMutation(
                    pick_id=f"pick-{index}",
                    operation_id=f"op-{index}",
                    ledger_tier="flat",
                    sport="mlb",
                    event_type="append",
                    created_at_utc=created.isoformat(),
                    event_id=f"event-{index}",
                    canonical_event_id=f"event-{index}",
                    event_start_utc=event_start.isoformat(),
                    market_type="total",
                    selection="over",
                    model_id="measured-edge-totals-v3",
                    model_artifact_hash=model_hash,
                    model_probability=(None if variant == 4 else 0.6 if index % 2 else 0.4),
                    market_snapshot_hash=market_snapshot_hash,
                    market_probability=0.8 if index % 2 else 0.2,
                    decision="CALL",
                    reason_code="QUALIFIED",
                    status="settled",
                    result="win" if index % 2 else "loss",
                    settled_at_utc=(event_start + timedelta(hours=4)).isoformat(),
                    decision_payload=payload,  # type: ignore[arg-type]
                )
            )


def _runtime(
    tmp_path: Path,
    *,
    include_invalid_eligible: bool = True,
    model_file: Path | None = None,
    config_file: Path | None = None,
) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    db_path = runtime_root / "ledgers" / "ledgers.db"
    _make_db(
        db_path,
        include_invalid_eligible=include_invalid_eligible,
        model_file=model_file,
        config_file=config_file,
    )
    return runtime_root, db_path


def _passing_inputs(tmp_path: Path) -> tuple[Path, Path, dict[tuple[str, str], dict[str, str]]]:
    """Build valid, producer-bound lineage for a policy-emitting gate test."""
    model_file = ROOT / "config/models/measured-edge-totals-v3.json"
    config_file = tmp_path / "config.yaml"
    config_file.write_bytes(b"config bytes")
    config_logical_hash = canonical_config_logical_hash(config_file.read_bytes())
    model_logical_hash = json.loads(model_file.read_text())["artifact_hash"]
    runtime_root, db_path = _runtime(
        tmp_path,
        include_invalid_eligible=False,
        model_file=model_file,
        config_file=config_file,
    )
    entries = {
        ("model", model_logical_hash): {
            "path": str(model_file),
            "byte_sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
        },
        ("config", config_logical_hash): {
            "path": str(config_file),
            "byte_sha256": hashlib.sha256(config_file.read_bytes()).hexdigest(),
        },
    }
    return runtime_root, db_path, entries


def _run(tmp_path: Path, *, record_experiment: bool = False):
    runtime_root, db_path = _runtime(tmp_path)
    return run_gate(
        db_path=db_path,
        sport="mlb",
        market="total",
        tier="flat",
        spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
        spec_path=SPEC_PATH,
        lineage_entries={},
        lineage_manifest_hash=None,
        runtime_root=runtime_root,
        report_dir=tmp_path / "reports",
        policy_dir=(tmp_path / "policies" if record_experiment else None),
        record_experiment=record_experiment,
    )


def _late_authentic_mutation(index: int = 999) -> LedgerMutation:
    event_start = datetime(2026, 8, 1, 19, tzinfo=UTC)
    market_snapshot_hash = hashlib.sha256(f"market-snapshot-{index}".encode()).hexdigest()
    return LedgerMutation(
        pick_id=f"pick-{index}",
        operation_id=f"op-{index}",
        ledger_tier="flat",
        sport="mlb",
        event_type="append",
        created_at_utc=(event_start - timedelta(minutes=20)).isoformat(),
        event_id=f"event-{index}",
        canonical_event_id=f"event-{index}",
        event_start_utc=event_start.isoformat(),
        market_type="total",
        selection="over",
        model_id="measured-edge-totals-v3",
        model_artifact_hash="a" * 64,
        market_snapshot_hash=market_snapshot_hash,
        model_probability=0.60,
        market_probability=0.80,
        decision="CALL",
        reason_code="QUALIFIED",
        status="settled",
        result="win",
        settled_at_utc=(event_start + timedelta(hours=4)).isoformat(),
        decision_payload={
            "model_artifact_hash": "a" * 64,
            "config_hash": "b" * 64,
            "market_quote_observed_at_utc": (event_start - timedelta(hours=1)).isoformat(),
            "market_quote_timestamp_valid": True,
            "market_quote_source": "polymarket_us",
            "market_quote_provenance": "decision_time_executable_quote",
            "market_quote_reconstructed": False,
            "market_snapshot_hash": market_snapshot_hash,
            "observed_at_utc": (event_start - timedelta(minutes=30)).isoformat(),
            "record_source": "live_forecast",
            "record_type": "QUALIFIED_SHADOW_CALL",
            "call_type": "model_qualified",
            "corrective_action": "",
            "is_backfill": False,
        },
    )


def test_runner_keeps_invalid_eligible_rows_and_blocks_them(tmp_path: Path) -> None:
    report, experiment, report_path = _run(tmp_path)
    assert report["status"] == "blocked"
    assert report["n_rows"] == 65
    assert report["descriptive_diagnostic"]["n_input_calls"] == 60
    assert "invalid_decision_payload_json" in report["blockers"]
    assert "corrective_row" in report["blockers"]
    assert "backfill_row" in report["blockers"]
    assert "invalid_call_type:research_observation" in report["blockers"]
    assert "missing_model_probability" in report["blockers"]
    assert "model_logical_hash_absent_from_lineage_manifest" in report["blockers"]
    assert "config_logical_hash_absent_from_lineage_manifest" in report["blockers"]
    assert experiment is None and report_path.is_file()
    assert report["candidate_policy_path"] is None
    assert report["runtime_ledger_integrity"]["status"] == "verified"


def test_projection_tampering_blocks_before_evidence_selection(tmp_path: Path) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE ledger_records SET model_probability=0.99 WHERE pick_id='pick-0'")
    conn.commit()
    conn.close()

    report, _experiment, _path = run_gate(
        db_path=db_path,
        sport="mlb",
        market="total",
        tier="flat",
        spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
        spec_path=SPEC_PATH,
        lineage_entries={},
        lineage_manifest_hash=None,
        runtime_root=runtime_root,
        report_dir=tmp_path / "tampered-reports",
        record_experiment=False,
    )
    assert report["verdict"] == "blocked_ledger_integrity"
    assert report["n_rows"] == 0
    assert any("projection_mismatch" in item for item in report["blockers"])


def test_missing_event_blocks_before_evidence_selection(tmp_path: Path) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM ledger_events WHERE sequence=(SELECT MAX(sequence) FROM ledger_events)")
    conn.commit()
    conn.close()

    report, _experiment, _path = run_gate(
        db_path=db_path,
        sport="mlb",
        market="total",
        tier="flat",
        spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
        spec_path=SPEC_PATH,
        lineage_entries={},
        lineage_manifest_hash=None,
        runtime_root=runtime_root,
        report_dir=tmp_path / "missing-event-reports",
        record_experiment=False,
    )
    assert report["verdict"] == "blocked_ledger_integrity"
    assert report["n_rows"] == 0
    assert any("projection_rows_without_events" in item for item in report["blockers"])
    diagnostic = report["descriptive_diagnostic"]
    assert diagnostic["label"] == ("RESEARCH_ONLY_UNVERIFIED_LEDGER_DESCRIPTIVE_NOT_GATE_EVIDENCE")
    assert diagnostic["n_candidate_rows"] == 65
    assert diagnostic["n_input_calls"] == 61
    assert diagnostic["n_numeric_calls"] == 60
    assert diagnostic["weight_learning_performed"] is False
    assert diagnostic["policy_eligible"] is False


def test_gate_reads_committed_rows_still_open_in_wal(tmp_path: Path) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    paths = RuntimePaths(repo_root=ROOT, runtime_root=runtime_root)
    store = RuntimeLedgerStore(paths)
    try:
        store._conn.execute("PRAGMA wal_autocheckpoint=0")
        assert store.apply(_late_authentic_mutation()) is True
        assert db_path.with_name("ledgers.db-wal").is_file()
        report, _experiment, _path = run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "wal-reports",
            record_experiment=False,
        )
        assert report["runtime_ledger_integrity"]["status"] == "verified"
        assert report["n_rows"] == 66
    finally:
        store.close()


def test_verification_and_fetch_share_one_snapshot(tmp_path: Path, monkeypatch) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    paths = RuntimePaths(repo_root=ROOT, runtime_root=runtime_root)
    writer = RuntimeLedgerStore(paths)
    import scripts.market_blend_stage1 as runner

    original_fetch = runner._fetch_settled
    mutation_applied = False

    def mutate_then_fetch(conn, **kwargs):
        nonlocal mutation_applied
        mutation_applied = writer.apply(_late_authentic_mutation())
        return original_fetch(conn, **kwargs)

    monkeypatch.setattr(runner, "_fetch_settled", mutate_then_fetch)
    try:
        report, _experiment, _path = runner.run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "snapshot-reports",
            record_experiment=False,
        )
        assert mutation_applied is True
        assert report["runtime_ledger_integrity"]["record_count"] == 65
        assert report["n_rows"] == 65
        assert len(writer.records()) == 66
    finally:
        writer.close()


def test_unsupported_pair_fails_before_ledger_access(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-with-no-ledger"
    with pytest.raises(MarketBlendBlockedError, match="unsupported Stage 1 sport/market pair"):
        run_gate(
            db_path=runtime_root / "ledgers/ledgers.db",
            sport="wnba",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            record_experiment=False,
        )


def test_lineage_manifest_separates_logical_and_byte_hashes(tmp_path: Path) -> None:
    artifact = tmp_path / "measured-edge-totals-v3.json"
    artifact.write_bytes((ROOT / "config/models/measured-edge-totals-v3.json").read_bytes())
    artifact_raw = json.loads(artifact.read_text())
    logical_hash = canonical_mlb_artifact_hash(artifact_raw)
    assert artifact_raw["artifact_hash"] == logical_hash
    manifest_path = tmp_path / "lineage.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "market_blend_lineage_manifest_v2",
                "artifacts": [
                    {
                        "kind": "model",
                        "logical_hash": logical_hash,
                        "byte_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "path": str(artifact),
                    }
                ],
            }
        )
    )
    sidecar = tmp_path / "lineage.sha256"
    sidecar.write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest())
    entries, manifest_hash = _load_lineage_manifest(
        manifest_path, sidecar, expected_schema="market_blend_lineage_manifest_v2"
    )
    assert logical_hash != hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert _lineage_status(
        entries,
        "model",
        logical_hash,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        str(artifact),
    ) == (True, True, True)
    assert len(manifest_hash or "") == 64
    artifact.write_bytes(b"tampered")
    assert _lineage_status(
        entries,
        "model",
        logical_hash,
        entries[("model", logical_hash)]["byte_sha256"],
        str(artifact),
    ) == (True, False, False)


def test_manifest_cannot_substitute_arbitrary_logical_hash(tmp_path: Path) -> None:
    artifact = tmp_path / "model.json"
    artifact.write_text(json.dumps({"artifact_hash": "real-logical-hash"}))
    byte_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entries = {
        ("model", "substituted-logical-hash"): {
            "path": str(artifact),
            "byte_sha256": byte_hash,
        }
    }
    assert _lineage_status(entries, "model", "substituted-logical-hash", byte_hash, str(artifact)) == (
        True,
        False,
        True,
    )


def test_gate_reports_are_immutable_without_creating_registry_orphan(tmp_path: Path) -> None:
    _report, _experiment, report_path = _run(tmp_path)
    runtime_root = tmp_path / "runtime"
    before = list_experiments(repo_root=ROOT, runtime_root=runtime_root)
    with pytest.raises(FileExistsError, match="immutable artifact already exists"):
        run_gate(
            db_path=runtime_root / "ledgers/ledgers.db",
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            policy_dir=tmp_path / "policies",
            record_experiment=True,
        )
    assert list_experiments(repo_root=ROOT, runtime_root=runtime_root) == before
    assert report_path.is_file()


def test_report_write_failure_voids_registry_entry(tmp_path: Path, monkeypatch) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    import scripts.market_blend_stage1 as runner

    def fail_write(_path, _payload):
        raise OSError("injected report write failure")

    monkeypatch.setattr(runner, "_write_immutable_json", fail_write)
    with pytest.raises(OSError, match="injected"):
        runner.run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            record_experiment=True,
        )
    rows = list_experiments(repo_root=ROOT, runtime_root=runtime_root, model_id="market-blend-mlb-total")
    assert rows[0]["status"] == "void"
    assert "output finalization failed" in rows[0]["void_reason"]


def test_registry_failure_writes_no_report_or_policy(tmp_path: Path, monkeypatch) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    import scripts.market_blend_stage1 as runner

    def fail_record(**_kwargs):
        raise sqlite3.OperationalError("injected registry failure")

    monkeypatch.setattr(runner, "record", fail_record)
    with pytest.raises(sqlite3.OperationalError, match="injected registry failure"):
        runner.run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            policy_dir=tmp_path / "policies",
            record_experiment=True,
        )
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "policies").exists()


def test_policy_orphan_is_tracked_by_void_registry_if_report_finalize_fails(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_root, db_path, entries = _passing_inputs(tmp_path)
    import scripts.market_blend_stage1 as runner

    original_write = runner._write_immutable_json
    writes = 0

    def fail_second_write(path, payload):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected report finalization failure")
        original_write(path, payload)

    monkeypatch.setattr(runner, "_write_immutable_json", fail_second_write)
    with pytest.raises(OSError, match="report finalization"):
        runner.run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries=entries,
            lineage_manifest_hash="f" * 64,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            policy_dir=tmp_path / "policies",
            record_experiment=True,
        )
    policies = list((tmp_path / "policies").glob("*.json"))
    assert len(policies) == 1
    rows = list_experiments(repo_root=ROOT, runtime_root=runtime_root, model_id="market-blend-mlb-total")
    assert rows[0]["status"] == "void"
    policy_hash = json.loads(policies[0].read_text())["artifact_hash"]
    assert rows[0]["artifact_hashes"]["candidate_policy"] == policy_hash
    with pytest.raises(MarketBlendBlockedError, match="report are both required"):
        MarketBlendPolicy.load(
            policies[0],
            runtime_paths=RuntimePaths(repo_root=ROOT, runtime_root=runtime_root),
            report_path=tmp_path / "reports/missing.json",
        )


def test_policy_load_requires_completed_registry_and_exact_immutable_report(tmp_path: Path) -> None:
    runtime_root, db_path, entries = _passing_inputs(tmp_path)
    report, experiment, report_path = run_gate(
        db_path=db_path,
        sport="mlb",
        market="total",
        tier="flat",
        spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
        spec_path=SPEC_PATH,
        lineage_entries=entries,
        lineage_manifest_hash="f" * 64,
        runtime_root=runtime_root,
        report_dir=tmp_path / "reports",
        policy_dir=tmp_path / "policies",
        record_experiment=True,
    )
    assert report["status"] == "passed"
    assert experiment is not None and experiment["status"] == "completed"
    policy_path = Path(report["candidate_policy_path"])
    loaded = MarketBlendPolicy.load(
        policy_path,
        runtime_paths=RuntimePaths(repo_root=ROOT, runtime_root=runtime_root),
        report_path=report_path,
    )
    assert loaded.artifact_hash == report["candidate_policy_artifact_hash"]

    void(
        report["experiment_id"],
        "injected post-publication invalidation",
        repo_root=ROOT,
        runtime_root=runtime_root,
    )
    with pytest.raises(MarketBlendBlockedError, match="has not completed"):
        MarketBlendPolicy.load(
            policy_path,
            runtime_paths=RuntimePaths(repo_root=ROOT, runtime_root=runtime_root),
            report_path=report_path,
        )


def test_forged_registry_outside_canonical_runtime_cannot_authorize_policy(
    tmp_path: Path,
) -> None:
    runtime_root, db_path, entries = _passing_inputs(tmp_path)
    report, _experiment, report_path = run_gate(
        db_path=db_path,
        sport="mlb",
        market="total",
        tier="flat",
        spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
        spec_path=SPEC_PATH,
        lineage_entries=entries,
        lineage_manifest_hash="f" * 64,
        runtime_root=runtime_root,
        report_dir=tmp_path / "reports",
        policy_dir=tmp_path / "policies",
        record_experiment=True,
    )
    policy_path = Path(report["candidate_policy_path"])
    forged_registry = tmp_path / "forged-runs.db"
    (runtime_root / "runs.db").replace(forged_registry)
    assert forged_registry.is_file()
    with pytest.raises(MarketBlendBlockedError, match="canonical experiment registry is missing"):
        MarketBlendPolicy.load(
            policy_path,
            runtime_paths=RuntimePaths(repo_root=ROOT, runtime_root=runtime_root),
            report_path=report_path,
        )


def test_registry_is_written_only_to_supplied_canonical_runtime(tmp_path: Path) -> None:
    report, experiment, _path = _run(tmp_path, record_experiment=True)
    assert experiment is not None
    experiment_id = report["experiment_id"]
    assert show(experiment_id, repo_root=ROOT, runtime_root=tmp_path / "runtime") is not None
    assert show(experiment_id, repo_root=ROOT) is None


def test_runtime_root_and_ledger_must_be_canonical_pair(tmp_path: Path) -> None:
    _runtime_root, db_path = _runtime(tmp_path)
    with pytest.raises(ValueError, match="not the canonical ledgers.db"):
        run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=tmp_path / "different-runtime",
            report_dir=tmp_path / "reports",
            record_experiment=False,
        )


def test_policy_output_without_canonical_registry_is_forbidden(tmp_path: Path) -> None:
    runtime_root, db_path = _runtime(tmp_path)
    with pytest.raises(MarketBlendBlockedError, match="requires the canonical experiment registry"):
        run_gate(
            db_path=db_path,
            sport="mlb",
            market="total",
            tier="flat",
            spec=load_stage1_experiment_spec(SPEC_PATH, SPEC_HASH_PATH),
            spec_path=SPEC_PATH,
            lineage_entries={},
            lineage_manifest_hash=None,
            runtime_root=runtime_root,
            report_dir=tmp_path / "reports",
            policy_dir=tmp_path / "policies",
            record_experiment=False,
        )


def test_generic_immutable_writer_rejects_existing_candidate_artifact(tmp_path: Path) -> None:
    path = tmp_path / "policy-deadbeef.json"
    _write_immutable_json(path, {"artifact_hash": "deadbeef"})
    with pytest.raises(FileExistsError, match="immutable artifact already exists"):
        _write_immutable_json(path, {"artifact_hash": "different"})
    assert json.loads(path.read_text()) == {"artifact_hash": "deadbeef"}
    assert list(tmp_path.glob("*.staged")) == []


def test_implementation_manifest_covers_every_production_path_and_spec() -> None:
    manifest, aggregate = _implementation_manifest(SPEC_PATH)
    paths = {item["path"] for item in manifest}
    expected = {
        "src/model_prediction/market_blend.py",
        "src/model_prediction/rebuild/decision.py",
        "src/model_prediction/rebuild/mlb_market_matching.py",
        "src/model_prediction/rebuild/mlb_shadow_pipeline.py",
        "src/model_prediction/rebuild/shadow_ledger.py",
        "src/model_prediction/rebuild/economic.py",
        "src/model_prediction/domain.py",
        "src/model_prediction/ledger.py",
        "src/model_prediction/forward.py",
        "src/model_prediction/data_sources/polymarket_us.py",
        "src/model_prediction/data_sources/mlb_market_odds.py",
        "src/model_prediction/runtime_ledger_store.py",
        "src/model_prediction/models/mlb.py",
        "src/model_prediction/cli.py",
        "src/model_prediction/experiment_registry.py",
        "src/model_prediction/runtime_paths.py",
        "src/model_prediction/eligibility.py",
        "src/model_prediction/pricing.py",
        "scripts/market_blend_stage1.py",
        "config/research/market_blend_stage1_v1.json",
    }
    assert paths == expected
    assert len(aggregate) == 64
    assert all(len(item["sha256"]) == 64 for item in manifest)
