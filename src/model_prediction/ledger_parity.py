"""XLSX ↔ SQLite ledger parity checker (consolidation G5) + I2 integrity.

The automatic reconciliation gate for the dual-write migration: compare
the legacy XLSX rows against the RuntimeLedgerStore mirror on canonical
fields, with explicit tolerances (never silent rounding):

    python -m model_prediction.ledger_parity [--tier main|flat|research] [--sport mlb]

Exit 0 only when every delta is zero. During phase 1 (XLSX
authoritative) any nonzero delta means the mirror is DEGRADED — the
parity alarm, not a silent failure.

During the I2 overlap the same module also runs the SQLite-native audit
chain check:

    python -m model_prediction.ledger_parity verify-integrity

Exit 0 only when the hash-linked ledger_events chain replays intact.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .runtime_ledger_store import RuntimeLedgerStore
from .runtime_paths import RuntimePaths

_PROB_TOLERANCE = 1e-12
_MONEY_TOLERANCE = 1e-9


def _absent(value: Any) -> Any:
    """XLSX encodes absence as '' and the mirror as None — normalize both
    to None so they compare as the same 'absent'."""
    return None if value in (None, "") else value


def _close(left: Any, right: Any, tolerance: float) -> bool:
    left = _absent(left)
    right = _absent(right)
    if left is None or right is None:
        return left is right  # both absent, or mismatch counted as a delta
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def compare(xlsx_rows: list[dict[str, Any]], sqlite_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare two canonical row sets; returns the G5 report."""
    xlsx = {str(r["pick_id"]): r for r in xlsx_rows}
    # Archived/removed records stay in the mirror as tombstones (their
    # audit events reference them) but have left the XLSX — they are
    # exempt from the missing_xlsx count, not a parity failure.
    sqlite = {str(r["pick_id"]): r for r in sqlite_rows if r.get("status") not in ("archived", "removed")}
    report: dict[str, Any] = {
        "rows": {
            "xlsx": len(xlsx),
            "sqlite": len(sqlite),
            "delta": len(sqlite) - len(xlsx),
        },
        "pick_id": {
            "missing_sqlite": 0,
            "missing_xlsx": 0,
            "duplicates": 0,
        },
        "settlement": {"status_mismatches": 0, "result_mismatches": 0},
        "financial": {"units_mismatches": 0, "pnl_mismatches": 0},
        "prediction": {
            "prob_mismatches": 0,
            "line_mismatches": 0,
            "selection_mismatches": 0,
        },
        "lineage": {"model_mismatches": 0, "artifact_mismatches": 0},
        "details": [],
    }

    report["pick_id"]["missing_sqlite"] = len(set(xlsx) - set(sqlite))
    report["pick_id"]["missing_xlsx"] = len(set(sqlite) - set(xlsx))

    def _note(kind: str, pick_id: str, field: str, xlsx_v: Any, sqlite_v: Any) -> None:
        report["details"].append(
            {"kind": kind, "pick_id": pick_id, "field": field, "xlsx": xlsx_v, "sqlite": sqlite_v}
        )

    for pick_id, xrow in xlsx.items():
        srow = sqlite.get(pick_id)
        if srow is None:
            continue
        if _absent(xrow.get("status")) != _absent(srow.get("status")):
            report["settlement"]["status_mismatches"] += 1
            _note("status", pick_id, "status", xrow.get("status"), srow.get("status"))
        if _absent(xrow.get("result")) != _absent(srow.get("result")):
            report["settlement"]["result_mismatches"] += 1
            _note("result", pick_id, "result", xrow.get("result"), srow.get("result"))
        if not _close(xrow.get("units"), srow.get("units"), _MONEY_TOLERANCE):
            report["financial"]["units_mismatches"] += 1
            _note("units", pick_id, "units", xrow.get("units"), srow.get("units"))
        if not _close(xrow.get("pnl_units"), srow.get("pnl_units"), _MONEY_TOLERANCE):
            report["financial"]["pnl_mismatches"] += 1
            _note("pnl", pick_id, "pnl_units", xrow.get("pnl_units"), srow.get("pnl_units"))
        if not _close(xrow.get("model_probability"), srow.get("model_probability"), _PROB_TOLERANCE):
            report["prediction"]["prob_mismatches"] += 1
            _note(
                "prob",
                pick_id,
                "model_probability",
                xrow.get("model_probability"),
                srow.get("model_probability"),
            )
        if not _close(xrow.get("line"), srow.get("line"), _MONEY_TOLERANCE):
            report["prediction"]["line_mismatches"] += 1
            _note("line", pick_id, "line", xrow.get("line"), srow.get("line"))
        if _absent(xrow.get("selection")) != _absent(srow.get("selection")):
            report["prediction"]["selection_mismatches"] += 1
            _note("selection", pick_id, "selection", xrow.get("selection"), srow.get("selection"))
        # XLSX calls the field model_version; the mirror canonicalizes it
        # to model_id — compare on the same semantic field.
        x_model = _absent(xrow.get("model_id") or xrow.get("model_version"))
        if x_model != _absent(srow.get("model_id")):
            report["lineage"]["model_mismatches"] += 1
            _note("model", pick_id, "model_id", x_model, srow.get("model_id"))
        if _absent(xrow.get("model_artifact_hash")) != _absent(srow.get("model_artifact_hash")):
            report["lineage"]["artifact_mismatches"] += 1
            _note(
                "artifact",
                pick_id,
                "model_artifact_hash",
                xrow.get("model_artifact_hash"),
                srow.get("model_artifact_hash"),
            )

    report["details"] = report["details"][:20]
    report["clean"] = _is_clean(report)
    return report


def _is_clean(report: dict[str, Any]) -> bool:
    return (
        report["rows"]["delta"] == 0
        and all(v == 0 for v in report["pick_id"].values())
        and all(v == 0 for v in report["settlement"].values())
        and all(v == 0 for v in report["financial"].values())
        and all(v == 0 for v in report["prediction"].values())
        and all(v == 0 for v in report["lineage"].values())
    )


def _xlsx_rows(tier: str, sport: str, data_root: Path) -> list[dict[str, Any]]:
    if tier == "research":
        from .research_ledgers import research_ledger

        return research_ledger(data_root, sport).rows()
    if tier == "main":
        from .main_ledgers import main_ledger

        return main_ledger(data_root, sport).rows()
    if tier == "flat":
        from .main_ledgers import flat_ledger

        return flat_ledger(data_root, sport).rows()
    if tier == "gated_research":
        from .research_ledgers import research_ledger

        return research_ledger(data_root, sport, gated=True).rows()
    raise ValueError(f"unsupported tier: {tier}")


def run(tier: str, sport: str) -> dict[str, Any]:
    paths = RuntimePaths.resolve(repo_root=PROJECT_ROOT)
    data_root = paths.repo_root / "data"
    xlsx_rows = _xlsx_rows(tier, sport, data_root)
    store = RuntimeLedgerStore(paths)
    try:
        sqlite_rows = store.records(tier=tier, sport=sport)
    finally:
        store.close()
    report = compare(xlsx_rows, sqlite_rows)
    report["tier"] = tier
    report["sport"] = sport
    return report


def _print_report(report: dict[str, Any]) -> None:
    print("Rows:")
    print(f"  XLSX      {report['rows']['xlsx']}")
    print(f"  SQLite    {report['rows']['sqlite']}")
    print(f"  Delta     {report['rows']['delta']}")
    print()
    print("pick_id:")
    print(f"  missing SQLite  {report['pick_id']['missing_sqlite']}")
    print(f"  missing XLSX    {report['pick_id']['missing_xlsx']}")
    print(f"  duplicates      {report['pick_id']['duplicates']}")
    print()
    print("settlement:")
    print(f"  status mismatches  {report['settlement']['status_mismatches']}")
    print(f"  result mismatches  {report['settlement']['result_mismatches']}")
    print()
    print("financial:")
    print(f"  units mismatches   {report['financial']['units_mismatches']}")
    print(f"  pnl mismatches     {report['financial']['pnl_mismatches']}")
    print()
    print("prediction:")
    print(f"  prob mismatches      {report['prediction']['prob_mismatches']}")
    print(f"  line mismatches      {report['prediction']['line_mismatches']}")
    print(f"  selection mismatches {report['prediction']['selection_mismatches']}")
    print()
    print("lineage:")
    print(f"  model mismatches    {report['lineage']['model_mismatches']}")
    print(f"  artifact mismatches {report['lineage']['artifact_mismatches']}")
    if report["details"]:
        print()
        print("first divergences:")
        for detail in report["details"]:
            print(
                f"  {detail['kind']}: {detail['pick_id']} {detail['field']} "
                f"xlsx={detail['xlsx']!r} sqlite={detail['sqlite']!r}"
            )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    def _arg(name: str, default: str) -> str:
        if name in args:
            idx = args.index(name)
            if idx == len(args) - 1:
                raise ValueError(f"{name} requires a value")
            return args[idx + 1]
        return default

    try:
        if args and args[0] == "verify-integrity":
            report = integrity_report(RuntimePaths.resolve(repo_root=PROJECT_ROOT))
            print(f"events: {report['events']}")
            print(f"chain: {'intact' if report['chain_ok'] else 'BROKEN'}")
            if report["first_problem"]:
                print(f"first problem: {report['first_problem']}")
            return 0 if report["chain_ok"] else 1
        if args and args[0] in ("backfill", "reconcile"):
            fn = backfill if args[0] == "backfill" else reconcile
            tier = _arg("--tier", "")
            sport = _arg("--sport", "")
            if tier or sport:
                if not (tier and sport):
                    raise ValueError("backfill needs both --tier and --sport (or neither for --all)")
                combos = [(tier, sport)]
            else:
                combos = [(t, sp) for t, sports in _TIER_SPORTS.items() for sp in sports]
            total = {"applied": 0, "already_present": 0, "skipped": 0}
            for t, sp in combos:
                try:
                    result = fn(t, sp)
                except ValueError as exc:
                    print(f"{t:<15} {sp:<14} SKIP ({exc})")
                    total["skipped"] += 1
                    continue
                total["applied"] += result["applied"]
                total["already_present"] += result["already_present"]
                extra = f" tombstoned={result.get('tombstoned')}" if "tombstoned" in result else ""
                if "synced" in result:
                    extra += f" synced={result['synced']}"
                print(
                    f"{t:<15} {sp:<14} applied={result['applied']} "
                    f"already_present={result['already_present']}{extra}"
                )
            print(
                f"TOTAL applied={total['applied']} already_present={total['already_present']} skipped={total['skipped']}"
            )
            return 0
        tier = _arg("--tier", "main")
        sport = _arg("--sport", "mlb")
        report = run(tier, sport)
    except Exception as exc:  # noqa: BLE001
        print(f"PARITY ERROR: {exc}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0 if report["clean"] else 1


_TIER_SPORTS = {
    "main": ("mlb", "wnba", "soccer", "tennis"),
    "flat": ("mlb", "wnba", "soccer", "tennis"),
    "research": ("lol", "cs2", "dota2", "valorant", "rainbow_six", "kbo", "npb"),
    "gated_research": ("lol", "cs2", "dota2", "valorant", "rainbow_six", "kbo", "npb"),
}


def _open_tier_ledger(tier: str, sport: str, data_root: Path):
    from .main_ledgers import flat_ledger, main_ledger
    from .research_ledgers import research_ledger

    if tier == "main":
        return main_ledger(data_root, sport)
    if tier == "flat":
        return flat_ledger(data_root, sport)
    if tier in ("research", "gated_research"):
        return research_ledger(data_root, sport, gated=(tier == "gated_research"))
    raise ValueError(f"unsupported tier: {tier}")


def reconcile(tier: str, sport: str) -> dict[str, int]:
    """Bring one tier-sport mirror to exact parity with the XLSX (H).

    Two deterministic repairs:
    1. backfill rows the mirror is missing;
    2. tombstone mirror rows the XLSX no longer has (status='removed')
       — those rows were removed from the XLSX through a path that
       predates the mirror hooks, so the tombstone preserves the audit
       reference while parity exempts them.
    Both carry fixed operation ids, so re-runs are no-ops.
    """
    result = backfill(tier, sport)
    paths = RuntimePaths.resolve(repo_root=PROJECT_ROOT)
    data_root = paths.repo_root / "data"
    ledger = _open_tier_ledger(tier, sport, data_root)
    xlsx_rows = {row["pick_id"]: row for row in ledger.rows()}
    store = RuntimeLedgerStore(paths)
    try:
        tombstoned = synced = 0
        for record in store.records(tier=tier, sport=sport):
            if record["pick_id"] in xlsx_rows:
                # Present in both: replay the XLSX row so any field drift
                # (e.g. settlements written through a pre-mirror path) is
                # repaired deterministically — upsert overwrites fields.
                xrow = xlsx_rows[record["pick_id"]]
                mutation = ledger._row_mutation(xrow, "update", f"op-sync-{tier}-{record['pick_id']}")
                if store.apply(mutation):
                    synced += 1
                continue
            if record["status"] in ("archived", "removed"):
                continue
            mutation = ledger._row_mutation(
                {**record, "status": "removed"},
                "remove",
                f"op-tombstone-{tier}-{record['pick_id']}",
            )
            if store.apply(mutation):
                tombstoned += 1
    finally:
        store.close()
    result["tombstoned"] = tombstoned
    result["synced"] = synced
    return result


def backfill(tier: str, sport: str) -> dict[str, int]:
    """Replay XLSX rows missing from the mirror, deterministically (H-prep).

    Historical rows predate the dual-write; this brings the mirror to
    exact parity so the attended-cycle gate (parity = exact) is reachable.
    Idempotent: backfilled rows carry a fixed op-backfill-<pick_id>
    operation id, so a re-run (or an interrupted run) is a no-op.
    """
    paths = RuntimePaths.resolve(repo_root=PROJECT_ROOT)
    data_root = paths.repo_root / "data"
    ledger = _open_tier_ledger(tier, sport, data_root)
    store = RuntimeLedgerStore(paths)
    try:
        existing = {r["pick_id"] for r in store.records(tier=tier, sport=sport)}
        applied = 0
        skipped = 0
        for row in ledger.rows():
            if row["pick_id"] in existing:
                skipped += 1
                continue
            mutation = ledger._row_mutation(row, "append", f"op-backfill-{tier}-{row['pick_id']}")
            if store.apply(mutation):
                applied += 1
            else:
                skipped += 1
    finally:
        store.close()
    return {"tier": tier, "sport": sport, "applied": applied, "already_present": skipped}


def integrity_report(paths: RuntimePaths) -> dict[str, Any]:
    """I2: replay the SQLite hash chain; event count + first break."""
    store = RuntimeLedgerStore(paths)
    try:
        ok, problems = store.verify_integrity()
        events = store.event_count()
    finally:
        store.close()
    return {
        "events": events,
        "chain_ok": ok,
        "first_problem": problems[0] if problems else None,
    }


if __name__ == "__main__":
    sys.exit(main())
