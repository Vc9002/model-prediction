# Burn-in window — consolidation freeze (O)

**Start**: 2026-08-15 05:25 UTC (merged SHA `37be479`, tag
`consolidation-2026-08-15`; local checkout and launchd jobs run this
code).
**Duration**: ≥ 3 days, i.e. through 2026-08-18.

## Burn-in checks (review contract)

Run each at least once per day during the window; record results here.

1. **No repo-local DBs appear** —
   `find data -name "*.db"` returns nothing (runtime root is canonical).
2. **No second runtime root appears** — all writes land under
   `/Users/vincentc9002/model-prediction-runtime/`; repo `data/` gains no
   new `*.db`, `runs.db`, `ledgers/`, or `production/` dirs.
3. **No duplicate daily cycles** —
   `sqlite3 /Users/vincentc9002/model-prediction-runtime/runs.db
   "SELECT status, count(*) FROM runs WHERE worker='daily' GROUP BY status"`
   shows no `failed` rows with lease notes; at most one daily row per
   scheduled window (skips recorded as `skipped`, not failures).
4. **No lock-refusal caused by competing schedulers** — dashboard-triggered
   daily during a scheduled run records `skipped` (exit 75), never
   `failed`.
5. **Exactly one dashboard PID survives restart** —
   `lsof -nP -iTCP:8765 -sTCP:LISTEN`, `cat dashboard/server.pid`, and
   `launchctl print gui/$(id -u)/com.vc.model-dashboard` all agree after
   a `launchctl kickstart -k`.
6. **Job history survives dashboard restart** — after restart,
   `/api/job?id=<pre-restart job>` still returns the record.
7. **Git tree remains clean** — `git status --porcelain` is empty after
   each scheduled production + daily cycle.

## Operational checks carried over from K

- Rolling artifacts in sync with live data:
  `python -m model_prediction.ledger_parity`-style check or the dashboard
  production-evidence page: `all_model_definitions_and_backfills_valid`
  must be true (the evidence API compares the validation reports against
  the runtime-root rolling artifacts).
- Soccer Odds API remains a documented known-DEGRADED external
  dependency — it does not block infrastructure burn-in.

## Results

| Date | Checks 1-7 | Notes |
|---|---|---|
| 2026-08-15 | PASS (day 0) | No repo DBs; supervisor runs all completed (daily ×4, production ×4, rebuild ×3 since cutover, incl. launchd-scheduled cycles on the new code); dashboard listener = server.pid = launchctl pid after kickstart; pre-restart job history serves via /api/job; tree clean after cycles |
| 2026-08-16 | PASS (day 1) | No repo DBs; runtime root intact; supervisor runs all completed (daily ×2, production ×2, rebuild ×2 since noon UTC); dashboard lsof=pidfile=launchctl (95673); job history serves; tree clean; main CI green @0e7c1cd |
| 2026-08-17 | | |
| 2026-08-18 | | |

Burn-in passes when three consecutive clean days are recorded, after
which the research sequence starts: MLB v8 reproduction first (frozen
benchmark, exact reproduction gate), then MLB v9 ablations.

## Mid-burn-in defect fixed (2026-08-15, day 0)

The burn-in caught a real defect: after the sqlite-ledger-authority
cutover, the WNBA threaded forecast logged **0 rows** (212
"SQLite objects created in a thread can only be used in that same
thread" errors in the 08-14 daily log; 0 occurrences on 08-13).
`RuntimeLedgerStore` shared one sqlite3 connection across the
ThreadPoolExecutor forecast workers. Fixed with per-thread connections
(+ regression test, revert-verified). WNBA rows for 08-14/15 are a
documented data gap — not backfilled. MLB's zero open rows the same
days were investigated and are design-consistent (below-confidence
research candidates + overnight unmatched quotes), not a defect.
