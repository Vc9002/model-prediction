"""Hypothesis Stateful Property-Based Ledger Invariant Testing.

Performs automated stateful fuzzing of ledger transitions, financial conservation,
and split-brain integrity invariants:

Invariants Verified:
1. P&L conservation: sum(P&L_individual) == P&L_aggregate across all settled trades.
2. State machine irreversibility: Settled / voided picks can never revert to open.
3. Dedup idempotency: Repeated mutations with identical operation_id are strictly rejected as no-ops.
4. Hash-Chain Cryptographic Integrity: SHA-256 parent-link chain remains unbroken.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from model_prediction.domain import (
    MarketType,
    PickStatus,
    utc_now,
)
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths


class LedgerInvariantStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self._temp_dir: Path | None = None
        self.paths: RuntimePaths | None = None
        self.store: RuntimeLedgerStore | None = None
        self.known_picks: dict[str, dict[str, Any]] = {}
        self.applied_operations: set[str] = set()

    @initialize(target_dir=st.runner())
    def setup_store(self, target_dir: Any) -> None:
        import tempfile

        self._temp_dir_obj = tempfile.TemporaryDirectory()
        base = Path(self._temp_dir_obj.name)
        repo_root = base / "repo"
        runtime_root = base / "runtime"
        repo_root.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.paths = RuntimePaths(repo_root=repo_root, runtime_root=runtime_root)
        self.store = RuntimeLedgerStore(self.paths)
        self.known_picks = {}
        self.applied_operations = set()

    def teardown(self) -> None:
        if self.store is not None:
            self.store.close()
        if hasattr(self, "_temp_dir_obj"):
            self._temp_dir_obj.cleanup()

    @rule(
        sport=st.sampled_from(["mlb", "soccer", "tennis", "wnba", "esports"]),
        event_num=st.integers(min_value=1, max_value=100),
        stake=st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False),
        model_prob=st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False),
    )
    def append_open_pick(self, sport: str, event_num: int, stake: float, model_prob: float) -> None:
        assert self.store is not None
        pick_id = f"pick-{sport}-{event_num}"
        op_id = f"op-append-{pick_id}"

        if pick_id in self.known_picks:
            return

        now_iso = utc_now().isoformat()
        mutation = LedgerMutation(
            pick_id=pick_id,
            operation_id=op_id,
            ledger_tier="main",
            sport=sport,
            event_type="append",
            created_at_utc=now_iso,
            event_id=f"event-{sport}-{event_num}",
            market_type=MarketType.MONEYLINE.value,
            selection="HOME",
            model_id=f"{sport}-v1",
            model_probability=round(model_prob, 4),
            market_probability=0.5,
            units=round(stake, 4),
            status=PickStatus.OPEN.value,
            decision_payload={
                "pick_id": pick_id,
                "sport": sport,
                "units": f"{stake:.4f}",
                "status": PickStatus.OPEN.value,
            },
        )

        applied = self.store.apply(mutation)
        assert applied is True
        self.applied_operations.add(op_id)
        self.known_picks[pick_id] = {
            "sport": sport,
            "status": PickStatus.OPEN.value,
            "units": round(stake, 4),
            "pnl": 0.0,
        }

    @rule(
        data=st.data(),
        result=st.sampled_from(["win", "loss", "push"]),
        pnl_multiplier=st.floats(min_value=-1.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    def settle_pick(self, data: st.DataObject, result: str, pnl_multiplier: float) -> None:
        assert self.store is not None
        open_picks = [p for p, info in self.known_picks.items() if info["status"] == PickStatus.OPEN.value]
        if not open_picks:
            return

        pick_id = data.draw(st.sampled_from(open_picks))
        info = self.known_picks[pick_id]
        op_id = f"op-settle-{pick_id}"

        pnl = round(info["units"] * pnl_multiplier, 4) if result != "push" else 0.0
        now_iso = utc_now().isoformat()
        mutation = LedgerMutation(
            pick_id=pick_id,
            operation_id=op_id,
            ledger_tier="main",
            sport=info["sport"],
            event_type="settle",
            created_at_utc=now_iso,
            status=PickStatus.SETTLED.value,
            result=result,
            pnl_units=pnl,
            settled_at_utc=now_iso,
            decision_payload={
                "status": PickStatus.SETTLED.value,
                "result": result,
                "pnl_units": f"{pnl:.4f}",
            },
        )

        applied = self.store.apply(mutation)
        assert applied is True
        self.applied_operations.add(op_id)
        info["status"] = PickStatus.SETTLED.value
        info["pnl"] = pnl

    @rule(data=st.data())
    def duplicate_operation_rejection(self, data: st.DataObject) -> None:
        assert self.store is not None
        if not self.known_picks:
            return
        pick_id = data.draw(st.sampled_from(list(self.known_picks.keys())))
        info = self.known_picks[pick_id]
        op_id = f"op-append-{pick_id}" if info["status"] == PickStatus.OPEN.value else f"op-settle-{pick_id}"

        # Attempt to apply a duplicate mutation with the same pick_id and operation_id
        dup = LedgerMutation(
            pick_id=pick_id,
            operation_id=op_id,
            ledger_tier="main",
            sport=info["sport"],
            event_type="append" if info["status"] == PickStatus.OPEN.value else "settle",
            created_at_utc=utc_now().isoformat(),
        )
        assert self.store.apply(dup) is False

    @invariant()
    def pnl_conservation_invariant(self) -> None:
        """Sum of individual record P&Ls must identically match the aggregate total."""
        assert self.store is not None
        records = self.store.records(tier="main")
        expected_total_pnl = sum(
            info["pnl"] for info in self.known_picks.values() if info["status"] == PickStatus.SETTLED.value
        )
        actual_total_pnl = sum(
            float(r["pnl_units"] or 0.0) for r in records if r.get("status") == PickStatus.SETTLED.value
        )
        assert math.isclose(expected_total_pnl, actual_total_pnl, abs_tol=1e-3)

    @invariant()
    def state_machine_irreversibility_invariant(self) -> None:
        """Settled picks in SQLite store must never revert to open."""
        assert self.store is not None
        records = self.store.records(tier="main")
        for r in records:
            pid = r["pick_id"]
            if pid in self.known_picks and self.known_picks[pid]["status"] == PickStatus.SETTLED.value:
                assert r["status"] == PickStatus.SETTLED.value

    @invariant()
    def hash_chain_integrity_invariant(self) -> None:
        """Cryptographic SHA-256 parent-link chain must always remain 100% valid."""
        assert self.store is not None
        ok, problems = self.store.verify_integrity()
        assert ok is True, f"Cryptographic hash chain broken: {problems}"


TestLedgerStateMachine = LedgerInvariantStateMachine.TestCase
