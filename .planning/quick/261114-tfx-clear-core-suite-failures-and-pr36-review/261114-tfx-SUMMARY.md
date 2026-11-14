---
quick_id: 261114-tfx
status: complete
date: 2026-11-14
commit: d5c331e9
---

# Summary: core suite green again; PR 36 review applied

## What changed

- `6a687685` fix: 13 failures cleared. 12 were stale fixtures: NFL fixtures (structural-v5)
  are no longer the champion (v7) so the serving-registration gate downgraded them; Main caps
  edge at 0.08 and adds +0.02 on 47+ total overs; gated_ledger has meant Main since 2026-10-22
  (no NO_CALL rows); invalid-quote MLB calls stay in candidates and are never written; CFB v3
  has no serving class (v4 is the oldest that serves); `_DuplicateLedger`/`object()` fakes lacked
  `rows()` which the cross-market coherence pass reads; Flat absorbed every research-only sport.
  1 was real: `BasketballModel` widens margin_sd below 8 current-season games, the scalar replay
  did not, so every early-season NBA spread snapshot would fail `parameter_mismatch`.
  `_nba_volatility_features` now records `volatility_sample`; replay applies the same widening;
  old snapshots without the key replay unchanged. Tennis settled-ledger test moved to
  `tests/research/test_tennis_markov_ledger.py` and skipped with reason.
- `d5c331e9` fix (review): `cli/daily.py` fixed-name tasks (NCAAF, NBA spread/total, NFL
  spread/total) now honour `_daily_sport_disabled`; skipped sports record status `disabled`;
  `_skipped_sport_result` helper; dead defaults dropped. Scope test pins the flag (an in-scope
  sport may be off only with `season_active: false`). CI step "Research-tooling tests".
  CLAUDE.md Scope: esports/KBO/NPB need `esports-backfill` / `international-baseball-backfill`
  before re-enable because their 14-day incremental ratings refresh stops with the forecast block.

## Evidence

- `tests/test_cli.py`, `test_five_blind_spot_guards.py`, `test_forecast_runtime_regressions.py`,
  `test_tennis_markov.py`: 109 passed, 0 failed (was 13 failed).
- `tests/test_focus_sports_config.py`, `test_daily_integrity.py`, `test_scalar_artifact_replay.py`,
  `test_learned_replay.py`, `test_nfl_main_gate.py`, `test_feature_capture.py`: 49 passed.
- `ruff check src/ tests/` clean; CI yaml parses; `cli.daily` imports.

## Left alone

- `docs/handoffs/HANDOFF.md` carries another session's large uncommitted rewrite; its "Next
  action" (triage the 12 failures) is now done but the file was not touched here.
