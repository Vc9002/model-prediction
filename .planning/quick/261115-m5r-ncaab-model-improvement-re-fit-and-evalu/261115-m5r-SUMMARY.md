---
phase: quick
plan: 261115-m5r
subsystem: ncaab-models
tags: [ncaab, v7, shadow, multi-season, as-of-conference-map, ridge, market-relative, no-edge]
status: complete
requires:
  - ncaab-spread-v6 / ncaab-total-v6 artifacts (read only)
  - data/research/ncaab_market/ archives from quick 261115-1m8
provides:
  - src/model_prediction/models/ncaab_v7_multiseason.py (inert components, as-of map, ridge grid)
  - scripts/ncaab_v7_multiseason_pin.py (pinned rule, sha256 76f8a71a...)
  - scripts/ncaab_v7_multiseason.py (components, gate, fit, final, render)
  - outputs/ncaab/ncaab_v7_multiseason.{md,json}, ncaab_v7_multiseason_frozen.json, ncaab_v7_multiseason_fit_pin1.json
key-files:
  created:
    - src/model_prediction/models/ncaab_v7_multiseason.py
    - scripts/ncaab_v7_multiseason.py
    - scripts/ncaab_v7_multiseason_pin.py
    - tests/test_ncaab_v7_multiseason.py
    - tests/research/test_ncaab_v7_multiseason_runner.py
    - outputs/ncaab/ncaab_v7_multiseason.md
    - outputs/ncaab/ncaab_v7_multiseason.json
    - outputs/ncaab/ncaab_v7_multiseason_frozen.json
    - outputs/ncaab/ncaab_v7_multiseason_fit_pin1.json
  modified: []
decisions:
  - v7 stays shadow; no registration, no artifact, no promotion, no ledger write, nothing outside NCAAB spread/total model code and research scripts
  - forecast quality improved on every pinned criterion; the market-relative rule accepts no model, so there is no edge claim
commits: 6
plan_head_before: b0694609
---

# Quick 261115-m5r: NCAAB v7 multi-season re-fit and evaluation

**Result: v7 is a clearly better forecaster than v6 (D1 margin RMSE 11.50 vs 11.81 on 2024-25, early season 13.95 vs 16.09 at zero games, large mismatches no longer compressed), but it is still worse than the closing line (11.19) and the market-relative rule accepts nothing. The finding is no edge over the line. Not promoted.**

Full numbers, both scorecards and every caveat are in `outputs/ncaab/ncaab_v7_multiseason.md` (JSON alongside).

## What was done

- **As-of-season conference map** from the ESPN conference id on every scoreboard game (id to name table, unknown ids raise, D1 independents scored at the non-D1 prior, old Pac-12 id 21 through 2023-24 hand-set to tier 1660). The "not available" case did not apply. With today's map the replay's v6 matches `v6_projections.jsonl` exactly (max abs difference 0.0 on spread and total over 554 rows). Map risk: v6 as-of minus v6 as served is -0.027 RMSE [-0.042, -0.013], small.
- **Multi-season ridge grid** (carry^age, lambda toward the tier prior, tier-prior scale) selected by leave-one-season-out over 2022-23 and 2023-24 only: carry 0.1, lambda 2, kappa 1.4, combiner F1 (a*ridge + c*tail), t-scales by games played, a fitted non-D1 rule, and a per-games-played recalibration of the v6 total (kept because it beat v6 as-of by 0.107 RMSE leave-one-season-out).
- **Pinned before the decision read**: windows, grid, selection rules, comparators, cohorts, acceptance rule and the market-rule hash (`ncaab-v7-market-rule-2`, K = 12 spread / 11 total counting the nine models 1m8 read) in `ncaab_v7_multiseason_pin.py`. 2024-25 is the decision window; 2025-26 and 2026-27 are labelled contaminated and carry no ESPN-closing-line x outcome x model number.

## Results

| scorecard | verdict |
|---|---|
| A, forecast quality (2024-25, 5,764 D1 games) | PASSED all three pinned criteria: D1 RMSE delta vs v6 as-of -0.310 [-0.430, -0.214]; win Brier delta -0.0038 [-0.0052, -0.0025]; games 0-2 RMSE delta -1.18 [-1.64, -0.67] |
| B, market-relative (2024-25) | NO PICK: v7, ridge, v6 as-of (spread) and v6 as-of total, v7 total all fail both criteria. Plain slope of (actual - line) on (v7 - line) -0.085 [-0.215, +0.052]; total +0.035 [-0.086, +0.144] |
| Mismatch fix | NOT CLAIMED by the pinned rule (see below) |
| Non-D1 no-pick rule on the ledger | declines 3 of the first 610 settled Flat rows, none of the losing ones |

## Deviations from Plan

1. **[Pin amended once]** Pin 1 (commit b4a61cf0) was committed before any outcome join. Its fit-window selection landed on the corner of all three grid axes, so the ridge grid was extended outward and re-pinned as pin 2 (commit 7127ad86, sha256 76f8a71a7e511b0c44cb4219c4b3ff5f1114eadbc51a0f77fe28329e3b6f3fa4) AFTER fit-season outcomes had been read and BEFORE any 2024-25 outcome was read. Pin-1 fit result kept in `ncaab_v7_multiseason_fit_pin1.json`. Pin 2 selected an interior point.
2. **[`final` ran three times]** The first run was the decision read. Two reruns added the first-610 ledger block and reworded the report; every non-ledger block was compared programmatically and is identical. The markdown was then re-rendered from the stored JSON (`render` stage, no outcome read). Treat 2024-25 as read once in substance, three times mechanically.
3. **[Plan said 610 rows]** The ledger now holds 634 settled Flat v6 spread/total rows (610 was the count at about 2026-11-15T05:34Z). Both are reported; the 3 declined rows are the same either way (all moneyline: Coastal Carolina vs Virginia Lynchburg, Houston Christian vs College of Biblical Studies, Weber State vs Lincoln (CA); real non-D1 opponents).
4. **[Line-joined metrics]** To keep the 1m8 locked windows unspent, no ESPN closing line is joined to 2025-26 or 2026-27 outcomes. The one exception is the 357-row settled-ledger replay (ledger's own quoted lines vs 2026-27 outcomes), labelled contaminated.
5. **[Tests location]** Runner-importing guards live under `tests/research/` (project convention); pure-function tests are in `tests/test_ncaab_v7_multiseason.py` (8 passed).
6. **[Not updated]** STATE.md and ROADMAP.md were not touched: STATE.md carries another session's uncommitted edits. The orchestrator should record this task. Commits `b6d3177a`, `c7c28386`, `171f158d` and the uncommitted `cli/daily.py`, `cli/commands.py`, `tests/test_artifact_hash_verification.py` edits belong to other sessions. `commits: 6` counts only this task's commits (b4a61cf0, 7127ad86, 729702ba, 26ebd1cd, cc7d1ef9 and this summary commit); a rev-list over the range also includes the other sessions' commits.

## Caveats

- **v7 is the ridge.** v7 minus ridge-only is +0.001 [-0.007, +0.009]; the v6 tail term adds nothing. Carry 0.1 differs from 1m7's declared 0.5 (one prior season there; the fit-window table here falls monotonically with less carry, less shrinkage and a larger tier prior).
- **Inputs v7 leans on harder than v6:** the tier table scaled by 1.4 and archived AP ranks. The tier table was authored 2026-11-08 and its estimation window is not recorded, so it may overlap the replay seasons; the Pac-12 override is hand-set; ranks are as served at fetch time, not verified pre-game; a season's conference membership is read from any of the team's games that season (membership only, never a result).
- **v6 as served win Brier** uses the t formula on the projection mean and sd (the file carries no win probability), so it is not like-for-like with v6 as-of's served blend.
- Margins are still compressed at the very top (|line| >= 20: v7 bias +2.63 vs v6 +8.39, market +0.73; v7 RMSE 13.69 vs market 12.83).

## Known Stubs

None. All modules are inert shadow research code.

## Threat Flags

None. Research-only: local archives read, runtime ledger opened `mode=ro`, no network, no config, ledger or registry writes.

## Self-Check: PASSED

- Created files exist; commits b4a61cf0, 7127ad86, 729702ba, 26ebd1cd, cc7d1ef9 are ancestors of HEAD.
- `pytest tests/test_ncaab_v7_multiseason.py tests/research/test_ncaab_v7_multiseason_runner.py -o addopts='' -q`: 11 passed; `tests/test_ncaab_v7_shadow.py` still passes; ruff clean on the touched files.
