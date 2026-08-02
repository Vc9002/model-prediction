# Project repair queue — 2026-08-02

Current evidence lives in `docs/PROJECT_STATUS.md`; exact reproduction commands
and line references live in `DEBUG.md`. Do not mark the project release-ready
while tests, lint, execution binding, point-in-time provenance, artifacts,
config, reports, ledgers, and audit state disagree.

## P0 — capital and evidence integrity

- [ ] Hard-block real-money execution until every order ticket is bound to the
  exact qualified ledger row and market/side/action/price/quantity/cost are
  recomputed and verified server-side.
- [ ] Make ledger mutation and audit append recoverable as one transaction;
  add failure-injection tests for create, settle, void, and removal.
- [x] Remove present-day probable-starter responses from historical MLB
  validation unless each record proves pregame `observed_at_utc`; retain MLB
  v6 as unqualified research. Prospective archive started 2026-07-26.
  → MLB v7 now active (2026-07-30), v6 superseded.
- [ ] Enforce `artifact.qualified` and quote `timestamp_valid` before a learned
  candidate can be classified, priced, or logged.
- [x] Keep WNBA model opinions visible when availability fails, default the
  affected inputs neutral, and record operator-visible diagnostics in Today.

## P1 — current regressions and artifact alignment

- [ ] Fix the four dashboard order-preview tests by pinning the intended unit
  value or using sizes within the current `$5.00`-per-unit cap.
- [ ] Repair canonical hashes for
  `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` as new
  versioned artifacts; do not overwrite rollback evidence.
- [ ] Point MLB total research at a total artifact, not the spread artifact
  (`mlb-spread-baseline-v1.json` is currently referenced as both
  `spread_research_artifact` and `total_research_artifact`).
- [ ] Create, remove, or explicitly disable the missing
  `market-residual-v1.json` config reference.
- [ ] Reproduce `outputs/latest/learned-model-validation.json` from one stable
  green checkout with current paths and active versions.
- [x] Reconcile CLI registry status with config/artifact status for Soccer,
  esports, KBO, and NPB. → Soccer is now shadow_qualified (operator override,
  2026-08-02); esports are v5 with proper validation; KBO/NPB are documented.

## P1 — routing, settlement, and concurrency

- [x] Make KBO/NPB forecast preview work without a ledger or writes.
- [x] Log exact-input KBO/NPB research decisions before gated-subset filtering.
- [x] Grade KBO/NPB `$0.50` tie settlement using entry price.
- [x] Include flat/research/gated settlement results and failures in `settle`/`daily`.
- [x] Keep flat isolated to MLB/NBA/WNBA/NFL; route soccer/esports/KBO/NPB to research + gated.
- [x] Split Research and Gated Research into one workbook per sport.
- [x] Enforce model-input, executable-edge, confidence, and Research-pair invariants for Gated.
- [x] Audit and archive legacy mixed Research/Gated workbooks.
- [x] Run daily forecasts in one process with shared learned-slate caches.
- [ ] Make exposure-check-plus-append atomic across processes and preserve
  research/gated paired-ledger consistency.
- [x] Keep a cross-process singleton guard around the daily writer workflow (`daily_lock.py`).

## P2 — source and feature correctness

- [ ] Redact The Odds API credentials from all returned/logged errors.
- [ ] Reject future `observed_at_utc` values in freshness checks.
- [x] Treat soccer draws as draws in head-to-head features.
- [x] Repair MLB weather payload shape, wind contribution, and event-hour selection.
- [ ] Validate a row before adding its event ID to feature-ingest dedup state.
- [ ] Paginate Polymarket discovery and distinguish provider failure from an
  empty slate; never hardcode aggregate `timestamp_valid=true`.
- [x] Correct the economic bootstrap-CI gate so an interval spanning zero does
  not pass as positive-ROI evidence. → Fixed 2026-07-31 (DEBUG.md section).
- [ ] Surface narrow exception catches that currently discard esports,
  KBO/NPB, and source-refresh failures.

## P2 — tests and maintainability

- [ ] Raise `cli.py` coverage above the measured 8.3% with end-to-end,
  side-effect-controlled tests. Now 3,943 lines; still zero dedicated test file.
- [ ] Add direct tests for execution-ticket binding/cap recomputation.
- [ ] Add audit-failure recovery and multiprocess ledger serialization tests.
- [ ] Add provider secret-redaction and future-timestamp tests.
- [x] Add `timestamp_valid=false`, WNBA conflict, KBO/NPB tie-price,
  soccer-draw, and weather-hour regression tests.
- [ ] Resolve the unused/non-conformant `SportModel` protocol and unwired model
  registry, or remove those abstractions.
- [ ] Clear the 118 Ruff findings (117 baseline + 1 EXE002 on test_validation.py).
  The 79 EXE002 findings are test files with `chmod +x` and no shebang — widespread
  but low-risk.

## P3 — evidence quality

- [x] Continue prospective executable BBO and closing-snapshot capture.
  → Active across 8 sports in `data/odds/` (2026-08-02).
- [x] Add decision-time starters, lineups, bullpen usage, weather, and
  availability records with observed/effective timestamps.
  → MLB player availability (`features/mlb_player_availability.py`) and
  pitching-staff availability now capture prospectively.
- [ ] Keep spread, total, F5, YRFI/NRFI, and research-league economics
  non-promotable until exact historical contract lines and timestamp-valid
  inputs exist.
- [ ] Report model quality, calibration, CLV, and executable net profitability
  as separate claims.

## Verified scan record

2026-07-26 dirty checkout at `697d765`: 410 tests passed and 4 failed; 117 Ruff
findings; 31 of 33 artifact hashes valid; 16,387 audit events with zero chain
breaks.

**2026-08-02** current checkout: **624 tests passed, 0 failed**; **118 Ruff findings**
(79 EXE002 shebang, 12 FURB162, remainder various); **43,304 audit events** with
zero chain breaks; CLI and ModelLedger import cleanly; `.venv/bin/model-prediction`
entry point works; 82 dirty files (session work uncommitted).

## Historical rollback

Use Git history and versioned artifacts deliberately. Do not run broad checkout,
reset, deletion, or artifact overwrite operations in this dirty working tree.
