---
quick_id: 261114-dsh
status: complete
date: 2026-11-14
commit: b29e3c14
---

# Summary: rebuild tab gone, sport filter defaults to scope, eight dated reports archived

## What changed

- `b29e3c14` `dashboard.html` 5023 -> 4830 lines. Rebuild Shadow tab, section, CSS and
  loader JS removed; `currentActiveTab` falls back to `today`; the "Run Main Forecast"
  confirmation and the bootstrap prompt no longer name esports/soccer/tennis. "Ledger sports
  shown" keeps all 19 sports (history stays visible) with only NBA/NCAAF/NFL/MLB/WNBA/NCAAB
  checked by default and the rest under a "Switched off 2026-11-13 (history only)" label.
  Staged as HEAD + transformation (blob) because the working copy carries another session's
  uncommitted hunks; both sets of edits remain in the working tree.
- `tests/research/test_rebuild_dashboard.py` asserts the tab is absent and no mutating
  `/api/rebuild/*` endpoint is referenced. Backend read-only rebuild routes untouched.
- `docs/archive/reports/` gains eight dated reports and an index README; top-level docs 51 -> 43.
  Only files with zero inbound links outside themselves moved (the two forensic reports link
  each other and moved together).

## Evidence

- `tests/test_dashboard_html.py`, `test_dual_auto_buyer_dashboard.py`,
  `test_production_evidence_api.py`, `tests/research/test_rebuild_dashboard.py`: 51 passed.
  3 failed, all three also fail on a `git archive HEAD` export (pre-existing, unrelated):
  `test_auto_buyer_consolidated_tab_markup_and_routing` (pins the old consolidated auto-buyer
  tab that was split into live/paper tabs; the dual-tab test passes),
  `test_current_configured_artifacts_are_valid_after_rename_and_config_fix`,
  `test_http_payload_renders_nba_verified_with_missing_ledger_dashes`.
- Staged `dashboard.html` has zero occurrences of "rebuild"; ruff clean.

## Left for the operator

- Merge PR 36 (the merge command was denied by the permission layer in this session).
- The three pre-existing dashboard/evidence test failures above.
- `scratch/` (211 MB, gitignored).
