---
quick_id: 261114-reg
status: complete
date: 2026-11-14
commit: 0face48b
---

# Summary: registry matches scope; 53 orphaned scripts gone; registry-adjacent tests green

## What changed

- `b34de4b5` refactor: `config/production.yaml` -- 40 entries for soccer, tennis, LoL, CS2,
  Dota2, Valorant, Rainbow Six, KBO, NPB, hockey, darts, Euroleague, NBL set `enabled: false`;
  their `champions:` and `challengers:` routes removed; scope comment under `models:`.
  `model_lifecycle.SUPPORTED_MARKETS` lists the six production sports. Registry: 89 entries,
  49 available, 40 disabled-by-scope, every contract still resolves (`load_error None`).
  53 scripts deleted (git-tracked, referenced nowhere; out-of-scope or one-off research
  runners). Catalog descriptors added for nine registry models that had none
  (`sports/naming.py`). Stale pins fixed in `test_production_registry`, `test_model_lifecycle`,
  `test_ci_contracts`, `test_audit_fixes_regression`, `test_soccer_v3`.
- `0face48b` test: the NEUTRAL-resolution settlement test now injects an unresolved market
  client; it had been building a real `PolymarketUSClient` and grading against the live,
  since-resolved market (network-dependent failure at HEAD).

## Evidence

- Registry load: entries 89, available 49, problems 40, primary `wnba-elo-trend-lr-v4`,
  NBA moneyline champion `nba-elo-trend-lr-v5`, TENNIS moneyline champion `None`.
- Targeted runs: `test_production_registry`, `test_production_canary`, `test_model_lifecycle`,
  `test_model_promotion`, `test_system_health`, `test_ci_contracts`, `test_model_naming`,
  `test_soccer_v3`, `test_focus_sports_config`, `test_daily_integrity`,
  `test_audit_fixes_regression`, `test_audit_remediation_batch3`, `test_ncaab_main_totals`,
  `test_forecast_runtime_regressions`: 143 passed, 0 failed.
- Five of those failures pre-dated this task; confirmed by running them against a
  `git archive HEAD` export (all five failed there too).
- `python -m compileall -q scripts` (the CI step) clean; `ruff check src/ tests/` clean;
  default collection 2771 tests, 0 errors.
- Open out-of-scope picks at the time of the change: 3 Euroleague + 1 hockey Flat rows from
  11-12/11-13, still settled by the registry-free settlement path.

## Left alone

- `scratch/` (211 MB, gitignored, ~250 local one-off scripts plus a full copy of the
  incumbent `src/`) was not touched: it is untracked operator scratch, not repo state.
- `docs/handoffs/HANDOFF.md` still carries another session's uncommitted rewrite.
- 15 docs named for out-of-scope sports are already under `docs/archive/` except
  `docs/ESPORTS_OVERHAUL_2026-10.md` and `docs/SOCCER_V3_CORRECTION_2026-10-19.md`; left in
  place (they are dated evidence, not working rules).
