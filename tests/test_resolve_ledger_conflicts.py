"""Pin the conflict-resolution rules in scripts/resolve_ledger_conflicts.py.

The rules classify conflicting settled rows from canonical SQLite evidence.
These tests build synthetic stores with RuntimeLedgerStore.apply and assert
which rows the resolver archives/corrects — never against live data.
"""

from __future__ import annotations

from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from scripts import resolve_ledger_conflicts


def _seed_row(
    store: RuntimeLedgerStore,
    *,
    tier: str,
    pick_id: str,
    event_id: str,
    result: str,
    pnl_units: float,
    units: float,
    market_probability: float,
    created_at_utc: str,
    settled_at_utc: str,
    event_start_utc: str = "2026-08-03T00:00:00Z",
    market_snapshot_hash: str | None = None,
    model_probability: float = 0.55,
) -> None:
    store.apply(
        LedgerMutation(
            pick_id=pick_id,
            operation_id=f"op-{pick_id}",
            ledger_tier=tier,
            sport="tennis",
            event_type="settle",
            created_at_utc=created_at_utc,
            event_id=event_id,
            canonical_event_id=event_id,
            event_start_utc=event_start_utc,
            market_type="moneyline",
            selection="home",
            line=None,
            model_id="tennis-surface-elo-v1",
            model_artifact_hash=None,
            market_snapshot_hash=market_snapshot_hash,
            market_snapshot_archive_path=None,
            market_snapshot_record_id=None,
            feature_schema_version=None,
            model_probability=model_probability,
            market_probability=market_probability,
            edge=round(model_probability - market_probability, 6),
            confidence=None,
            units=units,
            decision="CALL",
            reason_code=None,
            status="settled",
            result=result,
            pnl_units=pnl_units,
            settled_at_utc=settled_at_utc,
        )
    )


def _store(tmp_path):
    return RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))


def _key(event_id: str) -> list[str]:
    return [event_id, "moneyline", "", "tennis-surface-elo-v1", "home"]


def test_r1_archives_stale_survivor_same_tier(tmp_path) -> None:
    # One tier, two settled rows for the same identity with different
    # economics: the older settlement is a stale pre-correction survivor.
    with _store(tmp_path) as store:
        _seed_row(
            store, tier="flat", pick_id="p-old", event_id="evt-1",
            result="win", pnl_units=0.4695, units=1.0, market_probability=0.68,
            created_at_utc="2026-08-02T19:29:00Z",
            settled_at_utc="2026-08-24T03:19:39Z",
        )
        _seed_row(
            store, tier="flat", pick_id="p-new", event_id="evt-1",
            result="win", pnl_units=0.4926, units=1.0, market_probability=0.67,
            created_at_utc="2026-08-02T19:29:00Z",
            settled_at_utc="2026-08-24T04:41:41Z",
        )
        plan = resolve_ledger_conflicts._resolve(store, _key("evt-1"))
    assert plan["resolution"] == "resolved"
    actions = plan["resolutions"]
    assert len(actions) == 1
    assert actions[0]["rule"] == "R1"
    assert actions[0]["pick_id"] == "p-old"
    assert actions[0]["action"] == "archive"


def test_r4_benign_sizing_variance_needs_no_mutation(tmp_path) -> None:
    # Same result and stake-normalized economics across tiers (loss rows,
    # different sizing) — nothing to correct, nothing to archive.
    with _store(tmp_path) as store:
        _seed_row(
            store, tier="research", pick_id="p-r", event_id="evt-2",
            result="loss", pnl_units=-1.25, units=1.25, market_probability=0.59,
            created_at_utc="2026-08-10T00:00:00Z",
            settled_at_utc="2026-08-22T12:23:26Z",
        )
        _seed_row(
            store, tier="gated_research", pick_id="p-g", event_id="evt-2",
            result="loss", pnl_units=-1.5, units=1.5, market_probability=0.59,
            created_at_utc="2026-08-10T00:00:00Z",
            settled_at_utc="2026-08-24T04:50:14Z",
        )
        plan = resolve_ledger_conflicts._resolve(store, _key("evt-2"))
    assert plan["resolution"] == "benign_or_unresolved"
    assert plan["resolutions"] == []


def test_r2_corrects_post_event_row_to_decision_time_quote(tmp_path) -> None:
    # Decision-time row (created pregame) vs a post-hoc row (created after
    # the event) with a different quote: the post-event economics are wrong.
    with _store(tmp_path) as store:
        _seed_row(
            store, tier="flat", pick_id="p-pregame", event_id="evt-3",
            result="win", pnl_units=0.9615, units=1.0, market_probability=0.5098,
            created_at_utc="2026-08-02T19:29:00Z",
            settled_at_utc="2026-08-03T22:55:16Z",
            event_start_utc="2026-08-03T01:00:00Z",
        )
        _seed_row(
            store, tier="main", pick_id="p-posthoc", event_id="evt-3",
            result="win", pnl_units=2.7, units=1.0, market_probability=0.2703,
            created_at_utc="2026-08-03T17:07:25Z",
            settled_at_utc="2026-08-03T22:55:14Z",
            event_start_utc="2026-08-03T01:00:00Z",
        )
        plan = resolve_ledger_conflicts._resolve(store, _key("evt-3"))
    assert plan["resolution"] == "resolved"
    actions = plan["resolutions"]
    assert len(actions) == 1
    assert actions[0]["rule"] == "R2-latest-settled"
    assert actions[0]["pick_id"] == "p-posthoc"
    assert actions[0]["new_pnl_units"] == 0.9615
    assert actions[0]["new_market_probability"] == 0.5098


def test_r3_lineage_backed_row_outranks_hashless_row(tmp_path) -> None:
    # Exactly one row carries market-snapshot lineage: the hashless row is
    # corrected stake-normalized to the lineage-backed row's economics.
    with _store(tmp_path) as store:
        _seed_row(
            store, tier="research", pick_id="p-hashless", event_id="evt-4",
            result="win", pnl_units=0.641, units=1.0, market_probability=0.6093,
            created_at_utc="2026-08-21T00:00:00Z",
            settled_at_utc="2026-08-22T15:39:19Z",
            market_snapshot_hash=None,
        )
        _seed_row(
            store, tier="gated_research", pick_id="p-lined", event_id="evt-4",
            result="win", pnl_units=1.0, units=1.25, market_probability=0.5495,
            created_at_utc="2026-08-21T00:00:00Z",
            settled_at_utc="2026-08-24T04:50:11Z",
            market_snapshot_hash="abcd1234",
        )
        plan = resolve_ledger_conflicts._resolve(store, _key("evt-4"))
    assert plan["resolution"] == "resolved"
    actions = plan["resolutions"]
    assert len(actions) == 1
    assert actions[0]["rule"] == "R3-lineage"
    assert actions[0]["pick_id"] == "p-hashless"
    # 1.0U at the reference stake-normalized pnl (1.0/1.25 = 0.8).
    assert actions[0]["new_pnl_units"] == 0.8
    assert actions[0]["new_market_probability"] == 0.5495


def test_disagreeing_results_are_never_auto_corrected(tmp_path) -> None:
    # One row says win, the other says loss — no rule may pick a side.
    with _store(tmp_path) as store:
        _seed_row(
            store, tier="flat", pick_id="p-w", event_id="evt-5",
            result="win", pnl_units=0.9, units=1.0, market_probability=0.52,
            created_at_utc="2026-08-02T19:29:00Z",
            settled_at_utc="2026-08-03T22:55:16Z",
            event_start_utc="2026-08-03T01:00:00Z",
        )
        _seed_row(
            store, tier="main", pick_id="p-l", event_id="evt-5",
            result="loss", pnl_units=-1.0, units=1.0, market_probability=0.52,
            created_at_utc="2026-08-03T17:07:25Z",
            settled_at_utc="2026-08-03T22:55:14Z",
            event_start_utc="2026-08-03T01:00:00Z",
        )
        plan = resolve_ledger_conflicts._resolve(store, _key("evt-5"))
    assert plan["resolution"] in {"benign_or_unresolved", "unresolved"}
    assert plan["resolutions"] == []
