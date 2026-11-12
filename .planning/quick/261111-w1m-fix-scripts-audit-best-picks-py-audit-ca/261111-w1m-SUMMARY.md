---
phase: quick-261111-w1m
plan: 01
subsystem: forecast-model-picks
tags: [sqlite, audit, best-picks, verified-quote, ledgers.db]

# Dependency graph
requires: []
provides:
  - "scripts/audit_best_picks.py: every verdict in audit_candidate is recomputed live from ledgers.db; no sport/market literal overrides remain"
  - "VERIFIED_QUOTE_PROVENANCE gate: only settled rows whose decision_payload_json.market_quote_provenance == decision_time_executable_quote count toward a verdict; the candidate's own quote must also be verified before CONFIRMED_BEST is reported"
  - "tests/test_audit_best_picks.py: 12 synthetic-ledger tests pinning the verified-quote gate, tier dedup, malformed-payload handling, directional-total evidence, and removal of every hardcoded override"
  - "docs/BEST_PICKS_AUDIT_FRAMEWORK.md: reconciled (single SOP section, sample output under section 4) and rewritten to match the live verified-quote logic with a dated recomputed snapshot"
affects: [forecast-model-picks, operate-model-dashboard]

# Actuals (#2632)
actuals:
  tokens: 22303
  tasks: 3
  commits: 4
  commits_measured_note: "git rev-list --count PLAN_HEAD_BEFORE..HEAD reports 6 on this shared branch (SEQUENTIAL execution, no worktree isolation); 2 of those 6 (37084668, 4dd3034e) are an unrelated concurrent session's NFL weather commits that landed interleaved with this plan's commits on refactor/cleanup-2026-11-11. This plan's own commits are exactly 4: 4c439391, 3426c06e, c880b427, 3ca0a4c5."

tech-stack:
  added: []
  patterns:
    - "Verified-quote evidence gate: BucketStats{all_rows, verified} dataclass splits settled-row evidence by whether the row's own market_quote_provenance was a decision-time executable quote; only .verified counts toward a verdict, .all_rows is context only"
    - "Read-only sqlite connection via mode=ro URI for any report script that must never mutate or create ledgers.db"

key-files:
  created:
    - tests/test_audit_best_picks.py
  modified:
    - scripts/audit_best_picks.py
    - docs/BEST_PICKS_AUDIT_FRAMEWORK.md

key-decisions:
  - "Totals (over/under) are judged on the (sport, direction, bucket) record, never the pooled over+under record, so one direction's losses can no longer be masked by the other direction's wins (or vice versa)."
  - "A mechanical bucket verdict of CONFIRMED_BEST is withheld to QUALIFIED_MODERATE when the candidate's own current quote is not itself decision_time_executable_quote; the mechanical bucket_verdict is still reported separately so the evidence is never hidden, only the recommendation is downgraded."
  - "docs/BEST_PICKS_AUDIT_FRAMEWORK.md's Section 3 scorecard is now a dated, recomputed snapshot with an explicit recompute-before-citing instruction, rather than permanently fixed figures."

requirements-completed: [QUICK-261111-w1m]

coverage:
  - id: D1
    description: "audit_candidate counts only verified-quote rows toward any verdict and withholds CONFIRMED_BEST from candidates whose own quote is unverified"
    requirement: "QUICK-261111-w1m"
    verification:
      - kind: unit
        ref: "tests/test_audit_best_picks.py#test_unverified_bucket_evidence_stays_qualified_moderate"
        status: pass
      - kind: unit
        ref: "tests/test_audit_best_picks.py#test_verified_bucket_and_verified_candidate_confirms_best"
        status: pass
      - kind: unit
        ref: "tests/test_audit_best_picks.py#test_verified_bucket_with_unverified_candidate_quote_is_withheld"
        status: pass
    human_judgment: false
  - id: D2
    description: "Every hardcoded sport/market override removed from audit_candidate; totals judge on the direction bucket, not the pooled total record"
    requirement: "QUICK-261111-w1m"
    verification:
      - kind: unit
        ref: "tests/test_audit_best_picks.py#test_nba_spread_bucket_was_hardcoded_confirmed_now_live_trap"
        status: pass
      - kind: unit
        ref: "tests/test_audit_best_picks.py#test_nba_total_direction_bucket_not_pooled_total"
        status: pass
      - kind: other
        ref: "grep -cE '\"(nba|nfl|ncaab)\"' scripts/audit_best_picks.py == 0"
        status: pass
    human_judgment: false
  - id: D3
    description: "Live read-only run against the real ledger exits 0 and no CONFIRMED_BEST pick lacks a verified quote"
    requirement: "QUICK-261111-w1m"
    verification:
      - kind: integration
        ref: "python scripts/audit_best_picks.py --all-open (exit 0, verified at execution time)"
        status: pass
    human_judgment: false
  - id: D4
    description: "docs/BEST_PICKS_AUDIT_FRAMEWORK.md reconciled (one SOP section, sample output under section 4) and rewritten to match the live logic with a dated snapshot"
    requirement: "QUICK-261111-w1m"
    verification:
      - kind: other
        ref: "Task 3 structural gates (duplicate-section grep, awk section-order check, git log subject check) — all passed, see below"
        status: pass
    human_judgment: false

duration: ~30min
completed: 2026-11-12
status: complete
---

# Quick Task 261111-w1m: Live verified-quote best-picks audit Summary

**Removed every hardcoded sport/market verdict override from `scripts/audit_best_picks.py`, gated every verdict on `decision_time_executable_quote` evidence, and reconciled + rewrote `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` to match.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-11-11 (session start, reading plan/context)
- **Completed:** 2026-11-12T04:32:31Z
- **Tasks:** 3 (3 of 3 complete)
- **Files modified:** 3 (`scripts/audit_best_picks.py`, `tests/test_audit_best_picks.py` created, `docs/BEST_PICKS_AUDIT_FRAMEWORK.md`)

## Accomplishments

- **Verified-quote gate end-to-end (Task 1):** Added `VERIFIED_QUOTE_PROVENANCE = "decision_time_executable_quote"` and the Pillar-1 threshold constants (`MIN_GRADED`, `BEST_MIN_WIN_RATE`, `BEST_MIN_ROI`, `TRAP_WIN_RATE`, `TRAP_ROI`). Split `EdgeTierStats` evidence into a `BucketStats{all_rows, verified}` dataclass; the settled-row loader now extracts `market_quote_provenance` from `decision_payload_json` via `json_valid`-guarded SQL (malformed JSON counts as unverified, never raises). Both sqlite connections now open `mode=ro` so the report can never mutate `ledgers.db`. `audit_candidate` takes a `quote_provenance` argument and withholds `CONFIRMED_BEST` to `QUALIFIED_MODERATE` whenever the candidate's own current quote is not itself verified — the mechanical `bucket_verdict` is still returned separately so the underlying evidence is never hidden. Split `audit_open_picks` into `collect_open_pick_audits` (data) and a thin print layer; every printed pick now shows its verified W-L, verified-of-graded count, verified WR/ROI, and quote provenance.
- **Removed every hardcoded override (Task 2):** Deleted the entire sport-literal override region (`# 1. Check Totals directional bias`, `# 2. Check Spread traps`). Totals (over/under) now select evidence from the `(sport, direction, bucket)` dicts instead of the pooled `(sport, market, bucket)` dicts, so a profitable direction can no longer be drowned by — or hide behind — its opposite direction. Every flag is built from live `BucketStats` numbers; zero sport-name literals remain in the script (confirmed via grep).
- **Reconciled and rewrote the framework doc (Task 3):** Step A reconciled another session's uncommitted doc edits (kept all their content: effective-date bump, NCAAB spread snapshot subsection, operator live exception, per-audit display requirements, `min_team_games: 8` note, expanded SOP, section 6 dedup protocol) while fixing structure — moved the sample terminal output back under section 4, removed the duplicate trailing section 5, removed the stray duplicate bullet. Step B rewrote the doc's content to match the new logic: Pillar 1 states the exact literal rules the script implements; Section 3's static scorecard is replaced by a dated, live-recomputed snapshot table plus a "Labels that did not replicate" comparison table; Section 4 documents `--pick-id` and shows a real (non-invented) terminal excerpt; Section 5 ties "best pick" to the script's final verdict.

## Live verdict counts (before / after)

- **Planning-time baseline** (hardcoded overrides, mixed verified/unverified counting): **9 CONFIRMED_BEST, 81 DOWNGRADE_TRAP, 8 QUALIFIED_MODERATE**.
- **Live run after this fix** (`python scripts/audit_best_picks.py --all-open`, verified-quote gate, no overrides): **5 CONFIRMED_BEST, 38 DOWNGRADE_TRAP, 55 QUALIFIED_MODERATE**. No `CONFIRMED_BEST` pick lacks a `decision_time_executable_quote`.

## Dated snapshot as-of time

Section 3's recomputed snapshot table in `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` is stamped **2026-11-11, 11:28 p.m. ET**, computed live with `HistoricalAuditEngine` against canonical `ledgers.db`. The doc explicitly instructs recomputing it before citing any figure, since settled decisions change daily.

## Task Commits

Each task was committed atomically, with pathspec-scoped `git commit -- <paths>` so nothing else in the dirty shared tree was swept in:

1. **Task 1: Verified-quote gate end-to-end** - `4c439391` (fix) — `scripts/audit_best_picks.py`, `tests/test_audit_best_picks.py` (Tests 1-6)
2. **Task 2: Remove hardcoded overrides; live directional evidence** - `3426c06e` (fix) — `scripts/audit_best_picks.py`, `tests/test_audit_best_picks.py` (Tests 7-12)
3. **Task 3 Step A: Reconcile uncommitted doc edits** - `c880b427` (docs) — `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` only
4. **Task 3 Step B: Align doc with live logic** - `3ca0a4c5` (docs) — `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` only

**Plan metadata:** not committed by this agent — SUMMARY.md/STATE.md/ROADMAP.md commits are handled by the orchestrator per this task's explicit constraints.

_Note: An unrelated concurrent session landed two of its own commits (`37084668`, `4dd3034e`, NFL weather work) interleaved between this plan's Task 1 and Task 2 commits on the shared `refactor/cleanup-2026-11-11` branch (sequential execution, no worktree isolation available this run). Verified via `git show --stat` on each of this plan's four commits that each touches only its own intended file(s)._

## Files Created/Modified

- `scripts/audit_best_picks.py` - Verified-quote evidence gate, `BucketStats`, read-only connections, directional evidence selection for totals, removed every hardcoded override, `collect_open_pick_audits`/`audit_open_picks` split
- `tests/test_audit_best_picks.py` (created) - 12 synthetic-ledger tests (verified-quote gate, tier dedup, malformed payload, end-to-end open-picks run, removed overrides, directional evidence, negative-edge rule)
- `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` - Reconciled structure (one SOP section, sample output under section 4) then rewritten to match the live verified-quote logic, dated recomputed snapshot, "Labels that did not replicate" table

## Decisions Made

- Totals judge on the `(sport, direction, bucket)` record rather than the pooled total record (kept throughout Tasks 1-2 and documented in the doc's Pillar 2).
- The candidate-quote gate withholds `CONFIRMED_BEST` but always surfaces the mechanical `bucket_verdict` separately — evidence is never hidden, only the final recommendation is conservative.
- Kept the doc's NCAAB spread 5.0-7.9% subsection's existing content (11-2 diagnostic record, operator live exception) unchanged except for one added sentence noting the bucket has 0 verified rows under the literal rule, per the plan's Task 3 Step B item 9.

## Deviations from Plan

None - plan executed exactly as written. All three tasks, their RED/GREEN TDD sequencing, and the reconcile-then-rewrite doc ordering in Task 3 matched the plan's `<action>` blocks.

## Issues Encountered

- The first Task 1 commit message accidentally included a literal `-- scripts/audit_best_picks.py tests/test_audit_best_picks.py` line inside the commit body (a copy-paste artifact from drafting the pathspec alongside the heredoc message). Purely cosmetic — `git show --stat` confirms the commit touched only the two intended files — but noted here for transparency. Not corrected via amend per the "always create new commits, never amend" rule; a cosmetic-only message defect did not justify a fixup commit.
- `git rev-list --count` between this plan's pre-commit HEAD and the final HEAD reports 6 commits, not 4, because this ran in `SEQUENTIAL` mode on the shared `refactor/cleanup-2026-11-11` branch (worktree isolation was unavailable this run) and another session's two unrelated NFL-weather commits landed interleaved with this plan's commits. The `actuals.commits` field above is set to 4 (this plan's own commits only) with the discrepancy explained in `commits_measured_note`, rather than reporting the contaminated raw count as if it were this plan's cost.

## Stale pointers for the operator to clear

Per this task's constraints, `docs/handoffs/HANDOFF.md`, `docs/PROJECT_STATUS.md`, and `docs/ROADMAP.md` were left untouched (they carry other sessions' uncommitted edits). All three now contain pointers describing the *old, broken* state of `scripts/audit_best_picks.py` that this plan just fixed:

- **`docs/handoffs/HANDOFF.md` item 2** ("Best-picks audit script (offered, not done)") describes exactly the hardcoded-override problem this plan fixed. Safe to delete this item (or the whole file, if items 1/3-6 are also resolved).
- **`docs/PROJECT_STATUS.md` lines ~21-22** ("Best-picks audit labels are unreliable (hardcoded overrides in `scripts/audit_best_picks.py`); recompute from `ledgers.db` first.") is now stale — the overrides are removed and every verdict is recomputed live.
- **`docs/ROADMAP.md` line ~242** (the "Best-picks audit" bullet under the 2026-11-10 to 2026-11-11 entry, noting hardcoded CONFIRMED_BEST labels that don't replicate and "Fixing the script is open work (see HANDOFF)") is now stale — the fix referenced is this plan.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `scripts/audit_best_picks.py` and `docs/BEST_PICKS_AUDIT_FRAMEWORK.md` are now mutually consistent and both describe only live, recomputed ledger evidence.
- The three stale pointers above should be cleared by the operator in a separate pass (not done here, since those files carry unrelated uncommitted work from other sessions).
- No blockers for other in-flight work on `refactor/cleanup-2026-11-11`.

## Self-Check: PASSED

- FOUND: scripts/audit_best_picks.py
- FOUND: tests/test_audit_best_picks.py
- FOUND: docs/BEST_PICKS_AUDIT_FRAMEWORK.md
- FOUND (ancestor of HEAD): 4c439391, 3426c06e, c880b427, 3ca0a4c5

---
*Phase: quick-261111-w1m*
*Completed: 2026-11-12*
