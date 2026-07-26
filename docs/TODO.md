# Project repair queue — 2026-07-26

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
- [ ] Point MLB total research at the total artifact, not the spread artifact.
- [ ] Create, remove, or explicitly disable the missing
  `market-residual-v1.json` config reference.
- [ ] Reproduce `outputs/latest/learned-model-validation.json` from one stable
  green checkout with current paths and active versions.
- [ ] Reconcile CLI registry status with config/artifact status for Soccer,
  esports, KBO, and NPB.

## P1 — routing, settlement, and concurrency

- [x] Make KBO/NPB forecast preview work without a ledger or writes.
- [x] Log all intended KBO/NPB research rows before gated-subset filtering.
- [x] Grade KBO/NPB `$0.50` tie settlement using entry price, not ordinary
  moneyline push economics.
- [x] Include flat/research/gated settlement results and failures in `settle`
  and `daily` output.
- [x] Keep flat isolated to MLB/NBA/WNBA/NFL; route soccer, esports, KBO, and
  NPB only to research plus the valid gated subset.
- [x] Run daily forecasts in one process, share learned-slate caches, overlap
  independent captures, and propagate stage failures.
- [ ] Make exposure-check-plus-append atomic across processes and preserve
  research/gated paired-ledger consistency.
- [ ] Keep the cross-process singleton guard for all daily/forecast writers.

## P2 — source and feature correctness

- [ ] Redact The Odds API credentials from all returned/logged errors.
- [ ] Reject future `observed_at_utc` values in freshness checks.
- [x] Treat soccer draws as draws in head-to-head features.
- [x] Repair MLB weather payload shape, wind contribution, and event-hour
  selection.
- [ ] Validate a row before adding its event ID to feature-ingest dedup state.
- [ ] Paginate Polymarket discovery and distinguish provider failure from an
  empty slate; never hardcode aggregate `timestamp_valid=true`.
- [ ] Correct the economic bootstrap-CI gate so an interval spanning zero does
  not pass as positive-ROI evidence.
- [ ] Surface narrow exception catches that currently discard esports,
  KBO/NPB, and source-refresh failures.

## P2 — tests and maintainability

- [ ] Raise `cli.py` coverage above the measured 8.3% with end-to-end,
  side-effect-controlled tests.
- [ ] Add direct tests for execution-ticket binding/cap recomputation.
- [ ] Add audit-failure recovery and multiprocess ledger serialization tests.
- [ ] Add provider secret-redaction and future-timestamp tests.
- [x] Add `timestamp_valid=false`, WNBA conflict, KBO/NPB tie-price,
  soccer-draw, and weather-hour regression tests.
- [ ] Resolve the unused/non-conformant `SportModel` protocol and unwired model
  registry, or remove those abstractions.
- [ ] Clear the 117 Ruff findings, separating executable-bit metadata from
  semantic exception/control-flow issues.

## P3 — evidence quality

- [ ] Continue prospective executable BBO and closing-snapshot capture.
- [ ] Add decision-time starters, lineups, bullpen usage, weather, and
  availability records with observed/effective timestamps.
- [ ] Keep spread, total, F5, YRFI/NRFI, and research-league economics
  non-promotable until exact historical contract lines and timestamp-valid
  inputs exist.
- [ ] Report model quality, calibration, CLV, and executable net profitability
  as separate claims.

## Verified scan record

2026-07-26 dirty checkout at `697d765`: 410 tests passed and 4 failed; 117 Ruff
findings; 31 of 33 artifact hashes valid; 16,387 audit events with zero chain
breaks/hash mismatches; ledger/audit reconciliation false because 1,150
historical creation events lack audited removals; console entry point and
critical imports pass.

## Historical rollback

Use Git history and versioned artifacts deliberately. Do not run broad checkout,
reset, deletion, or artifact overwrite operations in this dirty working tree.
