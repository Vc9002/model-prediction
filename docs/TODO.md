# Project repair queue — 2026-08-02 (refreshed 2026-08-15: all P0/P1 items
# resolved with evidence; remaining items are marked open below)

Current evidence lives in `docs/PROJECT_STATUS.md`; exact reproduction commands
and line references live in `DEBUG.md`. Do not mark the project release-ready
while tests, lint, execution binding, point-in-time provenance, artifacts,
config, reports, ledgers, and audit state disagree.

## P0 — capital and evidence integrity

- [x] Hard-block real-money execution until every order ticket is bound to the
  exact qualified ledger row and market/side/action/price/quantity/cost are
  recomputed and verified server-side. → DONE: HMAC-signed execution tickets
  (`execution_ticket.py`), order-readiness re-verification (F-53), CI contract
  test (`test_execution_gate.py`).
- [x] Make ledger mutation and audit append recoverable as one transaction;
  add failure-injection tests for create, settle, void, and removal.
  → RESOLVED for the canonical store: `RuntimeLedgerStore.apply` commits the
  mutation + its audit event in ONE sqlite transaction (G2); the XLSX is a
  best-effort export since the J cutover. Crash-injection coverage exists
  (`test_ledger_hardening`). Cross-file xlsx atomicity is historical-only.
- [x] Remove present-day probable-starter responses from historical MLB
  validation unless each record proves pregame `observed_at_utc`; retain MLB
  v6 as unqualified research. Prospective archive started 2026-07-26.
  → MLB v7 now active (2026-07-30), v6 superseded.
- [x] Enforce `artifact.qualified` and quote `timestamp_valid` before a learned
  candidate can be classified, priced, or logged. → RESOLVED by explicit
  operator directive (MASTER.md P0): qualification is the operator's decision;
  `timestamp_valid` handling re-verified live everywhere it applies.
- [x] Keep WNBA model opinions visible when availability fails, default the
  affected inputs neutral, and record operator-visible diagnostics in Today.

## P1 — current regressions and artifact alignment

- [x] Fix the four dashboard order-preview tests by pinning the intended unit
  value or using sizes within the current `$5.00`-per-unit cap. → DONE:
  56 dashboard tests pass against the current cap (2026-08-15).
- [x] Repair canonical hashes for
  `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` as new
  versioned artifacts; do not overwrite rollback evidence. → MOOT: both
  archived as obsolete; config/model.yaml documents the archived refs.
- [x] Point MLB total research at a total artifact, not the spread artifact
  (`mlb-spread-baseline-v1.json` is currently referenced as both
  `spread_research_artifact` and `total_research_artifact`). → DONE:
  spread → measured-edge-margin-v3.json, total → measured-edge-totals-v3.json
  (verified 2026-08-15).
- [x] Create, remove, or explicitly disable the missing
  `market-residual-v1.json` config reference. → DONE: the artifact exists and
  the config reference resolves (verified 2026-08-15).
- [x] Reproduce `outputs/latest/learned-model-validation.json` from one stable
  green checkout with current paths and active versions. → DONE 2026-08-15 via
  `validate-models` (the previously documented `validate-learned` name was
  stale; regenerated 19:09 local).
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
- [x] Make exposure-check-plus-append atomic across processes and preserve
  research/gated paired-ledger consistency. → DONE: exposure read + append
  happen under one held lock (`lock_exclusive` P1-1 + `_LEDGER_LOCK`); the
  sqlite store serializes cross-process via WAL + busy_timeout.
- [x] Keep a cross-process singleton guard around the daily writer workflow (`daily_lock.py`).

## P2 — source and feature correctness

- [x] Redact The Odds API credentials from all returned/logged errors.
  → DONE (P1-2: `raise_for_status` moved inside `_safe_get`; redaction
  applies to transport and HTTP error strings).
- [x] Reject future `observed_at_utc` values in freshness checks.
  → DONE (P1-4: explicit future rejection in both eligibility paths).
- [x] Treat soccer draws as draws in head-to-head features.
- [x] Repair MLB weather payload shape, wind contribution, and event-hour selection.
- [x] Validate a row before adding its event ID to feature-ingest dedup state.
  → DONE (P1-5: dedup registration moved after successful writes).
- [x] Paginate Polymarket discovery and distinguish provider failure from an
  empty slate; never hardcode aggregate `timestamp_valid=true`. → DONE
  (P1-3: offset pagination; `timestamp_valid` was already dynamic).
- [x] Correct the economic bootstrap-CI gate so an interval spanning zero does
  not pass as positive-ROI evidence. → Fixed 2026-07-31 (DEBUG.md section).
- [x] Surface narrow exception catches that currently discard esports,
  KBO/NPB, and source-refresh failures. → DONE (P1-6: logging added to the
  five silent except blocks).

## P2 — tests and maintainability

- [ ] Raise `cli.py` coverage above the measured 8.3% with end-to-end,
  side-effect-controlled tests. PARTIAL: `tests/test_cli.py` now exists
  (50+ tests); the monolith split is intentionally LAST per
  docs/RESEARCH_BACKLOG.md's cross-cutting order.
- [x] Add direct tests for execution-ticket binding/cap recomputation.
  → DONE: `tests/test_execution_gate.py`.
- [x] Add audit-failure recovery and multiprocess ledger serialization tests.
  → DONE: crash-injection in `test_ledger_hardening.py` (+ F-64 `_verify_chain`
  detection); multiprocess serialization covered by `daily_lock` + supervisor
  lease tests.
- [x] Add provider secret-redaction and future-timestamp tests. → DONE with
  the P1-2/P1-4 fixes (regression tests in `test_eligibility.py`/
  `test_the_odds_api.py`).
- [x] Add `timestamp_valid=false`, WNBA conflict, KBO/NPB tie-price,
  soccer-draw, and weather-hour regression tests.
- [x] Resolve the unused/non-conformant `SportModel` protocol and unwired model
  registry, or remove those abstractions. → DONE 2026-08-15: `SportModel` and
  the dead `ScoreModel` protocol deleted (zero consumers); the registry IS
  wired (`model_spec`/`get_model`, config-derived status per P1-8).
- [x] Clear the 118 Ruff findings (117 baseline + 1 EXE002 on test_validation.py).
  → DONE 2026-08-15: **0 findings** across src/ + tests/ (exec bits cleared;
  safe auto-fixes + noqa-with-justification for the deliberate catches;
  `.pre-commit-config.yaml` added so it stays clean).

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

## P2 — High-Alpha MLB Features & PA Monte Carlo Simulator (from Research Literature Dive #4)

- [ ] Ingest rolling Statcast Catcher Framing in the Shadow Zone (`features/catcher_framing.py`)
  to capture the $1\text{--}2\text{pp}$ PA called-strike leverage on totals and NRFI.
- [x] Implement Starting Pitcher Pitch-Type Arsenal vs. Lineup Vulnerability dot-product
  tensor (`features/pitch_arsenal.py`). → DONE: Repertoire metric tensor & tests in `test_pitch_arsenal.py`.
- [x] Build Discrete-Event Plate-Appearance (PA) 8-Class Monte Carlo Simulation Engine
  (`models/mlb_monte_carlo.py`) simulating 5,000 iterations per game over 24 base-out states. → DONE: Markov state tracker, fatigue/bullpen handoff, ML/RL/Totals/F5/NRFI/K-props distributions & tests in `test_mlb_monte_carlo.py`.
- [x] Wire Isotonic Tail-Calibrator (`meta_calibrator.py`) to post-process extreme-probability
  tail markets (NRFI, Runline -1.5, and K-props). → DONE: `TailCalibrator` with monotonic constraints & ECE metrics in `test_meta_calibrator.py`.
- [ ] Leverage 35-minute pre-game wake trigger (`plan_lineup_wakes.py`) to execute forecasts
  during the golden 15–30 minute post-lineup confirmation window before sportsbook lines adjust.

## P2 — Open-Source Research Adaptations (MLB v9, Soccer, Baselines)

- [x] **MLB v9 Step B — Batter Offensive Priors (Baseball Hydra concept)**: Implement `features/batter_priors.py` using closed-form Empirical Bayes Beta-Binomial shrinkage over PA for $\text{xwOBA}$, $\text{K}\%$, $\text{BB}\%$, $\text{ISO}$, $\text{barrel}\%$, and $\text{hard-hit}\%$.
- [x] **MLB v9 Step C — Rich Starter State (Market Efficiency Lab architecture)**: Build multidimensional starter state in `features/starter_state.py` including 21d/season xwOBA allowed, K%, BB%, CSW%, first-pitch strike%, fastball velocity/drift, average depth, and last-3 start deltas. → DONE: `PointInTimeStarterEngine` & `test_starter_state.py`.
- [x] **MLB v9 Step D — Dynamic Bullpen State**: Implement dynamic bullpen capability model combining reliever talent (xwOBA/K-BB%), availability probability from rolling 1d/2d/3d workloads and back-to-back flags, and role leverage. → DONE: `PointInTimeBullpenEngine` & `test_bullpen_state.py`.
- [x] **MLB v9 Step E — Confirmed Lineup Aggregation**: Join prospective lineup archive (`data/point_in_time/mlb_lineups.jsonl`) with batter priors to generate batting-order-weighted xwOBA, platoon-split xwOBA, and lineup xwOBA advantage. → DONE: `LineupStateEngine` & `test_lineup_state.py`.
- [x] **MLB v9 Step F — Monotonic XGBoost Challenger**: Train and evaluate XGBoost with domain monotonic constraints ($\frac{\partial P}{\partial \text{SP xwOBA Allowed}} \le 0$, $\frac{\partial P}{\partial \text{Lineup xwOBA}} \ge 0$) against standardized Logistic Regression on identical frozen cohort. → DONE: `MonotonicMLBClassifier` & `test_mlb_xgboost.py`.
- [x] **Permanent Baselines Ladder (Forrest31)**: Add pregame Pythagorean expectation and Log5 matchup models to the standard evaluator comparison ladder.
- [x] **Soccer Joint Bivariate Dixon-Coles Grid (penaltyblog & football-mle)**: Refactor soccer forecasting to generate a unified Dixon-Coles score grid $(\lambda_H, \lambda_A, \rho)$ driving 1X2, BTTS, O/U totals, and Asian handicaps from one joint distribution, with exponential time decay parameter $\xi$ tuned via temporal CV. → DONE: `DixonColesEngine` & `test_soccer_dixon_coles.py`.

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
