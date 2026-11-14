---
quick_id: 261114-dsh
slug: drop-rebuild-dashboard-tab-archive-dated-reports
date: 2026-11-14
status: complete
---

# Drop the Rebuild Shadow dashboard tab, default the sport filter to scope, archive dated reports

Operator: "do everything and continue" -- the last two items of the complexity review
(5,000-line `dashboard.html`, 130 docs) scoped to what follows from the 2026-11-13 decision.

## Tasks

1. `dashboard.html`: remove the Rebuild Shadow tab (button, section, CSS, loader JS, tab hook,
   default-tab fallback). Keep the read-only `/api/rebuild/*` backend routes (research tests
   cover them). Sport filter: keep every sport (settled history stays visible) but check only
   the six production sports by default; fix two stale strings. The file carries another
   session's uncommitted hunks, so apply the transformation to HEAD and stage that blob.
2. `tests/research/test_rebuild_dashboard.py`: assert the tab is gone and no mutating rebuild
   endpoint is referenced.
3. `docs/`: move dated reports with no inbound links (census over CLAUDE.md, .claude, docs, src,
   tests, scripts, ops, config) into `docs/archive/reports/` with an index README. Leave anything
   referenced from `PROJECT_STATUS.md` / `INDEX.md` / `ROADMAP.md`, which other sessions have open.
4. Run the dashboard tests; verify any failure against a `git archive HEAD` export before
   touching it; commit; push; comment on PR 36.

Not done: merging PR 36 (blocked by the permission layer; operator's action), `scratch/`
(gitignored local files), the three dashboard tests that already failed at HEAD.
