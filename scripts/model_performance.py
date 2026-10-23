"""Per-model settled CALL performance from the runtime ledger (read-only).

Counts the `main` tier only: `flat`/`research`/`gated_research` mirror the same
picks by design (PK is pick_id+ledger_tier), so summing tiers double counts.
Usage: python -I scripts/model_performance.py [YYYY-MM-DD] [--sport soccer]
Day is Eastern (04:00Z..04:00Z during EDT).
"""

import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta

DB = "E:/MP-runtime/ledgers/ledgers.db"


def main(argv):
    sport = argv[argv.index("--sport") + 1] if "--sport" in argv else None
    pos = [a for a in argv if not a.startswith("--") and a != sport]
    day = date.fromisoformat(pos[0]) if pos else datetime.now(UTC).date()
    lo = f"{day}T04:00:00Z"
    hi = f"{day + timedelta(days=1)}T04:00:00Z"
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sql = """
    select model_id, count(*), sum(result='win'), sum(result='loss'), sum(result='push'),
           round(sum(pnl_units),2), round(sum(units),2)
    from ledger_records
    where ledger_tier='main' and decision='CALL' and pnl_units is not null
      and settled_at_utc >= ? and settled_at_utc < ? {sport}
    group by model_id order by 6 desc"""
    args = [lo, hi]
    clause = ""
    if sport:
        clause = "and sport = ?"
        args.append(sport)
    rows = c.execute(sql.format(sport=clause), args).fetchall()
    dupes = c.execute(
        """select count(*) from (select 1 from ledger_records where ledger_tier='main'
           and decision='CALL' and pnl_units is not null and settled_at_utc >= ? and settled_at_utc < ?
           group by model_id,event_id,market_type,selection,coalesce(line,'') having count(*)>1)""",
        (lo, hi),
    ).fetchone()[0]
    print(f"{day} ET  (main tier, settled CALLs)")
    print(f"{'model':32}{'n':>5}{'W':>5}{'L':>5}{'P':>4}{'pnl_u':>9}{'staked':>9}")
    for r in rows:
        print(f"{r[0]:32}{r[1]:>5}{r[2]:>5}{r[3]:>5}{r[4]:>4}{r[5]:>9}{r[6]:>9}")
    print(
        f"TOTAL n={sum(r[1] for r in rows)} pnl={round(sum(r[5] for r in rows), 2)} "
        f"staked={round(sum(r[6] for r in rows), 2)}  | in-tier duplicate groups: {dupes}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
