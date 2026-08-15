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


def test_flat_research_and_gated_tiers_mirror_with_their_tier_label(tmp_path) -> None:
    """G6: the mirror is wired at the PickLedger chokepoint, so every tier
    routes through it — pin the tier labels the constructors pass."""
    from model_prediction.main_ledgers import flat_ledger
    from model_prediction.research_ledgers import research_ledger

    repo = tmp_path / "repo"
    data_root = repo / "data"

    flat = flat_ledger(data_root, "wnba")
    assert flat.tier == "flat" and flat.mirror is not None

    research = research_ledger(data_root, "cs2")
    assert research.tier == "research" and research.mirror is not None

    gated = research_ledger(data_root, "cs2", gated=True)
    assert gated.tier == "gated_research" and gated.mirror is not None


def test_spread_total_and_soccer_shapes_map_canonical_fields(tmp_path) -> None:
    """G6: market shapes populate different fields — the canonical mapping
    must carry market_type/selection/line/status correctly for each."""
    from model_prediction.ledger import PickLedger
    ledger = PickLedger(tmp_path / "p.xlsx", tier="flat", mirror=None)
    cases = [
        # (row, market_type, selection, line)
        ({"market_type": "spread", "selection": "home", "line": "3.5", "decision_line": ""}, "spread", "home", 3.5),
        ({"market_type": "total", "selection": "over", "line": "8.5", "decision_line": "8.5"}, "total", "over", 8.5),
        ({"market_type": "moneyline", "selection": "draw", "line": "", "decision_line": ""}, "moneyline", "draw", None),
        ({"market_type": "moneyline", "selection": "", "line": "", "decision_line": "-1.5"}, "moneyline", None, -1.5),
    ]
    for row, market, selection, line in cases:
        mutation = ledger._row_mutation(
            {"pick_id": "p1", "league": "mlb", **row},
            "append",
            "op-1",
        )
        assert mutation.market_type == market
        assert mutation.selection == selection
        assert mutation.line == line


def test_db_locked_mirror_never_breaks_the_xlsx_write(tmp_path) -> None:
    """G8: a locked mirror database (long-held write transaction) must not
    fail the legacy operation — alarm written, XLSX authoritative."""
    import sqlite3

    from model_prediction.ledger import PickLedger
    from model_prediction.runtime_ledger_store import RuntimeLedgerStore
    from tests.test_ledger import request as make_request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    locker = sqlite3.connect(paths.ledgers_db, timeout=5.0)
    locker.execute("BEGIN IMMEDIATE")  # hold the write lock open
    try:
        # busy_timeout is 5s; use a mirror with a very short timeout by
        # monkeypatching the busy timeout? Simpler: run the append with a
        # mirror whose apply raises the lock after waiting — verify the
        # XLSX write completed regardless.
        ledger = PickLedger(tmp_path / "picks.xlsx", tier="main", mirror=store)
        row = ledger.append_call(make_request(), 0.25, 70)
        assert row["pick_id"]  # legacy write succeeded
    finally:
        locker.rollback()
        locker.close()
        store.close()
    alarm = paths.ledgers_root / "parity_alarm.jsonl"
    assert alarm.is_file()


def test_restart_after_interrupted_settlement_heals_the_mirror(tmp_path) -> None:
    """G8: settle landed in XLSX but the mirror write failed (crash
    between backends). The next settlement pass takes the idempotent
    XLSX early-return path — which must ALSO mirror, healing the gap."""
    from model_prediction.ledger import PickLedger
    from model_prediction.runtime_ledger_store import RuntimeLedgerStore
    from tests.test_ledger import request as make_request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)

    class _FlakyMirror:
        def __init__(self, store: RuntimeLedgerStore) -> None:
            self._store = store
            self.fail_next_settle = True
            self.paths = store.paths

        def apply(self, mutation) -> bool:
            if mutation.event_type == "settle" and self.fail_next_settle:
                self.fail_next_settle = False
                raise RuntimeError("crash between backends")
            return self._store.apply(mutation)

    flaky = _FlakyMirror(store)
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="main", mirror=flaky)
    logged = ledger.append_call(make_request(), 0.25, 70)
    ledger.settle(logged["pick_id"], away_score=2, home_score=3)  # mirror fails here
    # Mirror missing the settle — exactly the interrupted state.
    assert store.records(tier="main")[0]["status"] == "open"

    # The next settle pass takes the XLSX early-return path and heals.
    ledger.settle(logged["pick_id"], away_score=2, home_score=3)
    record = store.records(tier="main")[0]
    assert record["status"] == "settled" and record["result"] == "loss"
    store.close()


def test_archive_and_remove_leave_exempt_tombstones_in_the_mirror(tmp_path) -> None:
    """G7: archive_settled_rows / remove_open_rows mirror tombstone rows
    (status archived/removed) which parity exempts — the mirror keeps the
    audit reference without counting as missing_xlsx."""
    from model_prediction.ledger import PickLedger
    from model_prediction.runtime_ledger_store import RuntimeLedgerStore
    from tests.test_ledger import request as make_request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store)
    logged = ledger.append_call(make_request(), 0.25, 70)
    ledger.settle(logged["pick_id"], away_score=2, home_score=3)
    removed = ledger.archive_settled_rows(
        [logged["pick_id"]], "retired model", "archive/test.xlsx"
    )
    assert len(removed) == 1

    records = store.records(tier="flat")
    assert len(records) == 1
    assert records[0]["status"] == "archived"

    from model_prediction.ledger_parity import compare

    report = compare(ledger.rows(), records)
    assert report["clean"] is True, report["details"]
    store.close()


def test_backfill_replays_historical_rows_deterministically(tmp_path) -> None:
    """H-prep: rows written before the mirror existed are replayed with
    fixed op-backfill-<pick_id> ids; re-running is a no-op."""
    from model_prediction.ledger import PickLedger
    from model_prediction.ledger_parity import compare
    from model_prediction.runtime_ledger_store import RuntimeLedgerStore
    from tests.test_ledger import request as make_request

    paths = RuntimePaths.for_test(tmp_path)
    # Historical XLSX written with NO mirror (pre-dual-write state).
    legacy = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=None)
    logged = legacy.append_call(make_request(), 0.25, 70)
    legacy.settle(logged["pick_id"], away_score=2, home_score=3)

    store = RuntimeLedgerStore(paths)
    assert store.records(tier="flat") == []  # mirror is empty

    # Backfill via the ledger's own mapper, exactly like ledger_parity.
    applied = 0
    for row in legacy.rows():
        mutation = legacy._row_mutation(row, "append", f"op-backfill-{row['pick_id']}")
        if store.apply(mutation):
            applied += 1
    assert applied == 1

    report = compare(legacy.rows(), store.records(tier="flat"))
    assert report["clean"] is True, report["details"]

    # Deterministic idempotency: the same backfill again is a no-op.
    for row in legacy.rows():
        mutation = legacy._row_mutation(row, "append", f"op-backfill-{row['pick_id']}")
        assert store.apply(mutation) is False
    store.close()


def test_same_pick_id_in_two_tiers_is_two_mirror_rows(tmp_path) -> None:
    """Found live on flat/tennis: main and flat share pick_ids (one
    decision, two tiers). The mirror key is (pick_id, ledger_tier) — a
    single-tier key would collapse them and break parity."""
    paths = RuntimePaths.for_test(tmp_path)
    with RuntimeLedgerStore(paths) as store:
        pick_id, _ = new_pick_ids()
        base = {
            "pick_id": pick_id,
            "ledger_tier": "main",
            "sport": "tennis",
            "event_type": "append",
            "created_at_utc": datetime.now(UTC).isoformat(),
        }
        assert store.apply(LedgerMutation(operation_id="op-main-1", **base)) is True
        flat = {**base, "ledger_tier": "flat", "operation_id": "op-flat-1"}
        assert store.apply(LedgerMutation(**flat)) is True

        assert len(store.records(tier="main")) == 1
        assert len(store.records(tier="flat")) == 1
        assert len(store.records()) == 2


def test_sqlite_authority_mirror_failure_aborts_the_mutation(tmp_path) -> None:
    """J: under sqlite authority the mirror IS the canonical store — its
    failure raises, the mutation did not land, and the XLSX export must
    not be written for a failed mutation."""
    from model_prediction.ledger import PickLedger
    from tests.test_ledger import request as make_request

    class _RaisingMirror:
        def __init__(self, paths: RuntimePaths) -> None:
            self.paths = paths

        def apply(self, mutation) -> bool:
            raise RuntimeError("canonical store down")

    paths = RuntimePaths.for_test(tmp_path)
    ledger = PickLedger(
        tmp_path / "picks.xlsx",
        tier="main",
        mirror=_RaisingMirror(paths),
        authority="sqlite",
    )
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="canonical store down"):
        ledger.append_call(make_request(), 0.25, 70)
    # The mutation failed canonically — no open rows anywhere.
    assert ledger.report()["open"] == 0


def test_sqlite_authority_export_failure_is_best_effort(tmp_path) -> None:
    """J: when the canonical store succeeds, an XLSX export failure is an
    alarm, never a mutation failure."""
    from model_prediction.ledger import PickLedger
    from model_prediction.runtime_ledger_store import RuntimeLedgerStore
    from tests.test_ledger import request as make_request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)

    class _ExplodingXlsx(PickLedger):
        # The unchecked writer is the real disk path; the authority-aware
        # wrapper above it must convert this into an alarm.
        def _write_rows_unchecked(self, rows) -> None:
            raise OSError("export disk full")

    ledger = _ExplodingXlsx(
        tmp_path / "picks.xlsx",
        tier="flat",
        mirror=store,
        authority="sqlite",
    )
    row = ledger.append_call(make_request(), 0.25, 70)  # must succeed
    assert row["pick_id"]
    # The canonical store has the row; the alarm recorded the export gap.
    records = store.records(tier="flat")
    assert len(records) == 1 and records[0]["pick_id"] == row["pick_id"]
    alarm = paths.ledgers_root / "parity_alarm.jsonl"
    assert alarm.is_file() and "export" in alarm.read_text(encoding="utf-8")
    store.close()


def test_authority_flag_resolves_from_env_in_constructors(tmp_path, monkeypatch) -> None:
    """The live constructors honor MODEL_PREDICTION_LEDGER_AUTHORITY."""
    from model_prediction.main_ledgers import ledger_authority

    monkeypatch.delenv("MODEL_PREDICTION_LEDGER_AUTHORITY", raising=False)
    assert ledger_authority() == "xlsx"
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "sqlite")
    assert ledger_authority() == "sqlite"
