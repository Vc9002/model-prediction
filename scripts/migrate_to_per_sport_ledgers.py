"""One-time migration: split data/picks.xlsx and data/flat_picks.xlsx (single
files holding every sport, distinguished only by a `league` column) into
per-sport files under data/main/<sport>.xlsx and data/flat/<sport>.xlsx.

Read-only against the source files; writes only the new per-sport files.
Rows for sports outside MAIN_LEDGER_SPORTS (esports/KBO/NPB/NBA/NFL) in the
old flat_picks.xlsx are reported but NOT copied into data/flat/ -- per
operator directive, Flat is now Main's paired companion only for sports that
actually reach Main. Their full record already lives independently in
Research (esports/KBO/NPB) or nowhere (NBA/NFL, which had zero rows at
migration time) -- nothing here deletes the OLD single files; that is a
separate, explicit step after this script's output is verified.

Usage: PYTHONPATH=src .venv/bin/python3 scripts/migrate_to_per_sport_ledgers.py <data_root>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.main_ledgers import MAIN_LEDGER_SPORTS, flat_ledger_path, main_ledger_path
from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic


def _split(source: Path, resolver, data_root: Path, *, restrict_to_main_sports: bool) -> dict[str, int]:
    if not source.exists():
        print(f"  {source}: does not exist, skipping")
        return {}
    headers, rows = read_xlsx_rows(source)
    by_league: dict[str, list[dict[str, str]]] = {}
    dropped = 0
    for row in rows:
        league = str(row.get("league", "")).casefold()
        if restrict_to_main_sports and league not in MAIN_LEDGER_SPORTS:
            dropped += 1
            continue
        by_league.setdefault(league, []).append(row)
    counts: dict[str, int] = {}
    for league, league_rows in sorted(by_league.items()):
        if league not in MAIN_LEDGER_SPORTS:
            print(f"  WARNING: unexpected league {league!r} with {len(league_rows)} rows -- not migrated")
            continue
        dest = resolver(data_root, league)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_xlsx_rows_atomic(dest, headers, league_rows)
        counts[league] = len(league_rows)
    if dropped:
        print(f"  dropped {dropped} rows outside {MAIN_LEDGER_SPORTS} (not copied to new Flat structure)")
    return counts


def main() -> None:
    data_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    print(f"Migrating ledgers under {data_root} ...")
    print("Main (data/picks.xlsx -> data/main/<sport>.xlsx):")
    main_counts = _split(data_root / "picks.xlsx", main_ledger_path, data_root, restrict_to_main_sports=True)
    for sport, count in main_counts.items():
        print(f"  {sport}: {count} rows")
    print("Flat (data/flat_picks.xlsx -> data/flat/<sport>.xlsx, MAIN_LEDGER_SPORTS only):")
    flat_counts = _split(
        data_root / "flat_picks.xlsx", flat_ledger_path, data_root, restrict_to_main_sports=True
    )
    for sport, count in flat_counts.items():
        print(f"  {sport}: {count} rows")
    print("Done. Old data/picks.xlsx and data/flat_picks.xlsx were NOT modified or deleted.")


if __name__ == "__main__":
    main()
