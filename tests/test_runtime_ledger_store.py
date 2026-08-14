"""Tests for the runtime ledger store (consolidation G1-G3)."""

from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.runtime_ledger_store import (
    LedgerMutation,
    RuntimeLedgerStore,
    new_pick_ids,
)
from model_prediction.runtime_paths import RuntimePaths


def _mutation(**overrides) -> LedgerMutation:
    pick_id, operation_id = new_pick_ids()
    fields: dict = {
        "pick_id": pick_id,
        "operation_id": operation_id,
        "ledger_tier": "main",
        "sport": "mlb",
        "event_type": "append",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "event_id": "401690001",
        "event_start_utc": "2026-08-14T23:05:00Z",
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "model_id": "mlb-elo-trend-lr-v8",
        "model_artifact_hash": "abc123",
        "feature_schema_version": "1",
        "model_probability": 0.61,
        "market_probability": 0.55,
        "edge": 0.06,
        "confidence": 0.7,
        "units": 1.5,
        "decision": "CALL",
        "reason_code": "CALL_LEARNED_CONFIDENCE",
        "status": "open",
        "decision_payload": {"extra": "tail-field"},
        "feature_payload": {"elo": 1520},
    }
    fields.update(overrides)
    return LedgerMutation(**fields)


def test_record_and_idempotent_retry(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        m = _mutation()
        assert store.apply(m) is True
        # The SAME mutation object re-applied (interrupted-retry case) is
        # a no-op — no duplicate record, no duplicate audit event.
        assert store.apply(m) is False
        records = store.records()
        assert len(records) == 1
        assert records[0]["pick_id"] == m.pick_id
        assert records[0]["model_probability"] == 0.61
        assert records[0]["selection"] == "home"

        events = store._conn.execute("SELECT COUNT(*) AS n FROM ledger_events").fetchone()
        assert events["n"] == 1


def test_mutation_and_audit_event_commit_in_one_transaction(tmp_path) -> None:
    """G2: the pick mutation and its audit event either both exist or
    neither does — one row per applied mutation, hash-linked."""
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        m1 = _mutation()
        m2 = _mutation(event_id="401690002")
        store.apply(m1)
        store.apply(m2)

        events = store._conn.execute(
            "SELECT * FROM ledger_events ORDER BY sequence"
        ).fetchall()
        assert len(events) == 2
        assert events[0]["previous_hash"] is None
        assert events[1]["previous_hash"] == events[0]["event_hash"]
        assert events[1]["pick_id"] == m2.pick_id

        ok, problems = store.verify_integrity()
        assert ok, problems


def test_tampering_breaks_the_integrity_check(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        store.apply(_mutation())
        store.apply(_mutation(event_id="401690002"))
        store._conn.execute(
            "UPDATE ledger_events SET event_hash = 'deadbeef' WHERE sequence = 1"
        )
        store._conn.commit()
        ok, problems = store.verify_integrity()
        assert not ok
        assert problems and "hash break" in problems[0]


def test_settle_mutation_updates_the_same_record(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        m1 = _mutation()
        store.apply(m1)
        settle = _mutation(
            pick_id=m1.pick_id,
            operation_id="op-settle-1",
            event_type="settle",
            status="settled",
            result="won",
            pnl_units=1.32,
            settled_at_utc=datetime.now(UTC).isoformat(),
        )
        assert store.apply(settle) is True
        records = store.records()
        assert len(records) == 1  # update, not a second row
        assert records[0]["status"] == "settled"
        assert records[0]["result"] == "won"
        assert records[0]["pnl_units"] == 1.32
        events = store._conn.execute("SELECT COUNT(*) AS n FROM ledger_events").fetchone()
        assert events["n"] == 2


def test_new_pick_ids_are_unique_and_stable(tmp_path) -> None:
    a = new_pick_ids()
    b = new_pick_ids()
    assert a != b
    assert a[0].startswith("pick-") and a[1].startswith("op-")


def test_main_ledger_dual_writes_append_and_settle_to_the_mirror(tmp_path) -> None:
    """G4 integration: the live main_ledger constructor mirrors every
    append and settle into the runtime ledger store with the SAME pick_id,
    and parity between the XLSX row and the mirror row is exact."""
    from model_prediction.ledger_parity import compare
    from model_prediction.main_ledgers import main_ledger

    from tests.test_ledger import request as make_request

    repo = tmp_path / "repo"
    data_root = repo / "data"
    ledger = main_ledger(data_root, "mlb")  # mirror resolves from data_root
    logged = ledger.append_call(make_request(), 0.25, 70)
    settled = ledger.settle(logged["pick_id"], away_score=2, home_score=3)

    paths = RuntimePaths(repo_root=repo, runtime_root=repo / "data")
    with RuntimeLedgerStore(paths) as store:
        records = store.records(tier="main")
        assert len(records) == 1
        record = records[0]
        assert record["pick_id"] == logged["pick_id"]
        assert record["status"] == settled["status"]
        assert record["result"] == "loss"
        assert abs(record["pnl_units"] - (-0.25)) <= 1e-9
        assert abs(record["model_probability"] - 0.59) <= 1e-12
        assert record["model_id"] == "mlb-test-v1"

        # Append + settle = two hash-linked audit events, chain intact.
        events = store._conn.execute(
            "SELECT COUNT(*) AS n FROM ledger_events"
        ).fetchone()
        assert events["n"] == 2
        ok, problems = store.verify_integrity()
        assert ok, problems

        report = compare(ledger.rows(), records)
        assert report["clean"] is True, report["details"]


def test_mirror_failure_never_breaks_the_xlsx_write(tmp_path) -> None:
    """G4: SQLite failure leaves the legacy operation valid — the mirror
    is DEGRADED with a parity alarm, never a raised ledger error."""
    from model_prediction.ledger import PickLedger

    from tests.test_ledger import request as make_request

    class _RaisingMirror:
        def __init__(self, paths: RuntimePaths) -> None:
            self.paths = paths

        def apply(self, mutation) -> bool:
            raise RuntimeError("mirror down")

    paths = RuntimePaths.for_test(tmp_path)
    ledger = PickLedger(
        tmp_path / "picks.xlsx",
        tier="main",
        mirror=_RaisingMirror(paths),
    )
    row = ledger.append_call(make_request(), 0.25, 70)  # must succeed
    assert row["pick_id"]
    alarm = paths.ledgers_root / "parity_alarm.jsonl"
    assert alarm.is_file()
    assert "append" in alarm.read_text(encoding="utf-8")
