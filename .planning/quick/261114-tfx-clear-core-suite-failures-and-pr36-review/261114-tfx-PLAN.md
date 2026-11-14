---
quick_id: 261114-tfx
slug: clear-core-suite-failures-and-pr36-review
date: 2026-11-14
status: complete
---

# Clear the 13 core-suite failures and apply the PR 36 review findings

Operator: "do everything" after the scope cut (261113-smp). Two blocks of work that make
further simplification safe: a green core suite, and the review of PR 36.

## Tasks

1. Reproduce the handoff's 12 failing tests (13 with one the scope cut surfaced) with
   `--tb=line`, cluster by failing site, probe each cluster for the full result dict, and
   classify stale-fixture vs real-bug before editing either side.
2. Fix fixtures where production rules are deliberate and newer than the test; fix code
   where the test is the contract (NBA replay missing the engine's thin-sample widening).
3. Move the data-dependent tennis settled-ledger evaluation to `tests/research/` (tennis is
   out of scope; the live calibration slope drifted to ~0.80) and skip it with the reason.
4. PR 36 review (`/code-review medium 36`): gate the fixed NCAAF/NBA/NFL tasks on the sport
   switch; status "disabled" not "off_season"; one skipped-result helper; drop dead defaults;
   scope test pins the flag not the season; CI runs `tests/research`; CLAUDE.md documents the
   esports/KBO/NPB ratings backfill needed before any re-enable.
5. ruff + targeted runs only; commit; push; comment on PR 36.
