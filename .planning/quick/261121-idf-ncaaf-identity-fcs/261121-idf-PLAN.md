---
phase: quick-261121-idf
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/features/cfb_team_identity.py
  - src/model_prediction/features/cfb_features.py
  - src/model_prediction/features/base.py (no change expected)
  - src/model_prediction/models/college_football.py
  - src/model_prediction/models/cfb_structural_v4.py
  - tests/test_cfb_team_identity.py
autonomous: true
requirements:
  - QUICK-261121-idf
---

# NCAAF serving path: team identity and per-season FBS/FCS class (cfb-structural-v5)

Operator-approved ("execute all"), from the 2026-11-20 NCAAF review. Worktree `E:\MP-wt\idf`,
branch `ncaaf-261121-idf`. `cli/forecast.py`, `.planning/STATE.md`, `docs/ROADMAP.md`, `docs/DEBUG.md`
are not touched.

## Verified facts (before any edit)

- 2026 history splits teams across display names: `App State Mountaineers` 7 / `Appalachian State
  Mountaineers` 3; `San José State Spartans` 7 (valid UTF-8 `c3 a9`, only the console mis-renders it) /
  `San Jose State Spartans` 4; `Hawai'i Rainbow Warriors` 5 / `Hawaii Rainbow Warriors` 5.
  `cfb_features.py` matches history rows by exact display name, so each half is a different team.
- `resolve_team` already aliases all three; 351 of 497 history names do not resolve (FIU as `Florida
  International Panthers`, `Missouri State Bears`, `Sacramento State Hornets`); `North Dakota State Bison`
  resolves as FCS but plays FBS-only (feed 80) games in 2026.
- Unresolved fallbacks disagree: team -> Mid-American/G6 (:301-302), Elo opponent -> Mid-American (:363),
  efficiency opponent -> FBS Independents +2.0 (:398). Against the v6 backfill's game_class, the current
  rule labels 1,703 of 1,782 FCS team-seasons as FBS.
- `_load_cfb_history` drops `home_team_id`, `away_team_id`, `feed_groups`. Legacy rows (no feed groups)
  are 97% FBS-FBS but 208 are not, so a missing feed class is never read as FBS.

## Tasks (one commit each)

A. **Identity.** `features/cfb_team_identity.py`: canonical team key per display name, reusing v6's
   `TeamResolver` (resolve_team canonical -> shared ESPN id -> `ID:` -> `NAME:` normalised), built from
   history rows before the decision time; slate teams map by name, else by the slate event's ESPN team id.
   `cfb_features.py` matches history, Elo, efficiency, rest and opponent lookups by key; raw display names
   stay in outputs and the tempo-team sets. `_load_cfb_history` keeps team ids and feed groups;
   `UpcomingCFBGame` carries optional team ids from the ESPN event.
B. **Per-season class.** Label each (team key, season) FBS/FCS from feed groups before the decision time
   (81 only => both FCS; 80 only => both FBS; 80+81 => the side opposite a labelled team, else the
   resolver tier breaks the tie), then previous season, then `resolve_team` tier, else UNKNOWN. UNKNOWN is
   treated as not-FBS (tier FCS, so `is_fbs_vs_fcs` is True) and logged. Drives team tier and
   `is_fbs_vs_fcs`.
C. **One fallback for opponent offsets.** Elo and efficiency opponent conference come from the same
   function as the team's own: resolved conference when the class agrees, `FCS` when the class is FCS or
   UNKNOWN, `Mid-American` (the existing team fallback) for an FBS-class team missing from the table.
   Committed last and measured separately because the efficiency adjustment's sign runs opposite to its
   comment (finding, not fixed here).

No fitted constant, calibration table or artifact changes.

## Verification

- Regression tests: split names collapse to one team state; unknown-class opponent => `is_fbs_vs_fcs`
  True; class from feed groups beats a stale resolver tier; PIT (a class row after the decision time is
  ignored). Mutation-prove each by reverting the fix.
- Targeted runs: tests/test_cfb_serving_parity.py, test_cfb_structural_v5.py, test_cfb_structural_v4.py,
  test_college_football.py, the new test file. Baseline at HEAD: 59 passed.
- As-of class accuracy per 2026 week against the v6 backfill game_class (research file, validation only).
- Impact ablation (HEAD, A, A+B, A+B+C) on the 2026-11-21/22 slate and a sample of settled 2026 games:
  probabilities moved and by how much, games newly `is_fbs_vs_fcs`.
- `ruff check` on touched files.
