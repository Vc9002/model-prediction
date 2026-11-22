---
phase: quick-261120-gaq
plan: 01
subsystem: best-pick-audit
tags: [audit, edge-buckets, first-sight, ledger]
status: complete
requirements: [QUICK-261120-gaq]
key-files:
  modified:
    - scripts/audit_best_picks.py
    - tests/test_audit_best_picks.py
    - docs/BEST_PICKS_AUDIT_FRAMEWORK.md
    - docs/DEBUG.md
    - .codex/skills/forecast-model-picks/SKILL.md (working tree only, NOT committed)
commits: 2
plan_head_before: b2d8df2be3f6c37fd0f40e382ebbedfb6ddc57e3
plan_head_after: 24091844a0aa8548c0ddaecf687cb210b13013bd
actuals:
  tokens: 40000
  tasks: 3
  commits: 2
---

# Phase quick-261120-gaq Plan 01: Best-pick audit buckets keyed on first sighting Summary

Edge buckets in `scripts/audit_best_picks.py` now use each decision's earliest open/superseded/settled row (verified: earliest verified row), graded at that row's own line and price. The old last-pre-game-run (settled row) record is kept as context only.

## Commits

- `2a48135f` fix(quick-261120-gaq): engine + 5 new tests (exactly `scripts/audit_best_picks.py`, `tests/test_audit_best_picks.py`)
- `24091844` docs(quick-261120-gaq): framework doc + DEBUG entry (exactly the two doc files)

## What changed

- `HistoricalAuditEngine`: `stats_by_*` maps now hold first-sight; new `last_run_by_*` maps reproduce HEAD; `ungraded_first_sight_keys` counter. One read-only query (`status IN open, superseded, settled`, scores via `json_extract` under `json_valid`). Archived/removed rows never read.
- Ordering parses `created_at_utc` (mixed `Z`, no-fraction, `+00:00` forms; naive read as UTC; unparseable sorts last), tier rank main first as tie-break.
- Grading order: own settled result; else final scores from the event's settled rows (moneyline, spread with selected-side line, total over/under); else same-key settled sibling at the same line; else skipped. Price-derived pnl for non-own grades. Missing/invalid price skips the row. No fall-forward to later rows.
- `audit_candidate`: every existing key, threshold, MIN_GRADED and basis string unchanged; adds `last_run_evidence` (same `use_model` decision as the evidence) and a "Context, last pre-game run (old basis): verified XW-YL, Z% ROI." flag.

## Verification

- `ruff check` and `ruff format --check` clean on both files (pre-commit also passed).
- `python -m pytest tests/test_audit_best_picks.py -o addopts='' -q`: 17 passed (12 existing + 5 new).
- Live `python scripts/audit_best_picks.py`: exit 0, wall time about 1.4 s total for the CLI (engine load baseline was about 0.5 s; the CLI also queries open picks and prints).

### Revert proof

With HEAD's engine restored, `test_first_sight_bucket_not_last_rerun` failed:
`E       assert 0 == 1` / `where 0 = EdgeTierStats(wins=0, losses=0, ...).losses` (5-8% bucket had 0 graded at HEAD). After restoring the new engine, sha256 matched the saved copy (`a1df4284...e663f`) and all 17 tests passed. Note: test (c) (archived/removed) already passed at HEAD, since HEAD only read settled rows; it is a guard, not a red test. Tests (a), (b), (d), (e) failed at HEAD.

### Task 2 live comparison output (read-only, live ledger)

```
  sport_mkt: 268 keys identical
  sport_dir: 127 keys identical
  model_mkt: 575 keys identical
  model_dir: 299 keys identical
LAST_RUN_EQUALS_HEAD True
NBA total under 5-8%, model nba-total-margin-v9
  first-sight  all_rows: 1W-5L-0P, WR 16.7%, ROI -67.3%, graded 6, pnl -5.05U
  first-sight  verified: 1W-5L-0P, WR 16.7%, ROI -67.3%, graded 6, pnl -5.05U
  last-run     all_rows: 8W-1L-0P, WR 88.9%, ROI +77.4%, graded 9, pnl +8.71U
  last-run     verified: 8W-1L-0P, WR 88.9%, ROI +77.4%, graded 9, pnl +8.71U
NBA total under 5-8%, pooled
  first-sight  verified: 4W-11L-0P, WR 26.7%, ROI -47.1%, graded 15, pnl -8.84U
  last-run     verified: 13W-3L-0P, WR 81.2%, ROI +61.1%, graded 16, pnl +12.22U
NBA moneyline 5-8%, pooled
  first-sight  all_rows: 10W-6L-0P, WR 62.5%, ROI +8.6%, graded 16, pnl +1.39U
  first-sight  verified: 9W-5L-0P, WR 64.3%, ROI +5.9%, graded 14, pnl +0.84U
  last-run     all_rows: 10W-3L-0P, WR 76.9%, ROI +31.6%, graded 13, pnl +4.11U
  last-run     verified: 8W-1L-0P, WR 88.9%, ROI +47.6%, graded 9, pnl +4.29U
NBA spread 5-8%, model nba-spread-margin-v9
  first-sight  verified: 6W-3L-0P, WR 66.7%, ROI +31.7%, graded 9, pnl +3.57U
  last-run     verified: 8W-3L-0P, WR 72.7%, ROI +41.3%, graded 11, pnl +5.68U
NBA spread 5-8%, pooled
  first-sight  verified: 15W-9L-0P, WR 62.5%, ROI +23.3%, graded 24, pnl +6.99U
  last-run     verified: 13W-12L-0P, WR 52.0%, ROI +1.6%, graded 25, pnl +0.49U
ungraded_first_sight_keys 494
old verdict CONFIRMED_BEST; new verdict DOWNGRADE_TRAP; new basis nba-total-margin-v9 only
  flag: Bucket 5.0% - 7.9% under (nba-total-margin-v9 only): verified 16.7% WR, -67.3% ROI (1W-5L, 6 verified of 6 graded).
  flag: Context, all models pooled: verified 26.7% WR, -47.1% ROI (4W-11L).
  flag: Context, last pre-game run (old basis): verified 8W-1L, +77.4% ROI.
new evidence.verified:      1W-5L-0P, WR 16.7%, ROI -67.3%, graded 6, pnl -5.05U
new last_run_evidence.verified: 8W-1L-0P, WR 88.9%, ROI +77.4%, graded 9, pnl +8.71U
WIDGET_CONTRACT_OK
```

(Other per-model NBA spread 5-8% lines are in the scratchpad output `gaq_compare.out`.)

Reading the numbers: the last-run maps equal HEAD's maps exactly (identical key sets and counts, pnl/staked within 1e-6). The NBA under 5-8% bucket collapses from 8W-1L (+77.4%) to 1W-5L (-67.3%) for v9, and the verdict for a v9 under at +6.5% edge flips CONFIRMED_BEST to DOWNGRADE_TRAP. The orchestrator's tracker figure (first-in-band 11W-10L, +3.5%) is a different population from the ledger's first-sight 4W-11L pooled unders; reported, not reconciled. Moneyline pooled verified is +5.9% first-sight (the brief's "about +11%" was the orchestrator's figure; measured here 5.9% verified, 8.6% all rows). NBA spread v9 moves much less (+31.7% vs +41.3%).

Extra sanity check (scratch, not committed): the score-grading rule agreed with the ledger's own settled WIN/LOSS/PUSH on 13,751 moneyline, 4,162 spread and 4,698 total settled rows; the only disagreements were soccer moneyline (346, draws) and tennis spread/total (4, set-based), all out of production scope and only reachable via the score fallback for non-settled first rows.

## Deviations from Plan

None in logic. Notes:
- `ruff format` reformatted both files before the first commit (pure formatting of the new code).
- The pre-commit hook stashed/restored unstaged files around each commit via its own mechanism (`patch...` in `~/.cache/pre-commit`); the working tree was restored and the other sessions' modified files remain unstaged (confirmed `git status`).
- `.codex/skills/forecast-model-picks/SKILL.md` item 1 under "Before naming any pick best" was edited in the working tree to say the bucket is recomputed from each decision's first-sighting row. That file also holds someone else's uncommitted section, so it is deliberately NOT staged or committed; it remains modified in the working tree.
- Nothing under src/, config/, the auto-buyer, the ledger DB, or the Rainmeter AutoLog.py was touched or run. STATE.md/ROADMAP.md/PLAN.md untouched.

## Known Stubs

None.

## Threat Flags

None. The script remains read-only (`_connect_ro`, `mode=ro`).

## Self-Check: PASSED

- scripts/audit_best_picks.py, tests/test_audit_best_picks.py, docs/BEST_PICKS_AUDIT_FRAMEWORK.md, docs/DEBUG.md: present, committed.
- Commits 2a48135f and 24091844 are ancestors of HEAD; `git show --name-only` lists exactly the intended files for each.
- `git rev-list --count b2d8df2b..HEAD` = 2, matching `commits: 2`.
