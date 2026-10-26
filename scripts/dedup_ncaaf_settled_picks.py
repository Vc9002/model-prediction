"""
Dedup NCAAF settled duplicate picks.

For each (event_id, market_type, selection, line, model_id) key that has
>1 settled row, keeps the LATEST row (by created_at_utc) and marks the
older rows 'superseded'. No rows are deleted.

This mirrors what commit b77a563d does for new re-forecasts going forward.

Safe to run multiple times (idempotent).

Usage:
    python scripts/dedup_ncaaf_settled_picks.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

RUNTIME_ROOT = Path("E:/model-prediction-runtime")
DB_PATH = RUNTIME_ROOT / "ledgers" / "ledgers.db"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report without changing anything")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    dups = conn.execute("""
        SELECT event_id, market_type, selection, line, model_id
        FROM ledger_records
        WHERE sport = 'ncaaf' AND status = 'settled'
        GROUP BY event_id, market_type, selection, line, model_id
        HAVING COUNT(*) > 1
    """).fetchall()

    print(f"Duplicate keys found: {len(dups)}")

    to_supersede: list[str] = []
    for event_id, market_type, selection, line, model_id in dups:
        picks = conn.execute(
            """
            SELECT pick_id, created_at_utc
            FROM ledger_records
            WHERE sport = 'ncaaf' AND status = 'settled'
              AND event_id = ? AND market_type = ? AND selection = ?
              AND (line = ? OR (line IS NULL AND ? IS NULL))
              AND model_id = ?
            ORDER BY created_at_utc ASC
        """,
            (event_id, market_type, selection, line, line, model_id),
        ).fetchall()

        # Keep latest, supersede the rest
        for pick_id, _ in picks[:-1]:
            to_supersede.append(pick_id)

    print(f"Rows to mark superseded: {len(to_supersede)}")

    if args.dry_run:
        print("[dry-run] No changes written.")
        conn.close()
        return

    for pick_id in to_supersede:
        conn.execute(
            "UPDATE ledger_records SET status = 'superseded' WHERE pick_id = ?",
            (pick_id,),
        )

    conn.commit()
    conn.close()
    print(f"Done. {len(to_supersede)} rows marked superseded.")

    # Verify
    conn2 = sqlite3.connect(str(DB_PATH))
    remaining = conn2.execute("""
        SELECT COUNT(*) FROM (
            SELECT event_id, market_type, selection, line, model_id
            FROM ledger_records
            WHERE sport = 'ncaaf' AND status = 'settled'
            GROUP BY event_id, market_type, selection, line, model_id
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    conn2.close()
    print(f"Remaining settled duplicate keys after dedup: {remaining}")


if __name__ == "__main__":
    main()
