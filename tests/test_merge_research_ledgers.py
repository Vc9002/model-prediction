from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_research_ledgers.py"
spec = importlib.util.spec_from_file_location("merge_research_ledgers", SCRIPT)
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


def _row(
    pick_id,
    tier,
    *,
    event="e1",
    created="2026-10-01T10:00:00Z",
    status="settled",
    result="win",
    pnl=0.9,
    sport="cs2",
):
    return LedgerMutation(
        pick_id=pick_id,
        operation_id=f"op-{tier}-{pick_id}",
        ledger_tier=tier,
        sport=sport,
        event_type="append",
        created_at_utc=created,
        event_id=event,
        event_start_utc="2026-10-01T18:00:00Z",
        market_type="moneyline",
        selection="home",
        line=None,
        model_id="cs2-tiered-elo-v6",
        model_probability=0.6,
        market_probability=0.5,
        edge=0.1,
        confidence=0.7,
        units=1.0,
        decision="CALL",
        reason_code="QUALIFIED",
        status=status,
        result=result if status == "settled" else None,
        pnl_units=pnl if status == "settled" else None,
        settled_at_utc="2026-10-02T01:00:00Z" if status == "settled" else None,
        decision_payload={"sportsbook": "polymarket"},
        feature_payload={"elo": 1500},
    )


def _seed(paths: RuntimePaths) -> None:
    with RuntimeLedgerStore(paths) as store:
        # research: duplicate settled rows for one pick (keep the latest), a second pick, an open row,
        # a superseded row and a sport outside the research set.
        store.apply(_row("r-old", "research", created="2026-10-01T05:00:00Z", pnl=0.8))
        store.apply(_row("r-new", "research", created="2026-10-01T10:00:00Z", pnl=0.9))
        store.apply(_row("r-two", "research", event="e2", result="loss", pnl=-1.0))
        store.apply(_row("r-open", "research", event="e3", status="open"))
        store.apply(_row("r-sup", "research", event="e4", status="superseded"))
        store.apply(_row("r-mlb", "research", event="e5", sport="mlb"))
        store.apply(_row("g-new", "gated_research", created="2026-10-01T10:30:00Z", pnl=0.9))


def test_dry_run_plans_but_writes_nothing(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    _seed(paths)
    before = Path(paths.ledgers_db).read_bytes()

    result = merge.run(paths, apply=False)

    assert result["to_copy"] == 3  # r-new, r-two, g-new
    assert result["duplicate_rows_dropped"] == 1  # r-old
    assert result["applied"] == 0
    assert Path(paths.ledgers_db).read_bytes() == before


def test_apply_copies_latest_settled_rows_with_provenance_and_is_idempotent(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    _seed(paths)

    first = merge.run(paths, apply=True)

    assert first["applied"] == 3
    assert first["integrity_ok"] is True
    with RuntimeLedgerStore(paths) as store:
        flat = {r["pick_id"]: r for r in store.records(tier="flat")}
        main = {r["pick_id"]: r for r in store.records(tier="main")}
        assert set(flat) == {"r-new", "r-two"}
        assert set(main) == {"g-new"}
        copied = flat["r-new"]
        assert copied["result"] == "win" and copied["pnl_units"] == 0.9
        assert copied["created_at_utc"] == "2026-10-01T10:00:00Z"
        payload = json.loads(copied["decision_payload_json"])
        assert payload["merged_from_tier"] == "research"
        assert payload["merged_from_pick_id"] == "r-new"
        assert payload["merge_tag"] == merge.MERGE_TAG
        # the legacy rows are untouched
        assert len(store.records(tier="research")) == 6
        assert len(store.records(tier="gated_research")) == 1

    second = merge.run(paths, apply=True)
    assert second["applied"] == 0
    assert second["already_present"] == 3


def test_existing_destination_key_is_never_overwritten(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    _seed(paths)
    with RuntimeLedgerStore(paths) as store:
        store.apply(_row("flat-existing", "flat", created="2026-10-01T12:00:00Z", pnl=5.0))

    result = merge.run(paths, apply=True)

    assert result["already_present"] == 1  # the e1 key is already in flat
    with RuntimeLedgerStore(paths) as store:
        flat = {r["pick_id"]: r for r in store.records(tier="flat")}
        assert set(flat) == {"flat-existing", "r-two"}
        assert flat["flat-existing"]["pnl_units"] == 5.0


def test_apply_rebuilds_the_workbooks_the_dashboard_reads(tmp_path) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    _seed(paths)
    data_root = tmp_path / "data"

    result = merge.run(paths, apply=True, data_root=data_root)

    assert result["exports_rebuilt"] == 2  # cs2 flat and cs2 main
    assert (data_root / "sports" / "cs2" / "flat.xlsx").exists() or (data_root / "flat" / "cs2.xlsx").exists()
    assert (data_root / "sports" / "cs2" / "main.xlsx").exists() or (data_root / "main" / "cs2.xlsx").exists()
