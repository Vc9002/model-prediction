import sqlite3

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
cur = con.cursor()
print([r[1] for r in cur.execute("PRAGMA table_info(ledger_records)")])
for r in cur.execute(
    "select model_id, ledger_tier, status, market_type, count(*), min(created_at_utc), max(created_at_utc) from ledger_records where sport='ncaab' group by 1,2,3,4 order by 1,2,3,4"
):
    print(r)
