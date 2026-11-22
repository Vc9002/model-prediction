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
| 2026-11-07 | UConn @ Michigan (neutral site) | Michigan @ Central Connecticut | settled loss, moneyline, -1.00U |
| 2026-11-10 | Wagner @ UConn | Wagner @ Central Connecticut | settled loss, total over, -1.56U |
| 2026-11-14 | Ohio State @ UConn | Ohio State @ Central Connecticut | settled loss, moneyline, -1.00U |
| 2026-11-19 | UConn @ Arizona | Central Connecticut @ Arizona | OPEN, moneyline home, model 97.7% vs 60% |

About 84 ledger rows (incl. superseded duplicates). The ledger is the append-only evidence store; those rows were not edited. Operator decision pending: whether to archive/void them and exclude them from model evidence (ledger mutation only through `archive_settled_rows` / `remove_open_rows`). The open Arizona row is repriced by the next daily run now that the fix is on disk.

## Home/away on the 2026-11-07 row: not a bug

ESPN lists `UConn @ Michigan` for that game but the slate tagged Michigan away. The game was a neutral-site Hall of Fame Series game at TD Garden (ESPN `neutralSite=True`), where either label is arbitrary. Neutral detection compares resolved name pairs, and the slate and ESPN both resolved UConn the same wrong way, so the pair still matched and the game was treated as neutral. The other four UConn games have ESPN labels matching the ledger (non-neutral).

## Are other teams affected? (audit, read-only)

Ledger vs ESPN, all 772 distinct NCAAB game labels (2026-11-02..11-19): 715 match both teams exactly; 12 are the right teams with a different ESPN tip time or listing order; 45 label rows have exactly one team different from ESPN.

- **Collision onto a real D1 team: UConn only** (5 games, fixed). No other D1-to-D1 collision exists in the ledger or in the 615-team simulation below.
- **Collisions onto a non-D1 school, 3 games, all before 11-09:** UMass -> `UMass Boston Beacons` (11-03 vs Troy, 11-06 vs Boston College) and Rutgers -> `Rutgers-Camden RUTGERS-CAMDEN` (11-07, New Haven @ Rutgers). Those 3 games hold 16 settled rows (7 Main, 9 Flat), net -13.16U on 23.28U. With the UConn games the mislabeled settled rows are 30, net -20.06U.
- **Unresolved names, 37 label rows, all dated 11-03..11-08:** D1 teams kept as a raw slate string (`Alabama`, `K-State`, `Mich. State`, `Notre Dame`, `Cornell`, `Delaware`, `California`, `NM State`, `UNCW`, ...) so the model had no rating history for them, plus spelling variants of non-D1 schools (`Concordia-Chicago`). None after 11-08, so the alias additions since then work.
- **Simulation:** every D1 team on ESPN's 11-02..11-26 scoreboards (615 teams, 1,779 inputs). School + mascot form (the slate's normal form): 1 wrong before this fix (UConn), 0 now. Remaining wrong results come only from school-only or abbreviated inputs, and every collision lands on a non-D1 school (`Rutgers`, `UMass`, `Oklahoma St`, `St Mary's`, `Wilson`, `Florida Memorial`, `Pacific Union`); since 11-15 `decline_non_d1` turns those into no-picks, so the cost is lost coverage on real D1 games (Rutgers, UMass), not a mispriced pick. The `umass` alias has the same defect as `uconn` did: its target `umass minutemen` is not a history name (history says `Massachusetts Minutemen`). Not changed here.
- **Orientation:** of the 715 matched games, 694 have the same away/home order as ESPN, 20 are flipped at ESPN-neutral sites (arbitrary), 1 flipped at a home-court site (2026-11-08 Evansville @ Southern Indiana, event 203192).
- **Real-money matcher (`data_sources/polymarket_execute.py::_team_name_matches`), not changed:** `UConn Huskies` vs side `Central Connecticut` / `Central Connecticut State` returns True (the final distinctive-token clause matches the alias target `connecticut`), and `UMass` vs `UMass Boston` returns True. Both need two near-identical teams in one event to bite, so the exposure is small, but it is the gated real-money path and was left for an explicit instruction. `Rutgers` vs `Rutgers-Camden`, `Oklahoma State` vs `Oklahoma Panhandle State`, `Arizona` vs `Arizona State`, `Michigan` vs `Michigan State` correctly return False.
- **Paper auto-buyer:** no buy touched any mislabeled game (its only Central Connecticut rows are real Central Connecticut games).

## Follow-up (2026-11-18, operator approved): 30 contaminated settled rows archived

The operator said yes after the export was reviewed. `PickLedger.archive_settled_rows` ran under SQLite authority with the daily writer lock held, a SQLite backup first (`E:/model-prediction-runtime/ledgers/ledgers.db.pre-collision-archive-20261118T222558Z.bak`), and the exact reviewed pick_id set and totals asserted (30 rows: 12 Main, 18 Flat, net -20.06U on 45.73U staked; UConn 14 rows -6.90U, UMass/Rutgers 16 rows -13.16U).

- Rows file and manifest: `data/archive/2026-11-18-ncaab-team-name-collision-rows/` (`settled_rows.jsonl`, `MANIFEST.json`).
- Verified against the backup: only those 30 rows changed (flat settled 13,662 -> 13,644, main settled 9,910 -> 9,898; archived 0 -> 18 and 0 -> 12). They stay in `ledger_records` as `status=archived`, each with an `archive` event, and 30 `settled_pick_archived` audit events carry the full rows in `data/events.jsonl`.
- Not archived: the 108 superseded rows and the one open row on the same games. The open Arizona row (event 229051) was repriced by the 17:00 ET daily run as `UConn Huskies @ Arizona Wildcats`, model 56.9% vs market 50% (was Central Connecticut at 97.7% vs 60%).
- Gotcha: the code default for `MODEL_PREDICTION_LEDGER_AUTHORITY` is `xlsx`; an ad-hoc ledger mutation must set `sqlite` explicitly or it reads the wrong authority.
