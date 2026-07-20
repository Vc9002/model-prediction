# Changelog

## 2026-07-20 — esports/KBO/NPB unit tracking, confidence-gate fix, BBO collection

- Fixed a no-op confidence-gate selector in `esports.py`: threshold selection
  picked whichever gate had the most observations, which always resolved to
  the loosest threshold (0.0) and never actually gated anything. Now selects
  by the diagnostic `units_at_minus_110` result on validation, verified to
  hold on the untouched locked test. Commit `51899ea`.
- Added `units_at_minus_110` reporting to `_metrics()` in both `esports.py`
  and `international_baseball.py` (KBO/NPB), so validation/holdout
  profitability is visible directly. KBO/NPB treat ties as a push (0 P&L),
  not a loss.
- Investigated why LoL/CS2 diagnostic numbers looked inflated: partly the
  gate no-op above, and more fundamentally real esports contract lines are
  skewed (70/30, 60/40) rather than flat `-110`, so the flat-stake diagnostic
  overstates edge. KBO/NPB lines are comparatively even, so the diagnostic is
  a closer (but still not executable) proxy there.
- Started real per-side moneyline BBO capture for esports, KBO, and NPB
  (`BBO_CAPTURE_SPORTS` in `data_sources/polymarket_us.py` now covers all
  7 sports: mlb, nba, wnba, nfl, esports, kbo, npb). Snapshots land in
  `data/odds/<sport>/<date>/`.
- Reviewed a concurrent roadmap-challenger factorial experiment
  (`src/model_prediction/roadmap_challenger.py`,
  `outputs/roadmap_challenger/ROADMAP_CHALLENGER_DECISION_DOSSIER.md`): 0 of
  64 tested feature combinations across MLB/NBA/WNBA/NFL clear the full
  statistical screen. `schedule_available` is structurally degenerate
  (near-constant). No production model or config was changed by that
  experiment.

## 2026-07-20 — documentation truth reset

- Added `docs/PROJECT_STATUS.md` as the current source-of-truth entry point.
- Removed stale production tables and replaced them with verified report-level status plus explicit release blockers.
- Recorded the current test, Ruff, artifact-hash, audit-chain, packaging, and working-tree results without claiming a clean release. The suite moved from one failure to green during review because another writer changed the MLB expectation; the artifact inconsistency remains documented.
- Standardized examples on `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli` while the installed console entry point is broken.
- Reframed model accuracy, diagnostic `-110` units, and executable profitability as separate claims.
- Documented the real-money CLI/dashboard surfaces and retained shadow/zero-unit defaults.
- Replaced the old final-status TODO with an integrity-first repair queue.

## 2026-07-17 (later — bug sweep + hardening)

- Fixed audit hash serialization: wrote event hashes with compact `separators=(",", ":")` matching computation, so chain is verifiable from JSONL alone.
- Fixed Polymarket executor: `token_id` now resolved from market slug via `client.get_token_id()` instead of passing the slug directly.
- Fixed empty `observed_at_utc=""` crashing `parse_utc()` in eligibility staleness check — added `.strip()` guard.
- Fixed `bans.py` `_entries()` catching `EntityResolutionError` so a stale ban config entry no longer blocks the entire CLI.
- Fixed `FixedPlattCalibrator.transform()` accepting `[0, 1]` boundary values (was `(0, 1)` strict, now auto-clips like `TrainablePlattCalibrator`).
- Fixed config drift: `maximum_data_age_hours` and `maximum_unreviewed_disagreement` now flow from `config/model.yaml` through the forecast path to `evaluate_eligibility`.
- Fixed float comparison in `evaluate_locked_holdout`: added `1e-12` epsilon so exactly-60% models don't fail on rounding.
- Fixed `normalize_no_vig` rejecting probabilities `>= 1` (was only checking `<= 0`).
- Fixed CLI dispatch fallthrough: replaced `else:` with explicit `elif args.command == "review-loss":` and final `else: raise ValueError(...)`.
- Added 256-entry LRU cap to `ESPNClient` and `ESPNMLBClient` `_get` response caches.
- Added `handle.flush()` to `MarketOddsSnapshotStore.append()` for crash resilience.
- Added Polymarket US odds summary to `daily` command output (`bbo_capture` metadata + per-sport snapshot counts).
- Fixed 4 stale docstrings in model files (nba/wnba/nfl/basketball "research state" → shadow-qualified).
- Fixed stale event count in TODO.md scan record (852 → 865).
- Ledger cleared and re-initialized; backup preserved at `data/.backup_2026-07-17/`.
- Removed redundant `input/INPUT.md` (content now in `input/TODO.md` + `input/PROMPT.md`).
- Updated root `README.md` with qualified models table and accurate daily-flow description.
- 120/120 tests pass, ruff clean.

## 2026-07-17

- Corrected monthly qualification: complete months with 10 or more called picks are binding; complete months below 10 calls are insufficient; an incomplete terminal month is provisional.
- Added 9-call, 10-call, losing-complete-month, and incomplete-terminal-month regression tests.
- Generated and activated hash-verified v2 artifacts for MLB, NBA, WNBA, and NFL while preserving all v1 files.
- Requalified WNBA at 65.98%, 97 calls, +25.18U. July 2026 has 27 calls and is provisional because the month is incomplete—not because it is below 10.
- Qualified NFL at the reproducible 60.55%, 109 calls, +17.00U. February has 2 calls and is non-binding.
- Tested a learned trailing-30-day adaptive MLB home-field feature. It fell from 60.87%/92/+14.91U to 60.42%/96/+14.73U and was rejected.
- Proved confidence-gap gating is exactly `2 * max_probability - 1`; it cannot change ordering or create away selections at an equivalent threshold and was rejected as redundant.
- Audited MLB pitcher data: 4,325 of 4,785 raw events contain both starters and ERA, but retrospective caches are not point-in-time valid. Pitcher training was blocked to prevent leakage.
- Added explicit multi-market readiness reporting. NBA/WNBA spreads and totals lack contract lines; MLB reconstructed full-game lines are timestamp-invalid; F5 and YRFI/NRFI lack required point-in-time inputs.
- Corrected DEBUG checks for canonical audit-chain hash, active nested artifact schema, artifact-derived thresholds, intentional rollback references, and Eastern-time MLB season filtering.
- Wrote the final evidence report to `outputs/latest/learned-model-validation-v2.json`.
