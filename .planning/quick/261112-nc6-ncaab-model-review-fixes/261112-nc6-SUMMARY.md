---
status: complete
quick_id: 261112-nc6
---

# NCAAB: close every open finding from docs/MODEL_REVIEW_2026-11-11.md

Run inline (gsd-tools not installed on this machine), same as the other 2611xx quick tasks.
Commit `e32335c7` (code, artifacts, tests, harness, report, evidence doc). The DEBUG.md entry
and the review-doc status edits were staged as isolated hunks but were swept into the parallel
session's docs commit `10b82f8a` before this task committed; their content is in HEAD. One
review-doc line and the HANDOFF §1 update remain in the working copy alongside the other
session's uncommitted NBA addendum.

## What was done

- Parameterised every fix in `ncaab_spread_v6.py` / `ncaab_total_v6.py` with defaults that
  reproduce the live model bit-for-bit (verified against HEAD on 662 probabilities and
  against all 68 live Flat rows of the 05:40Z daily — reproduction gate 0 deltas).
- `TrendEngine` gained optional per-team offense/defense shrinkage baselines.
- Walk-forward ablation (`scripts/ncaab_v6_review_ablation.py`, 28 variants, 60/20/20 by
  complete ET dates, selection on validation only) chose: coherent moneyline, tiered trend
  priors, additive matchup, HCA 5.0 / Elo HCA 45, `total_sd` 16.5, conference priors with
  league pace reference. Rejected: Student-t scale matching (coverage got worse), Elo-weight
  ramp (inert on validation), `league_total_weight` 0.15, retention 0.2 (not selectable).
- Artifacts rewritten to list every served parameter; factories load them hash-verified via
  `from_artifact`; unread policy keys removed; `max_edge`/`dynamic_sizing` consumed.
- Integer-line push mass excluded for spread and total.
- 13 new regression tests + 2 TrendEngine tests; 58 tests across the affected files pass.

## Validation (n = 1,043) control -> shipped

moneyline Brier 0.2058 -> 0.2020; margin MAE 9.046 -> 8.844; margin bias -1.92 -> -0.87;
cover Brier 0.2133 -> 0.2111; total MAE 14.30 -> 13.78; total PIT 0.40/0.68/0.81 -> 0.49/0.82/0.92.
Holdout (n = 742, mostly the 2026-11 early-season window): margin MAE 17.76 -> 16.52, total MAE
15.11 -> 14.48, moneyline Brier 0.1112 -> 0.1217 (worse; all variants under-disperse there).

## Verification

- `ruff check` / `ruff format --check` clean on every touched file.
- `pytest tests/test_ncaab_v6_review_fixes.py tests/test_ncaab_v6.py tests/test_college_basketball_forward.py
  tests/test_trends.py tests/test_trend_engine.py tests/unit/test_ncaab_sport_adapter.py
  tests/test_production_registry.py tests/test_nba_v9.py -o addopts='' -q` -> 58 passed.
- Red-without-fix: artifact tests failed on the pre-fix artifacts; the old integer-line total
  assertion failed on the push fix (rewritten to symmetry + push).

## Open (recorded in HANDOFF §1)

Early-season under-dispersion (both markets), Elo ramp, retention 0.2 vs 0.35 — need a second
cross-season window or market lines to select on. Both champions changed in place under the same
version string; rows before 2026-11-12T07:00Z were priced by the old model.
