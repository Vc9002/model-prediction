---
phase: quick-261125-1bw
plan: 01
subsystem: paper-sizing
tags: [units, ledger, edge-scaling, moneyline, ncaaf]
requires: []
provides:
  - moneyline and NCAAF-Over rows sized by edge (1.0U-2.0U) on Flat, Main (qualified and unqualified) and Research/Gated
affects: [ledger.append_evaluated, units.edge_scaled_units, eligibility sizing, auto-buyer stake, dashboard/cli real-money per-order cap]
key-files:
  modified:
    - src/model_prediction/ledger.py
    - src/model_prediction/units.py
    - tests/test_units.py
    - docs/DEBUG.md
  created:
    - tests/test_paper_units_edge_scaling.py
decisions:
  - "Operator directive 2026-11-25: no 1.0U moneyline or NCAAF-Over cap; every market edge-scaled"
  - "market_type kwarg kept on edge_scaled_units for call-site compatibility, no longer changes size"
  - "NCAAB HIGH_PROBABILITY_UNIT_CAP, units_cap, and raw-edge<=1% floor left in place"
status: complete
commits: 5
plan_head_before: c784fba17109cbf49a019a908aa8b93c611c6217
plan_head_after: f5efea7f
actuals:
  tokens: 14000
  tasks: 3
  commits: 5
completed: 2026-11-25
---

# Phase quick-261125-1bw Plan 01: Remove the 1U cap on moneylines and NCAAF Overs Summary

Moneyline and NCAAF-Over paper rows now size by edge exactly like spreads and totals (56% vs a 50.0% implied price at uncertainty 0.05 stores 1.75U for moneyline, spread, Over and Under alike; 70% at uncertainty 0.01 stores 2.0U).

## Commits

- `cc7f00d2` fix(quick-261125-1bw): moneylines and NCAAF Overs size by edge like spreads and totals (ledger.py, units.py, tests/test_paper_units_edge_scaling.py, tests/test_units.py)
- `a4056986` docs(quick-261125-1bw): log moneyline and NCAAF-Over edge scaling (docs/DEBUG.md)

## RED output (src identical to HEAD c784fba1)

Command: `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY timeout 600 .venv/Scripts/python -m pytest tests/test_paper_units_edge_scaling.py tests/test_units.py -o addopts='' -q -rf` (src diff against HEAD was empty, `git diff --quiet` exit 0).

```
FAILED tests/test_paper_units_edge_scaling.py::test_moneyline_units_match_spread_units[flat]
FAILED tests/test_paper_units_edge_scaling.py::test_moneyline_units_match_spread_units[main]
FAILED tests/test_paper_units_edge_scaling.py::test_high_edge_moneyline_reaches_the_two_unit_ceiling[flat]
FAILED tests/test_paper_units_edge_scaling.py::test_high_edge_moneyline_reaches_the_two_unit_ceiling[main]
FAILED tests/test_paper_units_edge_scaling.py::test_ncaaf_over_sized_like_under[flat]
FAILED tests/test_paper_units_edge_scaling.py::test_ncaaf_over_sized_like_under[main]
FAILED tests/test_paper_units_edge_scaling.py::test_moneyline_eligibility_units_pass_through
FAILED tests/test_paper_units_edge_scaling.py::test_units_cap_still_binds_moneyline
FAILED tests/test_paper_units_edge_scaling.py::test_unqualified_main_moneyline_scales_by_edge
FAILED tests/test_paper_units_edge_scaling.py::test_flat_moneyline_keeps_flat_call_reason_when_not_qualified
FAILED tests/test_units.py::test_edge_scaled_units_sizes_moneyline_like_spread
11 failed, 15 passed, 1 warning in 3.39s
```

Failure assertion lines (`--tb=line`): `assert 1.0 == 1.75` (ML vs spread, flat and main), `assert 1.0 == 2.0` (high edge, flat and main), `assert 1.0 == 1.75` (Over vs Under, flat and main; the Under assertion above it passed), `assert 1.0 == 1.6`, `assert 1.0 == 1.25`, `assert 1.0 == 2.0` (unqualified Main), `assert 1.0 == 1.75` (Flat reason relabel case), `test_units.py:106 assert 1.0 == 2.0`. The guards that passed at HEAD: low-edge moneyline floor, `test_hybrid_edge_scaled_units_scales_spread_and_total`, and the rest of test_units.py.

## GREEN counts

- `tests/test_paper_units_edge_scaling.py tests/test_units.py tests/test_ledger.py tests/test_main_ledgers.py tests/test_ledger_hardening.py tests/test_eligibility.py tests/test_universal_paper_calls.py tests/test_ledger_invariants.py tests/test_fix_regressions.py tests/test_ncaab_tier1_guardrails.py tests/test_cli.py::test_below_learned_confidence_threshold_downgraded_and_kept_off_main` with `-o addopts='' -q`, env vars unset: **148 passed, 1 warning in 62.45s**. (Run before ruff reformatted the new test file's layout.)
- After the ruff format of the new test file: `tests/test_paper_units_edge_scaling.py tests/test_units.py`: **26 passed**.
- `ruff check` and `ruff format --check` clean on the four .py files. The code negative gates hold (no `is_cfb_over` or `is_moneyline` in ledger.py code, no moneyline early return or "Moneyline caps" text in units.py).

## Daily-run precondition (at edit time, ~06:33Z)

`daily-20261125T052336Z-4320e8`: status `completed`, last heartbeat 2026-11-25T06:12:09Z, PID 17528 (dead). No run in status `started` anywhere in runs.db (query run read-only). Newest daily runs are all completed. No wait was needed.

## Pin updates

Only the pre-planned replacement of `test_hybrid_edge_scaled_units_caps_moneyline` in tests/test_units.py. No other test needed changing.

## Deviations from Plan

**1. [Docs - plan assumption] Line endings.** The plan said all touched files are LF and the CR count must stay 0. Measured by bytes, the working-tree files are CRLF for ledger.py, tests/test_units.py, docs/DEBUG.md and tests/test_ledger.py (git autocrlf=true, index is LF), while units.py is LF. I preserved each file's existing working-tree ending (ledger.py 2395 to 2379 CRLF lines, test_units.py 115 to 118 CRLF, DEBUG.md 6536 to 6552 CRLF, units.py LF 135 to 134) and wrote the new test file as CRLF to match its siblings. The plan's `grep -c $'\r$' ... = 0` gates therefore do not apply as written; byte counts were checked with Python instead (Git Bash `grep` CR counts were unreliable here).

**2. [Rule 2 - extra test] Added `test_flat_moneyline_keeps_flat_call_reason_when_not_qualified`.** Not in the plan's list; guards that the Flat branch still relabels a non-qualified result as FLAT_CALL while sizing moneylines by edge.

**3. [Style] ruff removed an unused `replace` import** from the new test file and reformatted it.

The two Flat/Main blocks stayed inline as planned. Pre-commit stashed and restored other sessions' unstaged files around each commit; nothing of theirs was staged or committed.

## Consequences (also in docs/DEBUG.md)

- **Still 1.00U:** raw edge of 1% or less; NCAAB rows with model probability of 80% or more (`HIGH_PROBABILITY_UNIT_CAP`); unqualified Main and Research moneylines at the default 0.05 uncertainty below roughly 6.9 points of raw edge. All 57 Main NBA moneyline rows since 11-18 are PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED (mean edge 6.6%), so many will still read 1.00U.
- **Non-monotonic curve, not fixed:** 56% vs 50.0% implied gives 1.75U, 62% vs 50.0% implied gives 1.50U at uncertainty 0.05. Moneylines inherit it.
- **Auto-buyer:** stake follows ledger units. Live state read-only on 11-25: `enabled` false, whitelist `nba-spread-margin-v9` only, $5.00 per unit, $25 per game, NCAAB and MLB moneyline disabled. A whitelisted moneyline model could now stake up to 2.0U. NBA ML v6 paper trial hardcodes 1.0U, unaffected. State file and auto-buyer code untouched.
- **Real-money ceiling (T-261125-1bw-01):** dashboard BUY `authorized_units` and `cli execute` `maximum_units` come from row units, so a moneyline per-order cap can reach 2.0U. Still operator-initiated with confirmation; no code changed. Operator decision item.
- **Existing rows not rewritten**; open picks re-size at the next daily forecast. The next scheduled daily (07:00 ET, 12:00Z) is the first run on the new code.

## Known Stubs

None.

## Self-Check: PASSED

- FOUND: src/model_prediction/ledger.py, src/model_prediction/units.py, tests/test_paper_units_edge_scaling.py, tests/test_units.py, docs/DEBUG.md
- FOUND commits cc7f00d2 and a4056986 on refactor/cleanup-2026-11-11

## Orchestrator verification and review cycles

- Independent revert proof on cc7f00d2: with ledger.py and units.py from c784fba1, 11 failed / 46 passed across the new file, test_units, test_ledger, test_main_ledgers; restored, 57 passed.
- Review cycle 1 (PASS WITH NITS): downgraded (unqualified) Main rows kept 1.00U; DEBUG claimed a NCAAF Over hurdle that 32528f57 had removed; code-default buyer layer and NCAAF moneyline missing from the auto-buyer note. Fixed in 9dddf0d3.
- Review cycle 2 (CHANGES REQUIRED): 9dddf0d3 bumped downgraded Main directly, which over-sized the learned NBA moneyline path (request `model_uncertainty=None`: 56% vs 50% gave 1.75U Main vs 1.25U Flat) and applied `units_cap` only inside the bump. Fixed in f7ee09c6: `ledger._edge_sized_units` is the single stake rule for Flat, qualified Main and downgraded Main. Parametrised parity test failed for the uncertainty-None cases against 9dddf0d3.
- Review cycle 3 (CHANGES REQUIRED, logic confirmed): my DEBUG.md splice merged two bullets; the zero-cap test could not fail against 9dddf0d3. Fixed in f5efea7f; the new no-bump cap test fails against 9dddf0d3 (1.6 vs 1.0) and passes now. Review cap (3 cycles) reached with no open findings.
- Final: 127 passed across test_paper_units_edge_scaling, test_units, test_ledger, test_main_ledgers, test_universal_paper_calls, test_eligibility, test_ledger_invariants, test_ledger_hardening, test_ncaab_tier1_guardrails (before the last test addition; that file then 18 passed). ruff clean. Full suite not run (project rule).

## Residuals

- Non-monotonic sizing curve (a model's own non-1.0 size passes through; 1.0 gets the raw-edge bump), pre-existing, now shared by moneylines. Fix would be `max(model_size, bump)`, which raises spread and total stakes too: operator decision.
- NBA ML v6 paper trial and MLB v9 benchmark still stake 1.0U (separate tracks).
- First daily run on the new sizing: 07:00 ET 2026-11-25.
