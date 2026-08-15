# Burn-in / stabilization period (post-consolidation step 1)

Run the consolidated infrastructure unchanged for several days BEFORE any
model-math work. The system must prove it recovers from every listed
failure without duplicate rows or corrupted state. **Do not begin
promotion work until this passes.**

## Daily verification checklist

| Check | Command / where |
|---|---|
| production scheduler keeps firing | `python -m model_prediction.run_supervisor runs` — completed rows for `production` on cadence |
| shadow scheduler keeps firing | same, for `rebuild-shadow` |
| production.db advances | `/api/data/versions` → `parts.predictions.max_id` grows |
| shadow.db advances | rebuild dashboard (or `data/rebuild/shadow.db` mtime + row count) |
| dashboard stays fast | `curl -w "%{time_total}" http://localhost:8765/api/data/predictions?limit=100` < ~100ms; `/api/data/versions` near-instant |
| no duplicate predictions | store identity key + `counts_by`; re-fired cycles append `None` (see failure tests) |
| no dropped settlements | `cli settle --all-unsettled` exit 0 + open-row counts stable |
| no Git working-tree changes from normal runs | `git status --porcelain` — must show nothing new from canary/control-plane paths (they resolve to the runtime root) |
| health transitions honestly | `python -m model_prediction.system_health` — reasons list tells you WHY |

## Failure tests (run each at least once, record the outcome here)

Expected: every failure degrades or downs health, **recovers on the next
successful run, and never produces duplicate rows or corrupted state**.

- [ ] **Kill a worker mid-run**: start `run_supervisor run daily` in one
  terminal, `kill <pid>` it mid-flight; the run row must end `failed`
  with exit code; a second run must succeed and leave exactly one set of
  rows.
- [ ] **Restart dashboard**: `./stop && ./dash`; every `/api/data/*`
  endpoint answers; startup makes no external API calls.
- [ ] **Restart Mac**: after reboot, launchd jobs fire again (or
  `run_supervisor run <worker>` manually), `system_health` transitions
  back to HEALTHY/DEGRADED with truthful reasons, no duplicated runs.
- [ ] **Temporarily break a provider**: point `MODEL_PREDICTION_RUNTIME_ROOT`
  at an isolated copy, make ESPN unreachable (e.g. bogus proxy env), run
  the daily worker → run row `failed` with the provider error in the
  note; restore; next run succeeds and heals ingest.
- [ ] **Temporarily make a DB unavailable**: `chmod 000` the runtime
  root's `production/production.db`, run `cli_production predict` → the
  cycle completes fail-soft (store mirror only); restore; next cycle
  records normally. `system_health` reports the prediction gap truthfully.
- [ ] **Run the same job twice**: two consecutive
  `run_supervisor run daily` invocations → second is either `skipped`
  (lease held) or appends zero duplicates (identity keys + ledger
  idempotency); `verify-chain` stays 0 breaks.

## Automatable subset

`scripts/burn_in_checks.sh` runs the checks that don't need a reboot or
manual interruption (duplicate-run skip, double-append no-op, health
truthfulness, dashboard data endpoints + latency, clean working tree).

## Known-degraded waiver

```
KNOWN_DEGRADED:
soccer_odds_api

reason:
The Odds API returns 401 — no replacement credential available

accepted:
2026-08-14

blocks burn-in:
NO

blocks soccer provider completeness:
YES
```

Burn-in rule: **no NEW or unexplained DEGRADED/DOWN conditions.** The
overall status may remain DEGRADED for the waived soccer provider for
the whole period; every other check must be HEALTHY.

## Exit criterion

Every box above checked over ≥3 consecutive days, with the operator's
initials + date per line. Then — and only then — proceed to freeze the
trustworthy benchmark datasets (`mlb_v8_reference.parquet` first).
