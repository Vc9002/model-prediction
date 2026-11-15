---
quick_id: 261115-g9j
description: NCAAB follow-up fixes (non-D1 no-pick rule, NFL consensus root, all-league daily restore)
status: complete
completed: 2026-11-15
commits: 3
plan_head_before: f451f80224534cc866ae4e2cfa7d066a987a68c5
plan_head_after: 0edd81e1846a3bbf2a7342c0f97ecea00637c24b
---

# Quick 261115-g9j: NCAAB follow-up fixes

## Commits

| Commit | Scope |
| --- | --- |
| fc7ff8b8 | Item 1: non-D1 no-pick rule |
| 1c154c4c | Item 2: NFL closing consensus pinned end to end (test only, no source change) |
| 0edd81e1 | Item 3: daily restore for every enabled league |

## Item 1: non-D1 no-pick rule (post-hoc, judge forward only)

`college_basketball_forward.py`. An event is declined when either resolved team is not in the served v6 spread model's `team_priors` (the conference map the model and the v7 shadow already use as "D1"; no second definition). Reason code `NO_CALL_NON_D1` (the v7 shadow's string), constant `DECLINE_NON_D1 = True`, config switch `models.NCAAB.decline_non_d1`. The 20-24 RMSE / +8..+14 bias evidence came from the 1m7 research cohorts, so the rule is post-hoc; the comment says so.

- Every slot of the event (moneyline, spread, total, whichever it lists) goes into `skipped` and `declined_slots` at the point the event is detected, before any later per-market `continue`. A restore therefore cannot reopen an earlier row for the event, whatever else would have skipped the market.
- The markets are still predicted (they are only never planned), so candidate-evidence packets keep the probabilities for judging the rule forward.
- Fail closed: a missing or empty conference map declines everything.
- The optional "< 3 games" rule was not added. The existing `min_team_games` skip is untouched except that it no longer double-reports an event the non-D1 rule already declined.
- Fixtures: the fake spread models in `test_college_basketball_forward.py`, `test_ncaab_main_totals.py` and `test_ncaab_tier1_guardrails.py` now carry a `team_priors`; one test that uses the real model with fixture team names sets `decline_non_d1: False` (it is about model output).

## Item 2: NFL closing consensus: already fixed, now pinned (no source change)

The Tier 1 summary said `closing_espn_consensus(data_root, ...)` in `_settle_all_unsettled` uses the stale legacy root. It does not: `closing_espn_consensus -> consensus_history -> espn_odds_path`, and `espn_odds_path` has applied `rerouted_data_root` since commit a19d42ce (2026-11-13), the single choke point for every reader and writer. A second reroute in `settle.py` would be a no-op, so I did not add one. Delivered instead: `tests/test_nfl_settlement_consensus.py`, which captures with the legacy `data/` root, settles through `_settle_all_unsettled` with a legacy-root config and the runtime root set, and asserts `closing_consensus_probability` is filled. Red proof: removing the reroute inside `espn_odds_path` makes it fail; restoring makes it pass.

## Item 3: daily restore for every enabled league

`cli/commands.py`, `cli/daily.py`. Daily clears every league's not-yet-started open rows in Main and Flat and used to restore nothing (NCAAB was restored in the previous task). Now:

- Capture before the clear: `_restorable_open_rows(ledger, config)` takes open rows of every league whose own `models.<LEAGUE>` entry exists and passes `is_sport_daily_enabled`, plus NCAAB unconditionally (it was restored before this widened).
- Restore after the research pool, before the `candidates` pop, excluding (a) every slot any result reports in `declined_slots` and (b) every prior-row slot the run evaluated: `_evaluated_slots` reads `candidates` (learned moneyline results) and `priced_contracts` (spread/total tasks), which carry `event_id` and `market_type`. The league comes from the prior row.
- Result: only slots the run never evaluated are reopened (an errored task, a failed slate fetch, a market that left the slate). Restore's existing guards still apply (slot already open/settled, and only rows this refresh actually retired).
- The count goes to a new `step2_3_unreplaced_restored` report key (tier -> count, all leagues) instead of `forecast_result["ncaab"]["unreplaced_restored"]`.
- `_open_ncaab_rows` and `_restore_unreplaced_ncaab` stay importable (`run_forecast` and tests use them); `_restore_unreplaced_ncaab` is now an alias of the league-agnostic `_restore_unreplaced`.

**What is and is not restored, for the operator:**

- Restored on Main and Flat: any prior open row of an enabled league (NBA, NFL, NCAAF, NCAAB at present) whose (event, market) the run did not evaluate. For NCAAB specifically, a slot the model simply stopped favouring is still restored (that is the existing behaviour; only gate-declined slots are excluded).
- Not restored: an evaluated-but-not-emitted slot for NBA/NFL/NCAAF (a gate, ban, or no-call decision). Only NCAAB reports `declined_slots`; the other sports expose evaluation through `candidates`/`priced_contracts` only, so I treated "evaluated" as "decided". Consequence to know about: a non-NCAAB task that evaluates a contract and drops it for any reason keeps the pick retired, which is the same as today's behaviour for those leagues.
- A task that exposes neither `candidates` nor `priced_contracts` (for example an unmigrated sport result shape) evaluates nothing as far as restore can tell, so its unreplaced picks would be reopened. I did not check every enabled sport's result shape beyond NBA/NFL/NCAAF spread/total and the learned moneyline slate.
- Not restored: switched-off sports (their rows are still cleared by the daily clear and not reopened, as before), and sub-leagues with no `models.<LEAGUE>` entry of their own.

## Tests (targeted, `-o addopts='' -q`)

Passing: `test_college_basketball_forward.py`, `test_ncaab_main_totals.py`, `test_ncaab_tier1_guardrails.py` (19), `test_ncaab_restore_wiring.py` (7), `test_nfl_settlement_consensus.py` (1), `test_ncaab_settlement_clv.py`, `test_ncaab_auto_buyer_paper_trial.py`, `test_espn_nfl_odds.py`, the restore/refresh slice of `test_cli.py`, `test_daily_integrity.py`, `test_focus_sports_config.py`. `ruff check` and `ruff format --check` clean on all touched files. The full suite was not run.

Red proofs, in a detached scratch worktree at HEAD with my files overlaid (confirmed `model_prediction.__file__` pointed there), each change reverted alone:

| Revert | Result |
| --- | --- |
| non-D1 rule off (`non_d1 = False`) | 4 failed |
| non-D1 slots not appended to `declined_slots` | 3 failed |
| `_evaluated_slots` ignores evaluation | restore test failed |
| capture narrowed back to NCAAB only | capture test failed |
| `daily.py` at HEAD (no capture/restore wiring) | wiring test failed |
| NFL: reroute removed from `espn_odds_path` | settlement test failed |

Each passed again when restored. The daily wiring is pinned by a source-order test (`run_daily` is too heavy to execute); the helper behaviour is tested directly against a sqlite-authority ledger.

## Deviations

- [Finding] Item 2's premise was stale (see above); delivered a pin instead of a fix. The statement in the 1m3 summary ("left alone ... same stale data root") should be read as already-fixed.
- [Scope] `_evaluated_slots` exclusion goes beyond "keep `exclude_slots` semantics": without it, widening restore would resurrect picks that NBA/NFL/NCAAF gates or bans dropped.
- No live ledger, settle, daily or forecast run; no `config/production.yaml`, `auto_buyer_state.json` or `config/model.yaml` edit. STATE.md and ROADMAP.md not touched (shared with other sessions); the orchestrator should record this task.

## Not done

- Out of scope per the plan: live-ledger CLV backfill of settled rows, zero-staking non-executable-quote rows, promoting v7, config/state edits.
- No end-to-end `run_daily` execution (no live-ledger runs allowed); the live behaviour of the widened restore is unobserved until the next scheduled daily run. Check `step2_3_unreplaced_restored` in that run's report.

## Self-Check: PASSED

Commits fc7ff8b8, 1c154c4c, 0edd81e1 are ancestors of HEAD; `tests/test_nfl_settlement_consensus.py` exists; `commits: 3` measured with `rev-list` from `plan_head_before`.
