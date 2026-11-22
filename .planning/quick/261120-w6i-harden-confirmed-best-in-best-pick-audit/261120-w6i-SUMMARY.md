---
phase: quick-261120-w6i
plan: 01
subsystem: best-pick-audit
tags: [audit, confirmed-best, wilson, clv, first-sight, point-in-time]
status: complete
requires:
  - quick-261120-gaq (first-sight bucket basis, 2a48135f)
provides:
  - CONFIRMED_BEST gated on n>=30, Wilson bound above bucket mean price, ROI>=15, bucket CLV, model-family CLV
  - first_sight_edge / audit_candidate(bucket_edge=) for open-candidate bucketing
affects:
  - scripts/audit_best_picks.py
  - tests/test_audit_best_picks.py
  - docs/BEST_PICKS_AUDIT_FRAMEWORK.md
  - docs/DEBUG.md
key-files:
  modified:
    - scripts/audit_best_picks.py
    - tests/test_audit_best_picks.py
    - docs/BEST_PICKS_AUDIT_FRAMEWORK.md
    - docs/DEBUG.md
decisions:
  - "The flat 60% win-rate gate (BEST_MIN_WIN_RATE) is deleted: it is the even-price vig heuristic, blocks profitable underdog buckets and is looser than the price comparator for favourite buckets. The Wilson lower bound above the bucket's stake-weighted mean price is its price-aware form."
  - "A statistically confirmed bucket is never a DOWNGRADE_TRAP; a CLV or family veto leaves it QUALIFIED_MODERATE."
  - "Last-run (old basis) maps stay untouched except the nrfi score-source skip, so they remain comparable context."
commits: 3
plan_head_before: 40cd97080391725fd63b8ab55adb40128a152543
plan_head_after: 0268a8780c0a9da8d8bef2de4a7fca854ef504e5
actuals:
  tokens: 12000
  tasks: 3
  commits: 3
metrics:
  completed: 2026-11-20
---

# Phase quick-261120-w6i Plan 01: Harden CONFIRMED_BEST in the best-pick audit Summary

CONFIRMED_BEST now needs 30 verified first-sight decisions, a one-sided 95% Wilson lower bound above the bucket's stake-weighted mean price, ROI >= 15%, and non-negative bucket and model-family closing-line value. Five first-sight basis bugs (D1-D5) are fixed in the same change. On the live ledger the open-book count of CONFIRMED_BEST goes from 13 to 0.

## Commits (nothing pushed)

| Task | Hash | Files |
| --- | --- | --- |
| 1 D1-D5 first-sight fixes | 9ec2eb63 | scripts/audit_best_picks.py, tests/test_audit_best_picks.py |
| 2 gates A, B, C | 0d937297 | scripts/audit_best_picks.py, tests/test_audit_best_picks.py |
| 3 docs | 0268a878 | docs/BEST_PICKS_AUDIT_FRAMEWORK.md, docs/DEBUG.md |

`git rev-list --count plan_head_before..HEAD` reads 6 because three other sessions' commits landed on the same branch in between (cde2c4e7, ee49c5d6, 336de2b3); the three above are this plan's. Each commit was made with `git commit -- <explicit paths>` and `git show --name-only` lists exactly the intended files.

## Test counts

17 (HEAD) to 21 (Task 1) to 28 (Task 2). Only `tests/test_audit_best_picks.py` was run. `ruff check` and `ruff format --check` are clean on both python files. No quoted sport-name literal in the script (guard grep passes).

## Live before/after, `--all-open` (back to back, read-only, 122 open rows)

| | HEAD | New |
| --- | :---: | :---: |
| CONFIRMED_BEST | 13 | 0 |
| QUALIFIED_MODERATE | 64 | 88 |
| DOWNGRADE_TRAP | 45 | 34 |
| Engine build time | 1.46 s | 1.42 s |

The default unstarted scope has 0 picks at this hour in both. 65 open picks sit in a different bucket on first-sight edge than on current edge. 6 open picks carry an UNPROVEN flag and 30 carry an under-review flag.

No bucket on the ledger is statistically confirmed (n >= 30, Wilson bound above mean price, ROI >= 15%). This differs from the planner's expectation of MLB over 2-4.9% passing. The bucket reads 59W-45L, n=104 (planner expected 105), ROI +15.6%, bound 48.7% against a 49.0 cent mean price, 6 moved keys. It misses by 0.3 points; the n and comparator differences come from this implementation's population rules (D2 verified-first-row, D4, D5) and the stake-weighted mean price. Not gated on, reported only.

Families under review (>= 20 moved keys, mean CLV <= 0): NBA moneyline `nba-elo-trend-lr-v4` (33 moved, -0.5 cents, +16/-17) and `nba-elo-trend-lr-v5` (41 moved, -1.7 cents, +14/-27); NBA spread `nba-spread-margin-v9` (46 moved, -0.6 cents, +18/-28); NBA total under `nba-total-margin-v9` (26 moved, -1.2 cents, +8/-18); NFL moneyline `nfl-elo-trend-lr-v4` (21 moved, -0.6 cents, +7/-14). No family with >= 20 moved keys has positive CLV.

### w6i_counts_head.out (full)

```
module: C:\Users\Vince\AppData\Local\Temp\claude\3c4e37e4a6ec2934\83b7e874-5070-48c7-b39a-c1484ff56543\scratchpad\w6i_head_audit_best_picks.py
(a) engine build time: 1.46s
(b) --all-open rows=122 verdicts={'QUALIFIED_MODERATE': 64, 'DOWNGRADE_TRAP': 45, 'CONFIRMED_BEST': 13}
    unstarted rows=0 verdicts={}
(c) n/a (module has no bucket_edge)
(d) open picks with UNPROVEN flag: 0; with 'under review' flag: 0
(e) CONFIRMED_BEST picks:
    2cd0021127ab4980 | NBA | MONEYLINE | home | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | Bucket 5.0% - 7.9% (nba-elo-trend-lr-v5 only): verified 83.3% WR, +32.8% ROI (5W-1L, 6 verified of 5 graded).
    8dfefa279f814330 | NBA | MONEYLINE | home | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    37da7094b57d4284 | NBA | MONEYLINE | home | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    a9cb55e8fef44e49 | NBA | MONEYLINE | home | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    cf006b7b03564b68 | NBA | MONEYLINE | away | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    192e7cb582274a99 | NBA | MONEYLINE | away | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    b8de7050ae164564 | NBA | MONEYLINE | away | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    e90ae94ffd4a41d2 | NBA | MONEYLINE | away | nba-elo-trend-lr-v5 only | 5.0% - 7.9% | (same bucket line)
    b5a2fd8deb874d5e | NBA | SPREAD | home | nba-spread-margin-v9 only | 5.0% - 7.9% | Bucket 5.0% - 7.9% (nba-spread-margin-v9 only): verified 66.7% WR, +31.7% ROI (6W-3L, 9 verified of 9 graded).
    48aeafebe4e64a95 | NBA | SPREAD | home | nba-spread-margin-v9 only | 5.0% - 7.9% | (same bucket line)
    dcbcf6b1c5674890 | NBA | TOTAL | over | nba-total-margin-v9 only | 8.0% - 11.9% | Bucket 8.0% - 11.9% over (nba-total-margin-v9 only): verified 62.5% WR, +23.5% ROI (5W-3L, 8 verified of 8 graded).
    8ab56f559c604b4b | NBA | TOTAL | over | nba-total-margin-v9 only | 8.0% - 11.9% | (same bucket line)
    4ee301cf40084877 | NCAAF | TOTAL | under | cfb-structural-v5 only | 8.0% - 11.9% | Bucket 8.0% - 11.9% under (cfb-structural-v5 only): verified 60.0% WR, +41.2% ROI (3W-2L, 5 verified of 6 graded).
widget contract OK
```

(The raw file repeats the full bucket line on each row; abbreviated here as "same bucket line". The raw file is at `.../scratchpad/w6i_counts_head.out`.) Note the HEAD "6 verified of 5 graded" shows the D2 defect: more verified than graded.

### w6i_counts_new.out (full)

```
module: E:\MP\scripts\audit_best_picks.py
(a) engine build time: 1.42s
(b) --all-open rows=122 verdicts={'QUALIFIED_MODERATE': 88, 'DOWNGRADE_TRAP': 34}
    unstarted rows=0 verdicts={}
(c) picks whose first-sight bucket differs from current-edge bucket: 65
(d) open picks with UNPROVEN flag: 6; with 'under review' flag: 30
(e) CONFIRMED_BEST picks:
    (none)
widget contract OK
(g) statistically confirmed buckets (n>=30, Wilson bound > mean price, ROI>=15):
    (none)
    families with >= 20 moved keys:
    ('nba', 'moneyline', '', 'nba-elo-trend-lr-v4'): CLV 33 moved, mean -0.5c (+16/-17); under review=True; verified n=55
    ('nba', 'moneyline', '', 'nba-elo-trend-lr-v5'): CLV 41 moved, mean -1.7c (+14/-27); under review=True; verified n=49
    ('nba', 'spread', '', 'nba-spread-margin-v9'): CLV 46 moved, mean -0.6c (+18/-28); under review=True; verified n=106
    ('nba', 'total', 'under', 'nba-total-margin-v9'): CLV 26 moved, mean -1.2c (+8/-18); under review=True; verified n=63
    ('nfl', 'moneyline', '', 'nfl-elo-trend-lr-v4'): CLV 21 moved, mean -0.6c (+7/-14); under review=True; verified n=48
```

`scripts/audit_best_picks.py --all-open` exits 0 (also after Task 1: 17 / 34 / 71 verdicts in that intermediate state), and `--help` works. The Rainmeter AutoLog.py was not run or edited.

## Which buckets and families pass or are under review

- Passing the full bar: none.
- Nearest miss: MLB totals over 2.0-4.9% (see above), out of season, only 6 moved keys.
- Under review: the five families above. Open picks in them cannot confirm until their mean CLV turns positive.

## Revert proofs (CLAUDE.md rule)

Script saved green, broken with Edit, run, restored with `cp`, sha256 compared (identical both times: `ddfd1bb426a56751a2876df3a6168b06e55656ddfaaf9a626154b0b860b72ad4`).

Gate A (statistically_confirmed replaced by HEAD's `v.win_rate >= 60.0 and v.roi >= BEST_MIN_ROI`), `-k "requires_30 or wilson_bound or mean_price_not_candidate"`:

```
E       AssertionError: assert 'CONFIRMED_BEST' == 'QUALIFIED_MODERATE'   (x3)
FAILED tests/test_audit_best_picks.py::test_confirm_requires_30_verified_decisions
FAILED tests/test_audit_best_picks.py::test_confirm_requires_wilson_bound_above_bucket_mean_price
FAILED tests/test_audit_best_picks.py::test_confirm_compares_bucket_mean_price_not_candidate_price
3 failed, 25 deselected
```

Gate B (`clv_unproven = False`), `-k clv_negative_bucket_is_unproven`:

```
>       assert res["verdict"] == "QUALIFIED_MODERATE"
E       AssertionError: assert 'CONFIRMED_BEST' == 'QUALIFIED_MODERATE'
FAILED tests/test_audit_best_picks.py::test_clv_negative_bucket_is_unproven
1 failed, 27 deselected
```

Task 1 red (new/updated tests against the HEAD engine before any engine change): 5 failed, 16 passed. Failures: D5 `assert (3, 0) == (1, 0)`; D1 `AttributeError: no attribute 'first_sight_edge'`; D2 `assert 1 == 0` (HEAD counted the later verified rerun at >= 12%); D3 `assert (0, 1) == (1, 0)` (HEAD graded a loss from the 1-0 first inning); D4 `assert 3 == 1`.

The family gate (C) is pinned by `test_model_family_negative_clv_blocks_confirmation` but was not separately revert-proved (the plan required proofs for A and B only).

## Decision: 60% win-rate gate dropped

`BEST_MIN_WIN_RATE` and its use are deleted. A flat 60% hurdle is the even-price vig heuristic: it blocks profitable underdog buckets (pinned by `test_underdog_bucket_confirms_without_flat_win_rate_hurdle`: 16W-14L at 35 cents confirms) and is looser than the price comparator for favourite buckets. The Wilson bound above the bucket's mean price is the price-aware form. The 55% trap hurdle stays but never overrides a statistically confirmed bucket.

## Deviations from Plan

- None in behaviour or scope. Executed as written, with two non-substantive notes:
  - The stake-weighting check is folded into `test_confirm_compares_bucket_mean_price_not_candidate_price` as the plan describes (28 tests total); it was briefly a separate test and merged back.
  - The working tree held CRLF copies of the two python files (autocrlf); they were normalised to LF locally. Committed blobs are unaffected (HEAD is LF).
- Plan baseline drift: HEAD at start was 40cd9708 rather than c0cc1b6a; both files were clean, baseline 17 passed matched, live baseline counts 13/64/45 matched.
- Correction applied per orchestrator: the external logger (`Rainmeter/Skins/BestPickAudit/@Resources/AutoLog.py`) already calls `engine.first_sight_edge(sport, event_id, market_type, selection, model_id)` when present and passes `bucket_edge=` as a keyword to `audit_candidate`. The implemented signatures match exactly, so the plan's open item "widget does not pass bucket_edge" is not open.

## Open items (not done)

- Soccer draw grading (draw selections and soccer moneyline draws still cannot be graded from scores).
- Zero-unit rows (out of scope per the plan; unaddressed).
- SQL performance rewrite (engine still loads all rows into Python; build time is 1.4 s, unchanged).
- Orchestrator: add the Quick Tasks row to STATE.md (stage HEAD's blob plus only that row; this executor did not touch STATE.md).

## Known Stubs

None.

## Threat Flags

None. The only new database access is one more read-only query (archived/removed keys) through `_connect_ro`; nothing writes to the ledger.

## Self-Check: PASSED

- 9ec2eb63, 0d937297, 0268a878 are ancestors of HEAD.
- scripts/audit_best_picks.py, tests/test_audit_best_picks.py, docs/BEST_PICKS_AUDIT_FRAMEWORK.md, docs/DEBUG.md exist and are committed.
- 28 tests pass; ruff clean.
