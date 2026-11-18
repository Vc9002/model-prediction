---
quick_id: 261118-ke0
description: NCAAB resolver: stop UConn resolving to Central Connecticut Blue Devils
status: complete
completed: 2026-11-18
commits: 1
---

# Quick 261118-ke0: UConn no longer resolves to Central Connecticut

## Commit

| Commit | Scope |
| --- | --- |
| 0de74638 | `_resolve_ncaab_team` alias lookup + UConn aliases + regression test |

## What was wrong

`src/model_prediction/college_basketball_forward.py::_resolve_ncaab_team`. History and ESPN name UConn `UConn Huskies`. The alias table sent `uconn` to the target `connecticut`, which no name starts with, and the lookup then fell to `target in k` (substring) and returned the only name containing it, `Central Connecticut Blue Devils`. The slate-to-ledger path (`forecast_ncaab_sport`) therefore built the event with the wrong team, so Elo, trend, priors and the Main sample gate used Central Connecticut's numbers.

Found because the Best Pick Audit widget showed "1 UNMATCHED": the unmatched pick was `Central Connecticut Blue Devils @ Arizona Wildcats` (event 229051), ESPN's game is `UConn Huskies @ Arizona Wildcats`; v6 priced Arizona at 97.7% against a market price of 60%.

## Fix

- Alias branch: exact name first, then whole-word prefix (`target + " "`). The bare substring test is gone (comment states why). A miss falls through to the existing general matching.
- `uconn`, `connecticut`, `connecticut huskies` all target `uconn huskies`. Central Connecticut keeps its own aliases.

## Evidence

- Reproduced on the live history (804 teams, `E:\MP\data\processed\ncaab\games.jsonl`): `resolve("UConn","Huskies")` returned Central Connecticut Blue Devils.
- Round trip over every history name (full name, and school + one-word mascot): old resolver 1 miss (UConn), new resolver 0 misses; 0 regressions; no other input changes result. After the fix the live probe returns `UConn Huskies` for UConn/Connecticut inputs and `Central Connecticut Blue Devils` for Central Connecticut inputs.
- Regression test `test_uconn_never_resolves_to_central_connecticut`: passes now; run against the pre-fix module loaded from `git show HEAD:` (so no revert of the shared tree) it fails with `- UConn Huskies / + Central Connecticut Blue Devils`.
- `pytest tests/test_college_basketball_forward.py tests/test_ncaab_main_totals.py tests/test_ncaab_tier1_guardrails.py -o addopts='' -q`: 31 passed. `ruff check` and `ruff format --check` clean on both touched files. No daily run was active during the edit.

## Blast radius (read-only, NOT changed by this task)

10 ledger games carry "Central Connecticut". Checked against ESPN: 5 are real Central Connecticut games (2026-11-02 @ Temple, 11-06 @ Syracuse, 11-08 @ Dartmouth, 11-13 @ Sacred Heart, 11-15 vs Quinnipiac); 5 are UConn games logged under the wrong name:

| Game date (UTC start) | ESPN game | Ledger label | Last row |
| --- | --- | --- | --- |
| 2026-11-03 | Gardner-Webb @ UConn | Gardner-Webb @ Central Connecticut | settled win, spread, +1.92U (model 99.9%) |
| 2026-11-07 | UConn @ Michigan | Michigan @ Central Connecticut (home/away also reversed) | settled loss, moneyline, -1.00U |
| 2026-11-10 | Wagner @ UConn | Wagner @ Central Connecticut | settled loss, total over, -1.56U |
| 2026-11-14 | Ohio State @ UConn | Ohio State @ Central Connecticut | settled loss, moneyline, -1.00U |
| 2026-11-19 | UConn @ Arizona | Central Connecticut @ Arizona | OPEN, moneyline home, model 97.7% vs 60% |

About 84 ledger rows (incl. superseded duplicates). The ledger is the append-only evidence store; those rows were not edited. Operator decision pending: whether to archive/void them and exclude them from model evidence (ledger mutation only through `archive_settled_rows` / `remove_open_rows`). The open Arizona row is repriced by the next daily run now that the fix is on disk.

## Open question (not fixed here)

The 2026-11-07 row has home and away reversed relative to ESPN (`UConn @ Michigan` logged as `Michigan @ Central Connecticut`). The resolver fix does not touch slate orientation; that comes from the Polymarket event title/side parsing in `forecast_ncaab_sport` and needs its own look.
