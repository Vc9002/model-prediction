---
phase: quick-261118-x3p
plan: 01
subsystem: nba-availability-research
tags: [nba, availability, ablation, research-only, v7, boxscores]
requires: []
provides:
  - scripts/ablate_nba_real_availability_v2.py (3-arm availability ablation)
  - tests/research/test_nba_availability_ablation_v2.py (micro-tests)
affects: []
tech-stack:
  added: []
  patterns: [batched per-date predict_games with config-suffixed event ids, true-last-game coverage rule]
key-files:
  created:
    - scripts/ablate_nba_real_availability_v2.py
    - tests/research/test_nba_availability_ablation_v2.py
  modified: []
decisions:
  - "Research only: no serving, config, ledger, data or promotion change"
  - "Validation selected zero; real arms beat static but not zero on validation"
metrics:
  tasks: 2
  files: 2
status: complete
commits: 2
plan_head_before: bfcf3711310a13552f46995dfab9eba6140ef4e5
plan_head_after: c714f097dfbe99a56dc9583f8149b6c6ea40a548
actuals:
  tasks: 2
  commits: 2
completed: 2026-11-19
---

# Phase quick-261118-x3p Plan 01: NBA real player availability ablation v2 Summary

Three-arm ablation (zero / STATIC tiers / REAL replacement-relative box-score values, player_id-keyed, EB-shrunk over played games only) on the same v7 engine and the same 60/20/20 split as v1: on validation no availability arm beat the zero gap, real values beat the static tiers, and the real-vs-zero differences are at noise level.

This is research only. No serving code, config, ledger or `data/` file was touched, no capture or backfill was run, and `scripts/ablate_nba_real_availability.py` is unchanged (last commit `59cfc2f3`). Any promotion would need the governed `model_promotion` path with walk-forward evidence on a holdout that is better covered than this one.

## Commits (code only)

- `3f2a3481` feat(quick-261118-x3p): helpers, micro-tests (8 pass), smoke path
- `c714f097` feat(quick-261118-x3p): full grid, single holdout touch, data-lag report

Run artifacts (scratchpad): `nba_avail_v2.log` and `nba_avail_v2.json` under `C:\Users\Vince\AppData\Local\Temp\claude\3c4e37e4a6ec2934\fac1a355-e782-413c-b381-d84f2c880f93\scratchpad\`.

## Definitions

- impact per played game = `box_plus_minus_proxy(row) * minutes / 48` (per-36 rate times share of a game; 0.075 x the raw box composite). Rows with 0 minutes never enter a player's history.
- real value = `max(0, (5*r + n*mean(last 15 played impacts)) / (5 + n) - r)`, r = replacement level; a player with no played history falls back to his static tier (0 such fallbacks occurred).
- static value = name tier 4.8 / 2.5 / 1.1 x (1 - P(Out)) = x 1.0; team loss capped at 12 for both arms.
- gap = k x (away_loss - home_loss); total loss = k x (home_loss + away_loss).
- A side is "covered" only if the team's actual most recent completed game (from nba_games_all.jsonl) has a boxscore; otherwise the side contributes 0 and is flagged uncovered. No stale February box can stand in for a March game.
- Limitation, plainly: no historical injury-report archive exists, so "out" is INFERRED from box scores (rotation player, mean minutes >= 8 over the last 15 boxscore games, non-COACH'S-DECISION DNP in the team's true last game). Both availability arms share this set and differ only in per-player value. This proxy misses players ruled out for the first time tonight and keeps players who return tonight, and every inferred absence is weighted as Out.

Train-only replacement-level fit (526 rotation players, 10+ played games, mean minutes >= 8): p10 0.490, p25 0.693, p50 1.006, p75 1.554, p90 2.169. Grid values sit at percentile rank 10.8 (0.5), 49.6 (1.0), 73.0 (1.5), 86.9 (2.0). Train p25 added as the extra candidate (0.69). Levels evaluated: 0.5, 0.69, 1.0, 1.5, 2.0.

Name collisions (player_id-keying check): 0 of 843 normalized names map to more than one player_id before the newest header date.

## Validation grid (933 games, 131 dates; 19 configs in one batched pass)

Batch equivalence (batched "zero" vs solo predict_games) verified exact on the first date. Columns: MAE = margin MAE in points, Acc = moneyline accuracy %, nzGap = games with a nonzero gap, |gap| = mean absolute gap over those games (points), e* = subset where both sides are covered (727 games).

| config | n | MAE | Brier | Acc% | nzGap | mean abs gap | eN | eMAE | eBrier | eAcc% |
|---|---|---|---|---|---|---|---|---|---|---|
| zero | 933 | 11.453 | 0.2232 | 63.6 | 0 | n/a | 727 | 11.278 | 0.2257 | 62.7 |
| real_r2.0_k0.25 | 933 | 11.453 | 0.2232 | 63.6 | 70 | 0.09 | 727 | 11.279 | 0.2257 | 62.7 |
| real_r0.69_k0.25 | 933 | 11.453 | 0.2232 | 63.5 | 229 | 0.21 | 727 | 11.279 | 0.2256 | 62.6 |
| real_r0.5_k0.25 | 933 | 11.453 | 0.2232 | 63.3 | 233 | 0.25 | 727 | 11.279 | 0.2256 | 62.4 |
| real_r2.0_k0.5 | 933 | 11.453 | 0.2232 | 63.6 | 81 | 0.15 | 727 | 11.279 | 0.2256 | 62.7 |
| real_r1.0_k0.25 | 933 | 11.453 | 0.2232 | 63.6 | 197 | 0.17 | 727 | 11.279 | 0.2256 | 62.7 |
| real_r1.5_k0.25 | 933 | 11.453 | 0.2232 | 63.6 | 125 | 0.13 | 727 | 11.279 | 0.2257 | 62.7 |
| real_r0.69_k0.5 | 933 | 11.454 | 0.2232 | 63.3 | 229 | 0.42 | 727 | 11.279 | 0.2256 | 62.4 |
| real_r2.0_k1.0 | 933 | 11.454 | 0.2232 | 63.6 | 81 | 0.31 | 727 | 11.280 | 0.2256 | 62.7 |
| real_r0.5_k0.5 | 933 | 11.454 | 0.2232 | 63.3 | 233 | 0.49 | 727 | 11.279 | 0.2256 | 62.4 |
| real_r1.0_k0.5 | 933 | 11.454 | 0.2232 | 63.5 | 197 | 0.34 | 727 | 11.280 | 0.2256 | 62.6 |
| real_r1.5_k0.5 | 933 | 11.454 | 0.2232 | 63.6 | 125 | 0.26 | 727 | 11.281 | 0.2257 | 62.7 |
| static_k0.25 | 933 | 11.456 | 0.2232 | 63.3 | 232 | 0.48 | 727 | 11.281 | 0.2257 | 62.4 |
| real_r1.5_k1.0 | 933 | 11.456 | 0.2232 | 63.5 | 125 | 0.53 | 727 | 11.283 | 0.2256 | 62.6 |
| real_r1.0_k1.0 | 933 | 11.457 | 0.2232 | 63.5 | 197 | 0.68 | 727 | 11.283 | 0.2256 | 62.6 |
| real_r0.69_k1.0 | 933 | 11.457 | 0.2232 | 63.3 | 229 | 0.84 | 727 | 11.283 | 0.2256 | 62.4 |
| real_r0.5_k1.0 | 933 | 11.458 | 0.2232 | 63.3 | 233 | 0.99 | 727 | 11.284 | 0.2256 | 62.4 |
| static_k0.5 | 933 | 11.463 | 0.2233 | 63.3 | 232 | 0.96 | 727 | 11.288 | 0.2257 | 62.4 |
| static_k1.0 | 933 | 11.491 | 0.2234 | 63.1 | 232 | 1.92 | 727 | 11.319 | 0.2257 | 62.2 |

Unrounded deltas versus zero (validation, margin MAE / Brier; negative Brier = better): best real by Brier is real_r0.5_k1.0 and real_r0.69_k1.0 at -0.000044 Brier with +0.0058 and +0.0046 MAE; real_r1.0_k1.0 is +0.0043 MAE, -0.000029 Brier; static_k1.0 is +0.0383 MAE, +0.000158 Brier (worse on both); static_k0.5 is +0.0105 MAE, +0.000076 Brier.

Diagnostics (validation): side status ok 1,460 / uncovered 400 / no_prior_game 6; 315 inferred-absent player-games; static tier hits 4.8: 11, 2.5: 67, 1.1: 237 (no silent collapse to all-1.1); real fallback to static: 0.

Winner (validation only, lowest unrounded margin MAE, Brier tie-break): **zero**. "No availability arm beat the zero gap on validation." (Zero is the winner by 0.00012 MAE over real_r2.0_k0.25.) The edge-of-grid note did not apply because the winner is the zero arm.

## One-shot holdout (681 games, 133 dates, touched once after selection)

Because validation selected zero, the set {winner, zero, static k=1.0} reduced to two configs. No real-value arm was evaluated on the holdout (the plan forbids a second holdout touch and a real arm was not the winner).

| config | n | MAE | Brier | Acc% | nzGap | mean abs gap | eN | eMAE | eBrier | eAcc% |
|---|---|---|---|---|---|---|---|---|---|---|
| zero (= winner) | 681 | 12.096 | 0.1945 | 70.5 | 0 | n/a | 91 | 10.895 | 0.2088 | 65.9 |
| static_k1.0 | 681 | 12.120 | 0.1947 | 70.2 | 11 | 2.42 | 91 | 11.077 | 0.2105 | 63.7 |

Deltas versus zero: winner 0 by construction; static_k1.0 +0.0243 margin MAE and +0.00023 Brier on all 681 games, +0.1820 MAE and +0.00173 Brier on the 91 eligible games (worse on both). Holdout diagnostics: side status ok 184 / uncovered 1,177 / no_prior_game 1; 21 inferred-absent player-games.

**Holdout coverage caveat:** the holdout is underpowered: only 91 of 681 holdout games have both sides covered by boxscores (about 13%; planning-time count of holdout games with a boxscore was about 92). Only 11 holdout games carried any static gap at all. The holdout cannot confirm or reject a real-value signal.

## Boxscore data lag and backfill findings

- 2,807 files (0 unparsed) in `data/availability/nba/espn_boxscores/`; header game dates run 2024-01-01 to **2026-11-02** (newest), 17 days before today (2026-11-19).
- Coverage of `nba_games_all.jsonl`: 2,807 of 3,899 games (72.0%); 0 covered games have team names missing from the boxscore keys.
- Coverage by month for the gap period: 2026-01 233/233, **2026-02 5/172, 2026-03 0/237, 2026-04 0/147, 2026-05 0/40, 2026-06 0/5**, 2026-10 84/140, **2026-11 8/126**. Earlier seasons: Nov-Mar full, April partial (postseason/play-in absent), May-June 0, October partial (preseason absent).
- 596 rows in `nba_games_all.jsonl` carry null status and null season_type. `scripts/backfill_nba_boxscores.py` selects only `season_type == "regular-season"` and `status == "completed"`, so it never selects those rows; this explains the Feb-Jun 2026 hole.
- Capture-script and scheduler findings: `scripts/backfill_nba_boxscores.py` exists and is the only NBA boxscore writer. The re-run grep for `backfill_nba_boxscores`, `espn_boxscores` and `nba_boxscore` across `scripts/register_windows_tasks.ps1`, `src/model_prediction/cli/daily.py` and `ops/` returned nothing: nothing schedules it (daily only captures NCAAB boxscores). A repo-wide search finds no other reference to the script outside this plan and the v2 script.
- **Refresh needed before any live use: yes.** The script's own verdict is "REFRESH NEEDED before any live use" (newest header date is 17 days old, threshold 2 days), and the Feb-Jun 2026 and November 2026 holes would leave most current teams uncovered.

## Plain verdict

Do real, replacement-relative values beat zero and static?

- Versus static: yes, consistently on validation. At full strength (k=1.0) every real arm has a lower margin MAE than static_k1.0 (11.454 to 11.458 against 11.491, about 0.033 to 0.037 points) and a lower Brier (0.2232 against 0.2234). Static also lost to zero on validation and on the holdout, so the served static tiers did not help in this test. Part of that gap is magnitude (real mean absolute gap 0.68 to 0.99 points at k=1.0 against 1.92 for static), not necessarily per-player value quality; at the matched magnitude of static_k0.25 (0.48) the real arms are only about 0.002 MAE better.
- Versus zero: no. On validation the best real arms tie zero. Margin MAE is slightly worse than zero (+0.0001 to +0.0058 points) while Brier is slightly better (up to -0.000044). These differences are far below any reasonable noise floor for 933 games (the validation margin MAE is 11.45 points), so the honest reading is no detectable effect. By the pre-declared rule (lowest margin MAE) zero won and the real arm was never put on the holdout.
- The inferred-absence proxy limits what this test can say: it only sees absences that showed up in the team's previous game, and 21% of validation sides (400 of 1,866) were uncovered.

No recommendation to promote is made. This is research only.

## Deviations from Plan

**1. [Rule 3 - Blocking/usefulness] Smoke dates moved to mid-validation**
- **Found during:** Task 1 smoke
- **Issue:** The first five validation dates are the season opener, where each team's last game is a preseason game with no boxscore, so every side was uncovered and the smoke exercised nothing beyond the "uncovered" branch (all three configs identical).
- **Fix:** `--smoke` uses SMOKE_DATES consecutive dates starting at the middle of the validation date list. The full run still evaluates all validation dates.
- **Files modified:** scripts/ablate_nba_real_availability_v2.py
- **Commit:** 3f2a3481

**2. [Minor] Section F header renamed**
- The plan wants the phrase "LOCKED HOLDOUT" exactly once in the log; the section header was renamed "F. FINAL CHECK, one-shot" so only the table title carries the phrase.

**3. [Interpretation] Fewer than two covered games**
- `inferred_absent` treats a team with fewer than two covered games before the cutoff as "uncovered" (no usable rotation window), matching v1's `len(window) < 2` guard. The plan did not name this case.

The full run completed on the first attempt (no crash, no rerun, one holdout touch). The one `LOCKED HOLDOUT` line appears after the collision diagnostic and the validation table in the log; the `holdout_games` variable appears in exactly two non-comment lines of the script.

## Known Stubs

None.

## Threat Flags

None. The only write path is `--out`, which is refused (exit 3, verified) when it resolves inside the repo.

## Self-Check: PASSED

- scripts/ablate_nba_real_availability_v2.py and tests/research/test_nba_availability_ablation_v2.py exist and are committed (3f2a3481, c714f097; each commit lists only those files).
- 8 micro-tests pass; ruff check and ruff format --check clean; v1 script unchanged (59cfc2f3, clean status); in-repo `--out` refused with exit 3 and no file created; result JSON passes the key check (JSON_OK).
