"""Tests for the I2 overlap integrity tooling (verify-integrity)."""

from __future__ import annotations

from model_prediction import ledger_parity
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_runtime_ledger_store import _mutation


def test_verify_integrity_empty_database_is_green(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        ok, problems = store.verify_integrity()
        assert ok is True
        assert problems == []
        assert store.event_count() == 0


def test_integrity_report_intact_chain(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        store.apply(_mutation())
        store.apply(_mutation(event_id="401690002"))
    report = ledger_parity.integrity_report(paths)
    assert report["events"] == 2
    assert report["chain_ok"] is True
    assert report["first_problem"] is None


def test_integrity_report_detects_tampering(tmp_path) -> None:
    """Same tamper pattern as test_runtime_ledger_store: rewrite an
    event_hash and the replay must report the first break."""
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        store.apply(_mutation())
        store.apply(_mutation(event_id="401690002"))
        store._conn.execute("UPDATE ledger_events SET event_hash = 'deadbeef' WHERE sequence = 1")
        store._conn.commit()
    report = ledger_parity.integrity_report(paths)
    assert report["chain_ok"] is False
    assert report["first_problem"] == "hash break at sequence 1"


def test_verify_integrity_subcommand_exit_codes(tmp_path, monkeypatch) -> None:
    """The subcommand resolves through RuntimePaths, so
    MODEL_PREDICTION_RUNTIME_ROOT must steer it to the test root."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    paths = RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "runtime")

    # Intact (including the empty-database case) -> exit 0.
    assert ledger_parity.main(["verify-integrity"]) == 0

    with RuntimeLedgerStore(paths) as store:
        store.apply(_mutation())
        store.apply(_mutation(event_id="401690002"))
        store._conn.execute("UPDATE ledger_events SET event_hash = 'deadbeef' WHERE sequence = 1")
        store._conn.commit()
    assert ledger_parity.main(["verify-integrity"]) == 1
