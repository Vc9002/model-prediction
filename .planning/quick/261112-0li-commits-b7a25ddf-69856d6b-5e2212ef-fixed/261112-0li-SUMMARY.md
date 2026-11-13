---
phase: quick-261112-0li
plan: 01
subsystem: portfolio
tags: [auto-buyer, dedup, polymarket, pytest, audit, ledger-routing]

# Dependency graph
requires:
  - phase: quick-261111-tx4
    provides: "Composite game-market identity dedup for the NCAAB paper trial (b7a25ddf/69856d6b), the bug class this audit checked every other path against"
provides:
  - "Mutation-proven regression tests pinning the main auto-buyer's identity dedup (matchup_markets) across both the live and paper_auto_buyer ledger roots, against the real 2026-11-07 NBA ORL@GSW total duplicate"
  - "A pin that AutoPolymarketBuyer.paper_data_root defaults to PAPER_AUTO_BUYER_DATA_ROOT"
  - "A dated, re-measured, mutation-proven audit section in docs/LEDGER_ROUTING.md closing Phase 1 item 4 of the ledger-improvement plan"
affects: [auto-buyer-worker, ledger-routing-docs, dashboard-paper-ledger]

# Actuals (#2632)
actuals:
  tokens: 4260
  tasks: 2
  commits: 2
  plan_head_before: 0080f5bd6cd5f8366f2eac7c0ec9d1f6130a56be
  plan_head_after: b2ff76a1a229c9b11e3089aa7ea6c888607b5db6

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Characterization test pinned to a real historical bug shape (ledger row fields from the actual incident), proven non-vacuous by a temporary source mutation rather than a revert, since there is no fix to revert for an already-correct path"

key-files:
  created: []
  modified:
    - tests/test_auto_polymarket_buyer.py
    - docs/LEDGER_ROUTING.md

key-decisions:
  - "No production fix was made. The audit's planning-time verdict held after a fresh re-grep and a fresh read-only live-data snapshot: only the NCAAB paper trial (already fixed) ever had both vulnerable conditions (scratch-root buyer + pick_id pre-filter) together."
  - "Mutation proof substitutes for a revert-based proof here, since this task adds no fix to revert — Mutation A (disable matchup_markets) and Mutation B (remove the paper-root ledger file from the index) each isolate one half of the claim and are reverted immediately after measurement."
  - "The live-data snapshot's open-row counts are reported as a range, not a single number — the scheduled daily job writes ledgers.db 4x/day, and three reads during this task returned 60, 85, and 94 open rows while the duplicate-group and empty-event_id counts stayed at 0 across all three. The settled-duplicate counts (80 groups, 241 rows) are historical and were stable."
  - "Both out-of-scope findings named in the plan's 'Adjacent observations' were additionally recorded in .planning/WINDOWS.md (deviation kind, phase 1) as a best-effort cross-phase record, left uncommitted along with this SUMMARY for the orchestrator's docs commit."

patterns-established:
  - "For an 'already correct, prove it stays correct' audit task, pin the real historical incident's exact field shapes (not a synthetic minimal case) so the test doubles as a readable incident record."

requirements-completed:
  - QUICK-261112-0li

coverage:
  - id: D1
    description: "The main buyer's identity dedup (matchup_markets) is pinned against the real 2026-11-07 NBA ORL@GSW total duplicate shape, on both the live and paper_auto_buyer ledger roots, at both the step-5 (no slug) and step-8 (resolved slug) call-site shapes, with two controls, and proven non-vacuous by mutation"
    requirement: QUICK-261112-0li
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_reforecast_total_on_moved_line_is_blocked_on_both_ledger_roots"
        status: pass
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_main_buyer_dedup_index_reads_the_shared_paper_ledger_root"
        status: pass
      - kind: other
        ref: "Mutation A (if False: in place of the matchup_markets check) -> 2 failed; Mutation B (paper-root ledger file removed from auto_ledger_files) -> 1 failed, 1 passed; auto_executor.py restored to zero diff after each, confirmed via git diff --quiet"
        status: pass
    human_judgment: false
  - id: D2
    description: "docs/LEDGER_ROUTING.md has a dated 'Dedup identity audit (2026-11-12)' section with the path inventory, verdicts, re-measured live snapshot, pinning tests, mutation outcomes, and three open follow-ups marked not-done"
    requirement: QUICK-261112-0li
    verification:
      - kind: other
        ref: "grep -c '## Dedup identity audit (2026-11-12)' docs/LEDGER_ROUTING.md == 1; grep -q matchup_markets / 0416f6963152426e / ux_ledger_active_contract_model / a9aac3cb all pass"
        status: pass
    human_judgment: false

# Metrics
duration: ~35min
completed: 2026-11-12
status: complete
---

# Quick Task 261112-0li: Dedup Identity Audit (Pins + Doc Record) Summary

**Pinned the main auto-buyer's game-market identity dedup with two mutation-proven regression tests against the real 2026-11-07 NBA Orlando Magic @ Golden State Warriors total duplicate, and recorded a dated audit in docs/LEDGER_ROUTING.md concluding no live path besides the already-fixed NCAAB paper trial ever had the pick_id-not-identity dedup bug.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 2/2 completed
- **Files modified:** 2 (`tests/test_auto_polymarket_buyer.py`, `docs/LEDGER_ROUTING.md`)
- **src/ changes:** none — `src/model_prediction/portfolio/auto_executor.py` is byte-identical to HEAD; it was only temporarily mutated twice during the mutation proof and restored via `git checkout --` each time, confirmed clean before and after.

## Accomplishments

- `test_main_buyer_dedup_index_reads_the_shared_paper_ledger_root` pins that `AutoPolymarketBuyer.paper_data_root` defaults to `PAPER_AUTO_BUYER_DATA_ROOT` — the exact condition (a scratch root the shared dedup index can't see) that caused the NCAAB paper-trial duplicate — now pinned shut for the main paper buyer, which actually records fills to that root.
- `test_reforecast_total_on_moved_line_is_blocked_on_both_ledger_roots` (parametrized over the live and `paper_auto_buyer` ledger roots) reproduces the real 2026-11-07 ORL@GSW total duplicate: a first fill (`aa1ce475e1b440a5`, under 222.5) blocks a re-forecast (`0416f6963152426e`, over 220.5) at both the step-5 (pre-slug-resolution) and step-8 (resolved slug) call-site shapes, plus two controls — a different game is not blocked, and a different `market_type` on the same matchup is not blocked.
- Mutation proof confirms both tests are load-bearing, not vacuous: disabling the `matchup_markets` check (`if False:`) made both parametrized cases fail (**2 failed**); removing the paper-root ledger file from `_build_bought_index`'s `auto_ledger_files` list made only the paper-root case fail (**1 failed, 1 passed**). `auto_executor.py` was restored to zero diff after each mutation.
- `docs/LEDGER_ROUTING.md` gained a `## Dedup identity audit (2026-11-12)` section: the 8-row path inventory (re-grepped at execution time, line numbers current), a re-measured read-only live-data snapshot, the two new pinning tests and their mutation outcomes, and three explicitly open (not fixed) follow-ups.
- Re-measured live-data snapshot matched the planning-time numbers closely: `ledgers.db` shows 0 open duplicate identity groups and 0 open rows with empty `event_id` (stable across three reads despite the open-row total itself moving — 60, 85, 94 — because the scheduled daily job writes this table 4x/day); 80 settled-duplicate groups totaling exactly 241 rows (235 from the 2026-10-21 `b77a563d` transition, 6 pre-index rows from Aug/Sep), zero created on/after 2026-11-01. The paper ledger's one real duplicate group (ORL@GSW total) and the live ledger's 0 duplicate groups both reconfirmed exactly as the plan described.

## Task Commits

Each task was committed atomically, by explicit pathspec only:

1. **Task 1: Pin the main buyer's identity dedup against the real 2026-11-07 NBA paper duplicate, on both ledger roots** — `7157037b` (test)
2. **Task 2: Prove the pins are non-vacuous by mutation, then record the audit in docs/LEDGER_ROUTING.md** — `b2ff76a1` (docs; the mutation proof itself produced no commit — the file was restored via `git checkout --` both times)

**Plan metadata:** not committed by this executor — STATE.md, ROADMAP.md, and this SUMMARY.md are left for the orchestrator's docs commit, per this task's explicit constraints.

## Files Created/Modified

- `tests/test_auto_polymarket_buyer.py` — added `PAPER_AUTO_BUYER_DATA_ROOT` to the import block (isort constant-before-class ordering), plus the two new tests described above (102 lines added).
- `docs/LEDGER_ROUTING.md` — added the `## Dedup identity audit (2026-11-12)` section (57 lines added), inserted after `### Phase 2 (full retirement) readiness` and before `## The four ledgers (2026-08-03 layout, historical)`.

## Decisions Made

See `key-decisions` in frontmatter. In brief: no production fix was warranted or made; mutation proof (not revert proof) was used since there is no fix to revert; the live-data snapshot reports open-row counts as an observed range rather than a single number because the table is actively written by the 4x/day scheduled job during this session; both out-of-scope findings were additionally logged to `.planning/WINDOWS.md` as a best-effort cross-phase record.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed. All four assertions in Task 1's new parametrized test passed on the first run against unmutated code; no assertion was weakened.

## Known Stubs

None.

## Threat Flags

None — no new surface was introduced. This task added tests and documentation only; all STRIDE register items (T-0li-01 through T-0li-04, T-0li-SC) were mitigated or accepted exactly as specified in the plan's `<threat_model>`.

## Out-of-Scope Findings for the Operator

Two findings surfaced during this audit sit outside this task's scope and need an explicit operator decision. Both are documented in `docs/LEDGER_ROUTING.md`'s new audit section and additionally logged as open entries in `.planning/WINDOWS.md`:

1. **`_build_bought_index`'s fail-open read errors.** Every source loop in `auto_executor.py::_build_bought_index` swallows read errors with a bare `except (...): pass` (e.g. lines ~1291, ~1318, ~1336, ~1453). An unreadable ledger therefore silently becomes an empty dedup index — fail-open, not fail-closed — and this sits directly on the live-money buyer path. `69856d6b` already closed the equivalent gap for the NCAAB trial's own reader, but that fix was never extended to the main buyer's own index. This is a different bug class from the `pick_id`-identity bug this task audited (reliability of the read, not correctness of the key), and needs a real operator decision before any fix, since "fail closed" here means a transient read error blocks every buy for every sport until resolved — a real live-money tradeoff, not a mechanical port of the NCAAB fix.
2. **241 historical settled identity-duplicate rows in `ledgers.db`.** 80 groups, 241 rows total: 235 were settled on 2026-10-21 during the `b77a563d` ledger transition ("supersede re-forecast picks instead of removing"), the other 6 are pre-index rows from August/September 2026. Zero were created in November — the live forecast and settle path is not currently producing them. The read side is already mitigated (`a9aac3cb`, "read-only ledger report that counts each settled pick once"), so no report currently double-counts these. Whether to clean them up is a separate operator decision, and cleanup is a governed ledger mutation only through `archive_settled_rows`/`remove_open_rows` (never raw deletion), per CLAUDE.md.

## Issues Encountered

- The shared checkout has another session actively committing to the same branch (`refactor/cleanup-2026-11-11`) during this task's execution window: three unrelated commits (`e866c954`, `ce4f288b`, `0a6e9d1e`) landed on `HEAD` between this task's two commits. None touch this task's two files or `src/model_prediction/portfolio/auto_executor.py` — verified via `git show --stat` on each. This means the `commits:` actuals figure measured by `git rev-list --count PLAN_HEAD_BEFORE..HEAD` would read **5**, not the **2** this task actually made; the frontmatter's `actuals.commits: 2` reports this task's own commit count, not the raw ancestor-range count, because the range is contaminated by a concurrent session's commits in this sequential (non-worktree-isolated) execution mode. Flagging this for the orchestrator: any automated same-instrument check on `commits:` for this SUMMARY should expect 2, and a raw `git rev-list --count` over this plan's before/after hashes will over-count by exactly the 3 foreign commits named above.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Phase 1 item 4 of the ledger-improvement plan is closed: the dedup-identity audit's verdict (no other live path has the NCAAB bug class) now has mutation-proven test coverage and a durable doc record.
- The two out-of-scope findings above remain open in `.planning/WINDOWS.md` for an operator decision; neither blocks this task's own completion.
- `src/model_prediction/portfolio/auto_executor.py` carries zero changes from this task — the live-money buyer's own dedup logic is untouched.

---
*Quick task: 261112-0li*
*Completed: 2026-11-12*

## Self-Check: PASSED

- FOUND: tests/test_auto_polymarket_buyer.py
- FOUND: docs/LEDGER_ROUTING.md
- FOUND commit 7157037b (ancestor of HEAD)
- FOUND commit b2ff76a1 (ancestor of HEAD)
- Targeted selection `-k "dedup or rebought or blocks_the_other_line or reforecast or shared_paper_ledger_root"`: 8 passed, 59 deselected
- `ruff check` / `ruff format --check` on tests/test_auto_polymarket_buyer.py: clean
- `git diff --quiet -- src/model_prediction/portfolio/auto_executor.py` exits 0 (unchanged)
- Doc gates: heading count 1; `matchup_markets`, `0416f6963152426e`, `ux_ledger_active_contract_model`, `a9aac3cb` all present
