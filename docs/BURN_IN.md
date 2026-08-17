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
| 2026-08-16 | PASS (day 1) | No repo DBs; supervisor runs completed except ONE transient rebuild-shadow failure (23:15 UTC, exit 1, zero output — undiagnosable; 3 subsequent runs green; startup echo added to run_rebuild.sh so silent failures can't recur); dashboard lsof=pidfile=launchctl (95673); job history serves; tree clean; main CI green |
| 2026-08-17 | PASS (day 2) | No repo DBs; supervisor runs all completed (daily ×10, production ×11, rebuild-shadow ×10 since cutover; the one failed row remains the documented 08-15 23:15 UTC transient, zero repeats); 0 skipped rows (no lock refusals occurred to coalesce); dashboard lsof=pidfile=launchctl (76750) after a kickstart-restart this session; pre-restart job history (daily-1786732799, 08-14) serves via /api/job; tree clean (session work committed as 287d979→7efdd5f); evidence API all_model_definitions_and_backfills_valid=True |
| 2026-08-17 | PASS (day 3 — final acceptance) | Full final suite re-run live: checks 1–7 all pass (no repo DBs; single runtime root; daily ×11, production ×12, rebuild-shadow ×11 completed across the window — only the documented 08-15 transient failed, zero repeats; 0 skipped; dashboard lsof=pidfile=launchctl (76750); pre-restart job history serves; tree clean at 4b16089). `PRAGMA integrity_check` ok on runs.db and ledgers.db. Evidence API `all_model_definitions_and_backfills_valid=True`. system_health DEGRADED verdict fully explained: (a) soccer capture stale — the documented accepted external credential issue; (b) last production prediction 606 min — the machine slept 03:00–13:00 local (pmset log evidence; launchd StartInterval jobs don't fire during sleep) plus overnight quiet hours (no games); a health-threshold calibration note, not an infrastructure defect. |

## Final acceptance

**2026-08-17 (operator directive): `INFRASTRUCTURE_CONSOLIDATION = ACCEPTED`.**

Three consecutive clean days recorded (08-15/16/17). Duration caveat
recorded honestly: acceptance occurred at ~48h elapsed of the nominal
≥72h window (gate would have opened 2026-08-18 05:25 UTC); the
operator accepted early completion on the strength of three consecutive
clean days with every anomaly classified. Infrastructure work stops
unless a real defect appears — the research sequence starts per
`docs/POST_BURNIN_PROMPT.md`: MLB v8 row-parity reproduction first
(frozen benchmark), then v9 ablations.

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
