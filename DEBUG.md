# DEBUG.md — Current Project Audit and Reproduction Guide

**Last audited**: 2026-08-02 (see new section directly below)

## 2026-08-02 (latest) — Live-run verification of the per-model ledger architecture: 3 real bugs found and fixed, soccer draw settlement corrected

Operator asked to run a real `daily` end-to-end and confirm the per-model
ledger architecture (previous section) is genuinely working live, not just
migrated. Separately asked about KBO/NPB push settlement -- confirmed
correct as documented below -- which led to checking soccer's analogous
case and finding it was wrong.

**KBO/NPB tie/push settlement: confirmed correct, no change.** A tied KBO/
NPB game really is a 2-outcome market (home wins / away wins) with no
separate "draw" contract; Polymarket settles a tie at $0.50.
`cli.py::_settle_international_baseball_pick` sets
`binary_contract_settlement_value = 0.5` on a tie and `ledger.py::settle()`
computes `pnl = units * (binary_contract_settlement_value /
entry_probability - 1)` using the real pregame decision price as the
buy-in. Verified against real settled Doosan Bears/Samsung Lions/KT Wiz
push rows -- matches the formula exactly. This is the correct, intended
implementation of "if it's a push, market resolves 50/50, use pregame
price to determine buy-in, PnL from that price to 50/50" -- nothing to fix.

**Real bug found: soccer moneyline draws were graded PUSH, should be
LOSS.** Unlike KBO/NPB, Polymarket's soccer win market is not one 2-outcome
market with a tie case -- `data_sources/polymarket_us.py`'s own comment
(verified live 2026-07-27) documents it as three *independent* Yes/No
contracts per game (home wins / draw / away wins). A "home" or "away" pick
is a bet on that specific contract resolving YES; a draw resolves it NO --
a full loss of stake, not a refunded push. `pricing.py::grade_pick` was
reusing the generic moneyline "tied score -> PUSH" branch (correct for a
real 2-outcome tie-refund market, wrong for soccer's independent-contract
structure). Fixed: `grade_pick` now takes an optional `league` parameter;
`league == "SOCCER"` with a tied score grades LOSS. `ledger.py::settle()`
passes `row["league"]` through. 2 new tests in `test_pricing.py`
(soccer-draw-is-a-loss, non-soccer-tie-is-still-a-push -- KBO/NPB's
`binary_contract_settlement_value` special-case still depends on
`grade_pick` returning PUSH for the generic case).

**Real, quantified impact on already-logged data**: exactly 15 already-
settled soccer rows (mirrored identically in `data/flat_picks.xlsx` and
`data/research/soccer.xlsx`, since every soccer decision is logged to
both) were graded push/$0.00 and should have been loss/-1 unit (two of the
15 were sized at 1.25/1.5/2.0 units, not 1.0 -- pnl_units corrected
accordingly). Corrected via the sanctioned mutation path (never raw
edits): `archive_settled_rows` (with a real reason + archive reference,
full row content preserved at
`data/archive/2026-08-02-soccer-draw-push-bug/`), reset to `open`,
`import_rows` back in under the same `pick_id`, then re-`settle()`d
through the now-fixed `grade_pick` -- so every other derived field
(review_status, audit trail) comes from the real settlement code path,
not hand-computed. Verified zero `SOCCER`/`push` rows remain anywhere in
the ledger tree afterward.

**Real bug found: model ledger dedupe key silently dropped genuine
re-forecasts.** Live-verified by actually running `daily` and cross-
checking a real WNBA pick (event `401857107`, Indiana Fever @ Minnesota
Lynx) against its corresponding `data/model_ledgers/wnba-moneyline-elo-
trend-lr.xlsx` row. The Main-ledger pick_id had changed (an earlier open
pick for the same event was replaced by a same-day re-forecast with new
`model_probability`/`decision_price` and a fresh `observed_at_utc`), but
`record_from_pick_request`'s dedupe key -- `(event_id, market_type, line,
model_version)` -- didn't include `observed_at_utc`, so it matched the
*old*, already-migrated row's key and silently skipped writing the new
one. The per-model ledger was stuck showing stale numbers for any event
whose open pick got replaced, exactly the "same real decision routed to
Main+Flat" collapsing the key was designed for, over-applied to a
genuinely different later decision. Fixed: `observed_at_utc` added to
`_prediction_dedupe_key` (now a 5-tuple). Main/Flat/Research/Gated calls
for one real decision still share the exact same `PickRequest` object (and
therefore identical `observed_at_utc`), so they still collapse correctly;
a later re-forecast gets a new `observed_at_utc` and now creates a new row
instead of being silently dropped.

Adding `observed_at_utc` to the key surfaced a second, related bug:
`record_from_pick_request` wrote `request.observed_at_utc` verbatim, which
is `None` for callers that don't explicitly set it (several test fixtures,
possibly some real call sites) -- while `_append_record` (the primary
ledger) falls back to `request.observed_at_utc or iso_utc(created)`. Sole
result: every request with no explicit `observed_at_utc` was writing a
blank value, so every one of *those* re-forecasts collapsed into the same
blank-keyed row again, defeating the fix. `record_from_pick_request` now
takes a `created` parameter and applies the identical fallback; `_append_
record` passes its own `created` through. 1 new regression test
(`test_record_from_pick_request_does_not_dedupe_a_refreshed_forecast`).

Backfilled the 109 real rows this silently dropped by re-running
`scripts/migrate_to_model_ledgers.py` (idempotent by design -- it re-scans
every source ledger and skips anything already present by `prediction_id`,
so re-running after a live code fix is safe): 109 written, 380 skipped as
already migrated, 0 unmapped. Verified event `401857107` now has both the
original (migrated) row and the new, real re-forecast row, and a dry-run
immediately after writes 0.

**Real bug found: `ModelLedger.settle()` existed but was never called.**
The class had a working `settle()` method (used only by the dashboard's
manual operator-decision endpoint's read path, never by the actual
settlement pipeline). Nothing in `cli.py`'s real settlement paths
(`_settle_all_unsettled`, soccer/tennis/esports/KBO/NPB settlement)  ever
called it. Model ledger rows stayed `status: open` forever, even after
`PickLedger.settle()` graded the real, equivalent pick days or weeks
later -- meaning `compute_model_evidence`'s hit-rate/Brier/calibration
numbers (the entire point of "operator decides using real per-model
evidence") never actually populated past whatever the one-time migration
captured. Fixed with the same fail-soft pattern as the append-side hook:
new `model_ledger.settle_from_pick_row(model_ledgers_dir, row)` looks up
the matching open row by the same 5-tuple dedupe identity and calls
`ModelLedger.settle()` on it; wired into `PickLedger.settle()` right after
its own write, wrapped in try/except so a bug here can never turn a real,
successful settlement into a failure. 2 new tests in `test_ledger.py`
(settle-also-settles-the-model-ledger, a-failure-here-never-breaks-the-
primary-settle -- mirrors the existing append-side pair exactly).

Every fix in this section verified via the project's revert-and-confirm
convention (temporarily undo, confirm the new test fails, restore, confirm
it passes) before being considered done. Full suite: 643 passed. Ruff:
118 findings, matching the existing baseline exactly, 0 new.

**Not yet live-verified**: the settle-side wiring above has full unit-test
coverage but no live-run confirmation yet, because the day's real
settlement had already run (via the independently-scheduled
`com.modelprediction.daily.plist` launchd job, `scripts/run_daily.sh` --
found running concurrently under a real, pre-existing OS lock at
`data/locks/daily.lock` while investigating this, PID 32354 at the time)
before this fix landed. The append-side fix *will* be live-exercised by
that same scheduled run's forecast step. A live settle confirmation is the
natural next check once that process finishes.

## 2026-08-02 (later still) — Per-model ledger architecture: new schema, `ModelLedger`, real data migrated

Operator directive: "recompile all models will be production in its own
ledger, the clafiication of benchmarks or shadow should not exist, there
should be no calssification, all models are the same. i decide to pormote
it or not." Followed by explicit confirmation to build the full thing
("all of it in order").

**What shipped:**
- `src/model_prediction/model_ledger.py` — new `ModelLedger` class, one
  `.xlsx` file per model identity (not per sport/routing-destination).
  New common schema (`model_id`, `model_version`, `artifact_hash`,
  `code_revision`, `feature_schema_version`, `model_probability`,
  `model_projection`, `model_uncertainty`, `decision_price`,
  `market_no_vig_probability`, `model_market_difference`,
  `observed_at_utc`, `event_start_utc`, `input_availability`,
  `missing_inputs`, `source_lineage`, `status`, `result`, `closing_price`,
  `probability_clv`, `pnl_units`, `settled_at_utc`) plus a separate
  operator-decision block (`operator_decision`, `operator_selected_model`,
  `operator_selected_market`, `operator_units`, `operator_timestamp`,
  `operator_note`) that `record_operator_decision()` writes without ever
  touching the model's own fields. No `record_type`/`model_state`/
  `qualified_for_betting`-style classification field anywhere in this
  schema — deliberately, per the directive. `append_failure()` only
  accepts one of a fixed `INTEGRITY_FAILURE_REASONS` set (event started,
  identity unresolved, bad artifact hash, stale feature timestamp, wrong
  market, unmapped side, calculation failure, undefined missing-input
  behavior) — the only things allowed to block a numeric prediction now.
  12 new tests in `tests/test_model_ledger.py`.
- `scripts/migrate_to_model_ledgers.py` — read-only against every existing
  source ledger (`data/picks.xlsx`, `data/flat_picks.xlsx`,
  `data/research/*.xlsx`, `data/gated_research/*.xlsx`), maps
  `(league, market_type)` to a model identity (soccer's moneyline+total
  both map to `soccer-poisson-dc` — one model, two market types, not two
  models), and dedupes by the same market-identity key `ledger.py`'s own
  `_market_duplicate_key` uses (event/market/line/sportsbook/model_version),
  preferring a settled copy over an open one when the same real decision
  was logged to more than one old destination. Real numbers: 688 source
  rows scanned across 4 old destination types → 483 genuinely unique
  decisions written across 12 real models. Spot-verified against numbers
  already independently confirmed this session (soccer: 81, matching the
  earlier flat-ledger backfill count exactly; MLB moneyline: 110→55, a
  clean 2x consistent with Main+Flat being the only two destinations that
  sport ever wrote to). Idempotent — re-run against already-migrated data
  writes 0 new rows, skips all 483 by `prediction_id`.

**Explicitly not done in this pass** (real, large, separate remaining
work — see the conversation transcript for the full breakdown given to
the operator): cutting the *live* forecast pipeline (`cli.py`'s ~15
forecast functions, `daily`) over to write through `ModelLedger` instead
of the old `PickLedger`; the dashboard redesign (one row per event, one
column per model, evidence columns, no classification badges); every
challenger/ensemble model named in the plan (total-score Ridge, tennis
point-Markov, roster-aware esports Elo variants, joint Negative Binomial
totals) — none of that modeling code exists yet. `PickLedger`/`ledger.py`
is completely untouched; the live pipeline still writes through it exactly
as before. `data/model_ledgers/*.xlsx` is a real, verified, additive
backfill of historical data into the new shape — not yet the system of
record.

## 2026-08-02 (really final) — MLB position-player availability shipped; a real starter-quality candidate tested honestly and NOT promoted; CLAUDE.md added

### MLB position-player (lineup) availability -- shipped

Extended the pitching-staff availability feature (refactored into a shared
`_team_roster_group_availability` helper) to also cover non-pitchers --
`team_position_player_availability`/`matchup_position_player_availability`.
Real component of section 8 rank-2 ("confirmed vs. projected lineup").
Verified live: Yankees 18.75% unavailable, cross-checked against the real
roster (Aaron Judge, Cody Bellinger, Giancarlo Stanton, all genuinely on
the IL). 12 new tests, wired into `learned_forward.py` shadow-only, same
pattern as everything else this session.

### A real, well-motivated model-improvement candidate tested honestly — did NOT clear the bar, not promoted

Goal was explicit this round: find something that actually moves win rate/
profitability, not just another shadow/availability feature. Found that
the live MLB v7 model's `pitcher_era_gap` feature is a team-level rolling-
runs-allowed proxy, **not** starter-specific, despite its name — the
project's own roadmap ranks "true starting-pitcher quality" as MLB's #1
priority, and it's genuinely never been in the live model. A prior
attempt (`probable_starter_era_gap`, ESPN live probables) was built and
then retired for being train/serve-skewed (2026-07-30).

Found real, already-built, never-used infrastructure for this:
`validation.py::_load_starter_era_map`/`_starter_era_gap` computes a
genuinely point-in-time-safe rolling-5-start ERA gap from real
`mlb_statsapi` box-score snapshots (each starter identified as
`pitcher_order[0]`, history updated strictly *after* computing each game's
feature) -- already wired into `ValidationRow.starter_era_gap`, but never
tested in any ablation (grepped `production_feature_ablation.py`,
`roadmap_challenger.py`, and `outputs/` -- zero hits).

A quick standalone signal check (1055 games, the elasticity-refit feature
cache) showed a real, promising correlation: 0.14 with home-win outcome,
correct direction, real bucket separation (away-starter-much-better:
46.0% home win rate; home-starter-much-better: 55.9%; baseline 51.6%).

**Ran the real thing before trusting that**: `build_walk_forward_rows` +
`chronological_split` (the project's own validated 60/20/20 chronological
framework, reused rather than reimplemented) on the full real history --
6,324 MLB games, 2024-04-06 through 2026-08-01, 5,186 with a real
`starter_era_gap`. Fit the incumbent v7 feature set vs. incumbent +
`starter_era_gap`, both ways, on train only:

| | Validation Brier | Validation accuracy | Locked-holdout Brier | Locked-holdout accuracy |
|---|---|---|---|---|
| Incumbent (v7) | 0.24637 | 55.68% | 0.24683 | 55.65% |
| + starter_era_gap | 0.24696 | 55.13% | 0.24585 | 56.51% |

**Mixed, not a clean win — not promoted.** The candidate is *worse* on the
validation set (the set this project's own promotion rule says decides
whether a feature is worth including at all) and *better* on the locked
holdout, both by small margins. Checking holdout to override a validation-
set regression would itself be exactly the kind of "peek at holdout to
justify inclusion" `docs/AGENTS.md` explicitly forbids. The coefficient
sign is correct (`-0.0193`: a worse home starter ERA lowers home win
probability, as expected) and the standalone correlation is real -- this
isn't nothing -- but `elo_probability` (coefficient ~3.0) already captures
most of what a team's pitching-staff-quality trend proxies for over time,
and the genuinely *additive* value on top of the existing feature set
doesn't clear this project's own bar. Documented honestly rather than
force-promoted or suppressed: a real, validated-as-*not*-sufficient
finding, worth revisiting if a future session tries a different
functional form (e.g. an interaction term, or replacing `pitcher_era_gap`
outright instead of adding alongside it) rather than repeating this exact
test.

### CLAUDE.md added

Auto-loaded project guidelines: doc reading order, the point-in-time
correctness invariant (dominant real-bug source all session), the
shadow-feature pattern, promotion/validation conventions reusing existing
tooling instead of reimplementing chronological splits, testing
conventions, and the worktree-isolated-subagent staleness gotcha from
earlier today.

**Tests**: 571 passed (12 new: 5 position-player availability + the
same-day-transaction and pitching-staff tests already counted above).
`.venv/bin/ruff check src/ tests/` -> 117, at/under baseline.

## 2026-08-02 (final) — three-agent model review results, one real fix applied, one real gap found and NOT yet fixed (needs a scope decision)

Three parallel agents reviewed (1) all MLB code touched today, (2) esports/
KBO-NPB/soccer wiring, (3) the core ledger/eligibility/units invariants.
**Process learning, worth remembering**: two of the three ran with
`isolation: "worktree"`, which checks out a git *commit* into an isolated
copy -- it cannot see uncommitted working-directory changes. At the time
they ran, substantial real, tested, verified work (soccer flat/Main wiring,
the KBO/NPB timestamp fix, the MLB pitching-staff feature) was sitting
uncommitted. One agent correctly self-diagnosed this (used `git archive`
against the right commit and flagged the mismatch explicitly) and its
findings about the state *at that commit* were accurate, just about
already-superseded code. The other did not self-diagnose it and instead
concluded several already-fixed things (`edge_scaled_units` ignoring
`model_uncertainty`, `min_pick_units` still 0.5, `archive_settled_rows`
not existing) must have been "reverted" -- **all three directly
re-verified false** against the actual current code (see below). Lesson:
worktree-isolated review agents need either a fresh commit right before
launch, or explicit instruction to read the shared checkout path directly
(the way the third, reliable agent did) whenever there's known uncommitted
work.

### Real bug, fixed: same-day MLB transactions could leak into a decision

Covered in detail above this section's neighbor entries -- MLB Stats API's
transaction `date` field has no time-of-day component, so a transaction
reported the same calendar day as the decision could have happened before
or after it, with no way to tell. `_starter_status` now excludes same-day
transactions entirely and derives its cutoff from the decision's own
timestamp rather than `game_date`. Verified the regression test actually
catches the bug (reverted the fix, confirmed the test failed, restored
it). Also added a regression test locking in that `ENGINE_VERSION` rejects
the un-promoted v0.3 elasticity refit, and fixed two research scripts'
docstrings that referenced the wrong formula version.

### Real gap, found, NOT fixed: team-ban enforcement doesn't work for esports/soccer/KBO/NPB/tennis

Independently re-verified myself, not just trusted from the review (the
reporting agent's environment was unreliable per above, but this specific
finding checks out against the current live code): `evaluate_esports_
eligibility` (`eligibility.py`) -- the eligibility path used by every
sport routed through the canonical-registry-free "name-based" pattern
(esports, soccer, KBO, NPB, tennis) -- takes no `TeamBanList` parameter
and never checks one. Confirmed no call site (`_forecast_soccer_sport`,
`_log_esports_forecast`, `_forecast_international_sport`,
`_forecast_tennis_sport` in `cli.py`) passes bans through either. Contrast
with `evaluate_eligibility` (the MLB/WNBA/NBA/NFL path), which checks
`ban_list.check(...)` first, before anything else.

It goes deeper than a missing parameter: `TeamBanList.check`/`.add`/
`.remove` (`bans.py`) all call `self.registry.resolve(league, team_input)`
internally, and esports/soccer/KBO/NPB teams are deliberately **not** in
the canonical registry (confirmed live: `registry.resolve(League.LOL,
"T1")` raises `EntityResolutionError`). So `model-prediction ban add
--league LOL --team X` would itself fail today, not just the eligibility
check -- the entire ban mechanism is built around registry resolution, and
these sports were deliberately built registry-free. `config/model.yaml`'s
`team_ban_list.teams` does have (empty) `LOL`/`CS2`/`KBO`/`NPB`/`TENNIS`
sections, suggesting the intent to support this was there; the enforcement
never got built. Soccer/DOTA2/VALORANT/RAINBOW_SIX don't even have a
config section.

**Zero live impact today**: every one of those league sections is
currently empty (no team is banned in any of these sports right now), so
nothing is silently slipping through *right now*. But if the user ever
bans a team in one of these sports expecting it to actually block future
picks, it would silently not work. Properly fixing this means building a
genuinely separate, name-based ban mechanism for these sports (bypassing
`TeamBanList`'s registry coupling entirely, not just adding a parameter),
which is a real, non-trivial, cross-cutting change touching real-money-
adjacent eligibility logic in 4+ sports -- flagged for a scope decision
rather than built unilaterally in the same pass as everything else today.

## 2026-08-02 (still later) — MLB pitching-staff availability (new shadow feature, roadmap item), plus a full model review in progress

New feature: `features/mlb_player_availability.py::team_pitching_staff_availability`/
`matchup_pitching_staff_availability` — the first real pass at
`docs/MODEL_IMPROVEMENTS.md` section 8's rank-3 roadmap item ("bullpen
availability... closer/setup absence"), picked as the concrete next step
after reframing the roadmap's priority order (see the doc's own section 1/
12 updates from earlier today): MLB is the only one of the four major
leagues currently in season, and this reuses the exact live-roster-
snapshot infrastructure already built and validated today for starter
availability, rather than starting a new data source.

Coarser than the roadmap's full description -- MLB Stats API roster data
identifies position *type* (Pitcher vs. everyone else), not bullpen *role*
(closer/setup/long) -- so this reports aggregate pitching-staff health
(share of the current staff on the IL or an admin list), not a specific
reliever's availability. `capture_roster_snapshot` now also stores each
player's `position_type` (a schema addition -- pre-existing captured
snapshots don't have it, so this feature only works going forward from
today, same "clock going forward, no backlog" situation as the KBO/NPB fix).

**A real design bug caught by testing against live data before it
shipped**: the first version's "unavailable" bucket included `"Reassigned
to Minors"`, producing a nonsensical baseline (Yankees 43%, Dodgers 55% of
their pitching staff "unavailable"). Being optioned to AAA is routine
40-man-vs-26-man roster depth management, present for every team on any
given day regardless of health -- not an injury signal, and it swamped the
real signal with structural noise. Fixed with a new, narrower
`INJURY_OR_ADMIN_LIST_STATUSES` constant (`data_sources/mlb_injuries.py`)
that explicitly excludes `Reassigned to Minors` from both the numerator
and denominator, kept deliberately separate from the existing
`STATUS_ACTIVE_PROBABILITIES` (which correctly treats "optioned" as
disqualifying for a *named* player's starter-availability check, but
shouldn't for this *aggregate* health read -- same status, different
question, different answer). Re-verified live after the fix: Yankees
13.3% (2/15 pitchers), Dodgers 43.5% (10/23) -- cross-checked the Dodgers
number against the real roster entries directly (Blake Snell, Tyler
Glasnow, Bobby Miller, Brusdar Graterol, Gavin Stone, Ben Casparius, Blake
Treinen, Brock Stewart, Will Klein, Jake Cousins -- all real, all
genuinely on the IL, matches well-documented real 2026 Dodgers pitching
injuries).

Live-only by construction, and unlike the starter-availability feature,
this one has **no transactions-based historical fallback** -- "how many
pitchers are on the active roster right now" isn't reconstructable from
the IL-transaction log alone (trades/options/call-ups change roster
composition in ways a pure IL filter can't capture), so this feature
genuinely cannot backtest against past games, only forward from today.

Wired into `learned_forward.py`'s generic feature-computation path with
its own dispatch branch and feature-name constant
(`PITCHING_STAFF_FEATURE_NAMES`), same shadow-only, inert-until-requested
design as every other availability feature in this project -- no
production artifact requests these feature names, so this is dead code in
live production today by design, exactly like starter availability was
until an artifact opts in.

**Tests**: 7 new (`test_pitching_staff_excludes_optioned_players_from_numerator_and_denominator`,
`test_matchup_pitching_staff_gap_favors_home_when_away_staff_is_worse`,
`test_pitching_staff_no_pitchers_found_fails_closed`,
`test_pitching_staff_no_roster_snapshot_fails_closed`,
`test_pitching_staff_unrecognized_status_fails_closed`,
`test_matchup_pitching_staff_rejects_postgame_observation`, plus one
existing capture-schema test updated for the new `position_type` field).
Full suite: **563 passed**, 0 failed. `.venv/bin/ruff check src/ tests/` ->
117 findings, still at/under the 118 baseline.

**In progress, not yet reported**: three parallel review agents launched
against (1) all MLB code touched today, (2) esports/KBO-NPB/soccer wiring
(specifically re-checking every other `_forecast_*` function in `cli.py`
for the same timestamp-ordering bug class that broke KBO/NPB), and (3) the
core ledger/eligibility/units invariants. Findings and any resulting fixes
will be appended here once they land.

## 2026-08-02 (later) — soccer wired to flat/Main-gated, esports live-vs-backtest investigation, a real KBO/NPB zero-logging bug found and fixed

### Soccer: wired into flat forecast and Main (gated), same eligibility bar as Gated Research

`_forecast_soccer_sport` (`cli.py`) gained `flat_ledger`/`main_ledger`
parameters. `flat_ledger`: every priced contract logged unconditionally,
same "show everything" semantics every other sport's flat forecast already
uses -- previously soccer's `research_ledger is None` early-return meant a
flat-only call did nothing at all; fixed by falling back to `flat_ledger`
as the exposure-computation source when `research_ledger` isn't provided.
`main_ledger`: mirrors `gated_ledger` exactly, same eligibility result,
same "only when genuinely eligible" gate (operator-confirmed: same bar as
Gated Research, not a separate/stricter one).

**This wiring is correct and live-verified (19 real rows now flowing into
`data/flat_picks.xlsx` via a real `flat-forecast --sport soccer` run), but
produces zero Main rows today.** `config/model.yaml`'s `SOCCER.status` is
still `"research"`, and `lifecycle.py::can_create_qualified_call` requires
`ModelState.SHADOW_QUALIFIED` before any request can become a real CALL --
confirmed this is exactly why 100% of soccer's 62 real research picks carry
`NO_CALL_MODEL_UNVALIDATED`, unrelated to edge or confidence. Promoting
soccer to `shadow_qualified` (the same status LOL/CS2/DOTA2/VALORANT/MLB/
NBA/WNBA/NFL/KBO/NPB already carry) is a real model-promotion decision this
change deliberately does not make on its own -- soccer's real settled
research record (28-19, +6.84u) is a plausible case for it, but promotion
in this project's own stated governance requires the same walk-forward/
locked-holdout process those other sports went through, not a side effect
of wiring two ledgers.

Four new tests in `tests/test_cli.py` (`_soccer_forecast`/`_soccer_config`
fixtures): flat logs everything even when blocked at the model-state gate;
main mirrors gated exactly on both a real CALL and a blocked one.

### Esports: live-vs-backtest divergence investigated, not (yet) acted on

Real settled record since the v4->v5 rebuild: LOL 4-7 (-5.73u research),
1-2 (-1.83u gated); CS2 12-11 (-0.79u), 4-6 (-1.43u gated); DOTA2 8-5
(+3.33u), 3-2 (+1.68u gated); VALORANT 4-3 (+1.36u), 2-1 (+2.02u gated).
Rainbow Six: zero picks, but confirmed as a real data-availability gap
(zero scheduled Polymarket R6 events today), not a code issue --
`matches.jsonl` has 2,969 real historical matches for backtesting, there's
just no live market right now.

Checked calibration (predicted probability vs. actual hit rate) per title.
DOTA2 and VALORANT are fine (predicted ~59%/56%, actual ~62%/57%). LOL and
CS2 both run overconfident on their live samples: LOL predicted 58.9%
research-wide / 62.1% gated, actual 36.4%/33.3% -- roughly 2.4 standard
deviations below what LOL's own locked-test backtest (70.3% accuracy,
n=1,910, a real, large, validated number) would predict for a sample this
size. Investigated the likely explanation rather than guessing: the
backtest's own K/threshold selection is self-labeled
`"profitability": "not_established_no_point_in_time_market_prices"` --
it assumes a flat -110 price for every match, because no real captured
Polymarket price history existed when that methodology was built. That's
now only partially true: 16 days of real captured BBO exist (28,469 lines
across LOL/CS2/DOTA2/VALORANT since 2026-07-17), and live picks already
price against real executable asks (confirmed: `esports.py` sources
`executable_ask` from a live `PolymarketUSClient` call, not a synthetic
assumption) -- so the live PnL numbers above are real, not an artifact of
the backtest's own pricing assumption.

**Deliberately not acted on**: refitting confidence thresholds against the
current 16-day real-price window would trade the ~1,900-2,900-match locked-
test sample that justified each title's current threshold for one two
orders of magnitude smaller -- likely replacing real signal with noise, not
fixing anything. Documented as a real, open gap in
`docs/MODEL_IMPROVEMENTS.md` section 10 with a concrete recommendation: keep
accumulating real captured BBO (free, compounds daily) and revisit a real-
price threshold refit once the sample is large enough to matter.

### Real bug found and fixed: KBO/NPB have logged zero picks, ever, despite real games every day

Found while pulling a full cross-sport performance summary (`research_kbo`/
`research_npb` both showed 0 total rows -- not just 0 settled -- and
`research/kbo.xlsx`'s file mtime was 2026-07-28, untouched by any `daily`
run since). Today's real `daily` log showed `kbo: {"events": 5, "logged":
0}` / `npb: {"events": 6, "logged": 0}` -- real scheduled games, real
priced contracts with real team names/probabilities, zero logged. Traced
directly (wrapped `PickRequest.validate` to print the real exception): every
single contract failed with `"observation timestamp cannot be in the
future"`.

Root cause: `_forecast_international_sport` (`cli.py`) captured
`observed_now = utc_now()` **before** calling
`forecast_international_baseball_slate(...)` -- but that function stamps
each contract's own `observed_at_utc` using **its own internal** `utc_now()`
call, which happens strictly later (real fetch/compute time passes inside
the call). `request.validate(now=observed_now)` then always compared the
contract's own (later) timestamp against an (earlier) `now`, unconditionally
rejecting every contract as "in the future." `_forecast_soccer_sport`/
`_forecast_tennis_sport` already capture `observed_now` in the correct
order (after the slate builder returns); international baseball was the
one holdout. Fixed by moving the capture to after the slate-build call,
matching the correct pattern.

Verified three ways: (1) a direct call against real live KBO data went
from `logged=0` (with the real `ValueError` printed) to `logged=4`
matching `priced=4`; (2) a new regression test
(`test_international_forecast_observed_now_is_captured_after_slate_building_not_before`
in `tests/test_cli.py`) using a mock clock that genuinely advances during
the mocked slate-build call -- confirmed to fail against the pre-fix
ordering (`0 == 1`) and pass against the fix; (3) existing tests never
caught this because they freeze `utc_now()` to one fixed value everywhere,
which trivially satisfies `observed <= now` regardless of call order --
a real gap in the existing test fixture, not just in the production code.

This means every KBO/NPB research/gated pick this project has ever
"forecasted" was silently discarded before reaching any ledger, for as
long as this ordering bug existed. Re-ran the real `daily` command after
the fix to confirm live recovery (see Verification below).

**Tests**: full suite passes; 5 new tests (`test_soccer_flat_ledger_logs_every_contract_even_when_model_state_blocks_a_call`,
`test_soccer_main_ledger_mirrors_gated_ledger_exactly`,
`test_soccer_main_ledger_stays_empty_when_gated_ledger_does`,
`test_daily_forecast_roster_includes_soccer_and_both_international_baseball_leagues`
[pre-existing, unaffected], `test_international_forecast_observed_now_is_captured_after_slate_building_not_before`).
`.venv/bin/ruff check src/ tests/` -> 117 findings, at/under the 118
baseline.

## 2026-08-02 (latest) — MLB player availability (shadow feature) + Measured Edge margin/totals rebuild with fresh data

Two-part session, both parts of a single approved plan.

### Part 1: MLB player availability, shadow feature

New modules: `data_sources/mlb_injuries.py` (captures MLB Stats API's
`/v1/teams/{id}/roster?rosterType=40Man` live status and `/v1/transactions`
dated IL history), `features/mlb_player_availability.py` (cross-references
the ESPN-reported probable starter -- via a new sibling helper,
`data_sources/espn_probables.py::point_in_time_probable_starters`, exposing
the starter *names* the existing ERA-gap helper discarded -- against that
transaction history). Emits `probable_starter_unavailable_{home,away}` and
`availability_report_age_hours`, shadow-only (its own MLB-scoped gate in
`learned_forward.py`'s `_compute_features`, inert until a future artifact
lists these feature names). Wired into `cli.py`'s `daily` as
`step5c_mlb_availability`. 14 tests in `tests/test_mlb_availability.py`,
including a dedicated `date`-vs-`effectiveDate` regression test: MLB Stats
API transactions have both a `date` (when reported) and an `effectiveDate`
(sometimes retroactively backdated, e.g. "placed on the IL retroactive to
last week") -- only `date` (stored as `reported_date`) may ever decide what
was knowable as of a decision time.

A live smoke test against the real API caught a real bug before it shipped:
MLB's `typeDesc` is almost always the generic `"Status Change"` for IL
moves -- the actual detail ("placed RF Aaron Judge on the 10-day injured
list") lives in the free-text `description` field. The matcher was built
against `type_desc` first, which would have silently missed nearly every
real IL move; fixed to match `description`, then re-verified live (found
Aaron Judge on the 60-day IL and correctly flagged him unavailable). Also
simplified: a transaction that doesn't mention an IL/paternity/bereavement/
restricted/suspended list (trades, options, recalls, waiver claims -- most
of a player's real transaction history) is silently ignored rather than
raising an "unrecognized status" error, since only the IL-relevant subset
says anything about availability.

### Part 2: Measured Edge margin/totals rebuild

`scripts/mlb_elasticity_refit.py` (new, saved/rerunnable, with its own
on-disk feature cache at `data/research/mlb_elasticity_feature_cache.jsonl`
so reruns don't re-hit ESPN for already-reconstructed games) refit the
Trend Engine's five run-scaling elasticities via a real
`sklearn.linear_model.PoissonRegressor` GLM against 1,136 real completed
games, 2026-05-04 through 2026-07-31 (vs. the prior fit's 629 games) --
reusing `ESPNMLBClient.reconstructed_features` and
`models.mlb._offense_index`/`_starter_weakness` directly (the same
functions the live formula calls), so the design matrix matches production
feature construction exactly. Same 4-fold chronological expanding-window
CV structure as the original fit, this time with fold-level results
actually saved. One mid-run bug found and fixed: a transient ESPN 500
crashed the first attempt because `collect_rows` only caught
`(KeyError, TypeError, ValueError)` around `reconstructed_features`, not
`httpx.HTTPError` -- fixed to catch it, log, and continue (without
marking the event permanently skipped, so a rerun retries it) rather than
losing a multi-hour collection run to one bad request. The on-disk cache
meant no data was lost from the crash either way.

New elasticities (`mlb-analyst-poisson-trend-v0.3.yaml`): offense
0.035->0.088, starter_weakness 0.211->0.281, park 0.222->0.259, weather
0.021->0.040. Bullpen: previously forced to 0.0 (v0.2's fit found it
consistently negative -- implausible, attributed to selection bias in
which relief innings get recorded). This run's bullpen-included fit shows
a *positive* coefficient in every one of the 4 folds (0.170, 0.050, 0.044,
0.013) -- the documented governance rule ("only overturn 0.0 if every fold
is positive") is technically satisfied, so `bullpen_elasticity=0.069` is
now live in v0.3. Flagged explicitly rather than presented as settled: the
per-fold magnitude is monotonically *declining* by an order of magnitude
across the 4 folds as more data accumulates, which is at least as
consistent with a fading spurious correlation as with a real small
positive effect. Worth specifically re-checking next time this is refit
with more data before trusting it further.

`scripts/mlb_measured_edge_calibrate.py` (new, saved/rerunnable) refit
scale/offset for both margin and totals via OLS against two separate,
non-blended windows: (a) diagnostic -- `reconstruct-mlb-markets` extended
`data/historical/mlb_market_lines_reconstructed.jsonl` from 162 to 340
games (2026-07-01 through 2026-08-01, ESPN-reconstructed, hard-labeled
`timestamp_valid=False`); 290/284 of those games had usable spread/total
lines and cached features. (b) real-market -- genuine captured Polymarket
BBO from `data/odds/mlb/2026-07-17` through `2026-08-01`, joined to the
feature cache by (game_date, away team, home team) rather than event_id,
since Polymarket's own event_id is an unrelated internal id (a real bug
initially: the first calibration run matched 0 real-market rows because it
joined on event_id directly; fixed by building a team-pair+date index and
splitting Polymarket's `event_title` on " vs. "). Also corrects
`calibration_note`'s v1-era mischaracterization of the diagnostic window
as "real historical Polymarket-reconstructed spread lines" -- it has
always been ESPN's postgame reconstruction, diagnostic-only per
`ingest.py::reconstruct_mlb_markets`'s own hard `timestamp_valid: False`
label.

**Margin against the fresh, larger diagnostic window: a real, corroborated
improvement, with either elasticity set.** v1 (162 games, 2026-07-01..
07-12): correlation 0.062, 80 picks, 53.7% hit rate, +2.09 units. Refit
against the new v0.3 elasticities (290 games, 2026-07-01..08-01):
correlation 0.208, 285 picks, 60.0% hit rate, +41.45 units. Refit with
v0.2's *original*, unchanged elasticities on the exact same fresh 290-game
window: correlation 0.206, 289 picks, 59.5% hit rate, +39.36 units --
essentially identical. **Conclusion: margin's whole improvement came from
the fresher/larger calibration window, not from the elasticity refit
itself.**

**Totals: the v0.3 elasticities actively regressed it -- diagnosed and
fixed by keeping v0.2's elasticities.** First attempt (calibrating totals
against the new v0.3 elasticities) regressed badly against v1: correlation
0.166->0.041, hit rate 56.0%->52.9%, units +6.36->+0.73; real-market
corroboration (genuine Polymarket BBO) went further negative -- only 8
qualifying picks, 37.5% hit rate, **-2.27 units**, actively losing money.
Investigated rather than accepted: re-ran the exact same fresh 290-game
diagnostic window and 65-game real-market window with v0.2's *unchanged*
elasticities instead of v0.3's. Result: correlation 0.0585 (vs. 0.0414
under v0.3), hit rate 55.3% (vs. 52.9%), units +6.82 (vs. +0.73); real-
market corroboration jumped from 8 qualifying picks to **37**, hit rate
56.8%, **+3.09 units** -- flips from losing to profitable. Root cause: the
elasticity refit's GLM was fit against each *individual team's* runs
scored, a margin-shaped objective (which team scores more). Reusing that
same fitted elasticity set for totals (which cares about the *sum* of both
teams' runs) is a real objective mismatch -- elasticities that sharpen the
margin signal can inflate the run estimate's variance in ways that hurt
totals calibration specifically, even though both markets share one
`estimate_runs()` formula. **The promoted totals-v2 therefore uses v0.2's
original elasticities, not v0.3's** -- this also means `ENGINE_VERSION`
never needed to change (see below), sidestepping the margin/totals
coupling problem entirely.

Both heads independently re-checked a third way, against the 47 real
already-settled `measured-edge-{margin,totals}-v1` picks in
`data/flat_picks.xlsx` (not fresh calibration data -- recomputed via
`scripts/mlb_measured_edge_compare_settled.py` what v2, using v0.2's
elasticities, would have said for those exact same games/selections,
graded against their real known outcomes): margin (24 picks) Brier 0.2312
-> 0.2183, same 17/24 direction-correct; totals (23 picks) Brier 0.2448 ->
0.2489 (slightly worse) but direction-correct 9/23 -> 11/23 (better) --
mixed and far too small a sample to read much into on its own, but
consistent with "no regression" rather than the alarming totals collapse
seen under v0.3's elasticities.

**`mlb-analyst-poisson-trend-v0.3.yaml` stays on disk as a documented
research artifact, not promoted.** Its real value turned out to be
negative-result knowledge (a single shared elasticity set doesn't serve
both margin and totals; the bullpen finding below), not a better formula.
Also worth flagging on its own terms, independent of the promotion
decision: v0.3's bullpen-included fit shows a *positive* coefficient in
every one of its 4 CV folds (0.170, 0.050, 0.044, 0.013), technically
clearing the "overturn v0.2's forced-0.0 only if every fold is positive"
bar -- but the per-fold magnitude is monotonically *declining* by an order
of magnitude as more data accumulates, at least as consistent with a
fading spurious correlation as a real small positive effect. Not carried
into the promoted totals-v2 (which uses v0.2's elasticities, bullpen still
0.0); worth specifically re-checking if `mlb_elasticity_refit.py` is ever
rerun with more data.

**Promoted.** `MARGIN_MODEL_VERSION`/`TOTALS_MODEL_VERSION` in
`models/mlb.py` -> `measured-edge-{margin,totals}-v2`; `ENGINE_VERSION`
**unchanged** (`mlb-analyst-poisson-trend-v0.2` -- both v2 artifacts'
`base_score_model_version` is v0.2, verified via `_load_artifact`'s own
hash/version checks before promoting, and by actually instantiating
`MeasuredEdgeMarginModel`/`MeasuredEdgeTotalsModel` against the real files).
Six hardcoded `-v1.json`/`"measured-edge-*-v1"` references in `cli.py`
(lines ~661-662, 736, 817-818, 886) updated to `-v2`. v1 artifacts and
every v1 ledger row left on disk untouched -- nothing archived or deleted,
matching this project's versioned-artifact convention (old versions
coexist by filename; only the live pointer moves).

**Verified live in production, not just in scripts.** Ran the real `daily`
command end to end for 2026-08-02 (full multi-sport cycle -- BBO capture
across 9 sports, all forecasting, settlement, MLB availability capture --
not an MLB-only dry run). MLB Measured Edge: 15 scheduled games, 2 skipped
(unresolved probable starter, routine), 26 candidates logged to the flat
ledger (13 spread + 13 total) carrying `model_versions:
["measured-edge-margin-v2", "measured-edge-totals-v2"]`; confirmed
directly in `data/flat_picks.xlsx` (19 new margin-v2 + 19 new totals-v2
rows for 2026-08-02, alongside the untouched v1 history). `step5c_mlb_availability`
also ran cleanly in the same real cycle: 30 teams, 3,572 real transaction
entries captured.

**Tests**: `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` ->
**546 passed**, 0 failed (524 baseline + 14 new `test_mlb_availability.py`
+ 6 new `test_mlb_elasticity_refit.py` + 2 new
`test_espn_probables.py` additions; `test_mlb_measured_edge_calibration.py`
and its production-artifact-loading test updated from v1 to v2 filenames/
version strings to match the promotion). `.venv/bin/ruff check src/ tests/`
-> 117 findings, at/under the established 118 baseline (no new findings
from anything touched this session).

### Same day, later: two real bugs found and fixed in Part 1's shadow feature via ad hoc review of the real captured data

Reviewing today's own real captured `data/availability/mlb/` snapshots
(not synthetic fixtures) for anything the tests might have missed surfaced
two genuine gaps:

1. **Missing "rehab assignment" marker.** Real MLB Stats API transaction
   descriptions include 291 distinct "sent RHP X on a rehab assignment to
   Y" entries -- a player still recovering, not yet activated -- that
   matched neither `AVAILABLE_TRANSACTION_MARKERS` nor
   `UNAVAILABLE_TRANSACTION_MARKERS`, so they were silently skipped. Low-
   impact when the original "placed on the X-day injured list" transaction
   is also within the capture window (the earlier entry still wins as the
   latest *relevant* one), but a real gap when it isn't -- e.g. a 60-Day IL
   player whose placement predates the transactions capture's 60-day
   rolling lookback (`cli.py`'s `_capture_mlb_availability`) but who has a
   recent, in-window rehab-assignment transaction. Fixed by adding
   `"rehab assignment"` to `UNAVAILABLE_TRANSACTION_MARKERS`; verified no
   false-positive risk (zero real transactions in the captured data contain
   both "rehab" and "activated," so a genuine return-from-rehab -- its own
   separate "activated from the X-day injured list" transaction -- still
   correctly overrides it, since `_starter_status` checks the available
   marker first).

2. **Roster snapshots were captured but never read.** `cli.py`'s daily step
   already calls `capture_roster_snapshot` (confirmed real files exist,
   e.g. `data/availability/mlb/snapshots/2026-08-01/roster-*.json`), but
   `features/mlb_player_availability.py` only ever consulted the
   transactions-based reconstruction -- the roster snapshot's direct,
   current-status read was dead weight. This meant the transactions path's
   lookback-window blind spot (above) had no real backstop for a live
   decision. Fixed: `matchup_player_availability` now checks a fresh
   (within `maximum_report_age_hours`, strictly *before* the decision time
   -- a capture from after `observed_at` is never eligible, since a
   live-only signal from the future would leak knowledge the decision
   couldn't have had) roster snapshot first for each side, falling back to
   the transactions-based reconstruction only when nothing sufficiently
   fresh exists (the only option for a genuinely historical/backtest
   decision, since a live roster read can never cover the past). An
   unrecognized roster status now fails closed
   (`NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN`) rather than being silently
   ignored, unlike free-text transaction descriptions -- roster status is a
   small, closed, well-known enum (`STATUS_ACTIVE_PROBABILITIES`), so an
   unrecognized value is a real signal something changed upstream, not
   routine noise. Result now also exposes
   `{home,away}_probable_starter_source` (`"roster"` or `"transactions"`)
   so which path resolved each side is always visible. Verified live: found
   a real IL player (Adam Frazier, Injured 10-Day, Los Angeles Angels) on
   today's actual roster snapshot and confirmed the feature correctly
   resolves him via the roster path with `probable_starter_unavailable=1.0`.

**Tests**: 7 new (`test_rehab_assignment_flags_starter_unavailable_even_without_an_in_window_il_placement`,
`test_activation_after_rehab_assignment_still_clears_the_flag`,
`test_fresh_roster_snapshot_is_preferred_over_transactions`,
`test_roster_beyond_an_old_il_placement_still_catches_unavailability`,
`test_stale_roster_snapshot_falls_back_to_transactions`,
`test_roster_snapshot_captured_after_the_decision_is_never_used`,
`test_unrecognized_roster_status_fails_closed`), all in
`tests/test_mlb_availability.py`. Full suite: **553 passed**, 0 failed.
`.venv/bin/ruff check src/ tests/` -> 117 findings, still at/under baseline.

## 2026-08-01 — full reproduction-checklist re-run, end to end, against the state left by everything above

After four consecutive full-project review passes and a large number of
real fixes (side-selection logic in three sports, CLV wiring for three
more, a soccer team-name collision, a KBO/NPB home/away label risk, MLB
calibration governance, an esports methodology rebuilt from v4 to v5, a
four-implementation Elo consolidation, a dead sizing parameter finally
read, the unit range widened, and a real gap in the live-money SELL path)
it was worth re-running this file's own "Reproduction commands" checklist
in full, top to bottom, exactly as written, rather than trusting that the
individual per-fix verification passes add up to a system that's still
coherent as a whole. Every command below was actually executed against
the live repository state in this exact session, not paraphrased from
memory of what earlier per-fix checks showed.

**Tests**: `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` ->
**524 passed**, 0 failed, 0 skipped, 0 errors, 16.33s wall time. This is
the same 524 reported at the end of the fourth pass above — meaning
nothing in the interval between that pass's own verification and this
independent re-run introduced any regression, and no test is flaky at the
"run it twice in a row" level (a real concern after finding a genuine
time-of-day-dependent flaky test in the fourth pass above — this re-run
is itself partial evidence, though not proof, that no *other* latent
timing-flakiness exists elsewhere in the suite).

**Critical imports**: `model_prediction.cli`, `.validation`,
`.learned_forward`, `.eligibility`, `.ledger`, `.forward`, `.audit`,
`.xlsx_ledger` all import cleanly with no circular-import errors — worth
specifically re-checking given this session added new cross-module imports
that didn't exist before (`esports.py`/`international_baseball.py`/
`models/tennis.py` all now import from `features/elo_ratings.py`,
`cli.py` now imports `refresh_recent_international_baseball_matches` from
`international_baseball.py`) — a real, if usually easy-to-catch-at-import-time,
risk any time new inter-module edges get added.

**CLI**: `.venv/bin/model-prediction --help` exits cleanly; Python
3.14.5, matching every other verification pass this session (no
interpreter drift).

**`verify-chain`** (the audit-log tamper-detection / ledger-reconciliation
check): `break_count: 0`, `chain_intact: true`, `rows_missing_creation_event: []`.
`audit_lines` now at 34,748 (up from the low-9000s at the very start of
this session's work — reflecting every archival, restoration, settlement,
and order-related audit event this entire session generated, all still
individually verifiable and none breaking the hash chain).

**Canonical artifact hashes**: swept every JSON file under
`config/models/` (35 files as of this run — up from 33 at session start:
the esports v5 rebuild added 5 new artifacts, and the retired v4 esports
artifacts were deliberately left on disk as historical record rather than
deleted, same convention as MLB's retired v5/v6). 33 of 35 recompute to
their own declared hash correctly. The 2 mismatches
(`nba-spread-baseline-v1.json`, `nfl-spread-baseline-v1.json`) are the
exact same pre-existing, already-investigated pair found and diagnosed
earlier this session — confirmed via `grep` across the entire `src/`
tree that the config keys pointing at them
(`spread_research_artifact`/`total_research_artifact` for NBA/NFL) are
never read by any code path at all, so this remains a dormant,
zero-blast-radius provenance inconsistency (most likely: someone
hand-edited these two specific files after their hash was originally
frozen, adding the "Outcome matching pending" annotation visible in their
JSON, without recomputing the hash) rather than anything newly introduced.
Not re-fixed this pass, for the same reason it wasn't fixed when first
found: touching a hash-verification artifact that nothing consumes is
lower-value than the real, live-code-reachable findings this session
prioritized instead.

**Config artifact resolution**: swept every `production_artifact`/
`research_artifact`/`spread_research_artifact`/`total_research_artifact`/
`artifact` key across every sport block in `config/model.yaml`, checking
each declared path actually exists on disk. One miss:
`market_residual.artifact -> config/models/market-residual-v1.json` — the
same pre-existing, already-diagnosed dormant finding (a manual-only
`train-residual` CLI command's own output path; nothing in `daily` or any
other automatic path ever reads or writes it, so a config entry pointing
at a file that has simply never been generated yet is expected, not
broken).

**Runtime, without writes**: `model-prediction summary` returned real,
current numbers — `model_drift.MLB: {"holdout_hr": 0.5847, "live_hr": 0.8,
"n": 10, "status": "on_track", "z_score": 1.38}` (the v7 model's real
walk-forward holdout hit rate vs. its real recent live hit rate, still
within a statistically unremarkable z-score of each other — no drift
alarm), `open_picks: 18`, `picks_settled_today: 9`,
`qualified_pnl_units_all_time: 2.0411`. Dashboard `/api/health` returned
`{"ok": true}`; `/api/status` reported `models_loaded: 36` (up from 35 at
the session's earlier mid-point check — the esports v5 rebuild's 5 new
artifacts, net of nothing being removed from the loaded set) and real,
currently-advancing per-sport game counts, notably **kbo: 7039** and
**npb: 3925** — both higher than the 7,034/3,925 (kbo)/(already-current
npb) counts seen immediately after this session's own KBO/NPB staleness
fix was first deployed a few hours earlier, live confirmation that the
new `_refresh_international_baseball_ratings` step is continuing to pull
forward on its own during normal operation, not just the one time it was
manually invoked to prove the fix worked.

**Ruff**: `ruff check src/ tests/` -> **118 findings, byte-identical to
the baseline** established at the very start of this session and
re-confirmed after every single fix along the way — meaning across
everything this session touched (a genuine two-digit number of source
files across models, ledger core, dashboard, CLI, and tests), the net new
ruff-visible issue count is exactly zero. (`dashboard_server.py` carries
its own separate, never-tracked-as-baseline 47 findings, confirmed via a
targeted line-range check to be entirely pre-existing and untouched by
this session's specific edits to that file.)

**`daily`** (the actual live production pipeline — settlement, ingestion,
and the full forecast/logging pass across every sport, now including the
newly-wired `_refresh_international_baseball_ratings` step from earlier
this session): exceeded the 120-second foreground timeout as expected
(this command always does against real production data) and completed in
the background — **exit code 0**, and a full scan of its complete JSON
output for any `"failures": [...]` entry, any `error`/`Error`/`Traceback`
string, anywhere across every sport's settlement/forecast block, found
**nothing**. Re-ran `verify-chain` and the full test suite immediately
afterward, specifically because `daily` is the one command in this whole
checklist that actually *mutates* live ledger/audit state rather than
just reading it: `verify-chain` still `break_count: 0`/`chain_intact:
true` (audit log grew from 34,748 to a further-advanced line count,
entirely from this one real run, with zero breaks introduced), and the
full suite still **524/524 passed** — confirming the live pipeline run
itself didn't leave the repository in a state that breaks anything this
checklist already re-verified moments before.

This is, deliberately, the single most boring section in this entire
document — every number in it either matched what was already expected
from the individual fixes' own verification, or was a pre-existing, only
findings, nothing new, everything green. That is exactly the intended
outcome of running a full reproduction checklist after a long session of
changes: not to discover something new, but to confirm that the sum of
many individually-verified small changes is still, in fact, a coherent,
still-passing whole.

## 2026-08-01 — fourth full-project pass: a real gap in the live-money SELL path, a dormant P&L-formula duplication risk closed with a safeguard test instead of a refactor, one rate-limit-diagnostics finding investigated and deliberately left alone, and a self-inflicted test flakiness bug found and fixed along the way

Fourth parallel read-only review pass, this one scoped to
`dashboard_server.py`'s own computation logic (its wiring to the rest of
the project had already been checked in earlier passes; this time the
question was "is the arithmetic and the safety-gate logic *inside* this
file correct") and `data_sources/*.py` (the actual HTTP-fetching/parsing
layer every model depends on — ESPN, Polymarket, The Odds API, official
KBO/NPB pages). Same discipline as the three passes before it: every
finding traced and verified directly against the code, nothing acted on
from the review agent's word alone.

### Fixed: BUY orders check quote freshness and game-start; SELL orders checked neither

This is the most consequential finding of the whole four-pass series
because it sits directly on the real-money order-execution path, so it's
documented here in full rather than summarized.

**How it was found**: the review agent flagged that `_order_readiness`
(`dashboard_server.py`) — which requires a quote to be fresh (observed
within 5 minutes), the market to be `MARKET_STATE_OPEN`, and the game to
not have already started, before a BUY can proceed — is never called at
all for SELL actions. `preview_order`'s and `submit_order`'s sell branches
each independently check only `bid is not None and price <= bid` (a
"don't cross the resting bid" check) and nothing else. There's an
explicit, deliberate-reading comment in the code: *"Buys require the
buy-readiness gate. Sells are exits and only require an executable quote
(you can always try to close a position you hold)."* — meaning this wasn't
an oversight so much as an intentional design choice that turned out to
have a real, unconsidered consequence.

**Why it matters, traced all the way through rather than assumed**:
`_pick_quote` (the function both BUY and SELL rely on for a quote)
explicitly and permanently excludes any Polymarket snapshot observed at or
after `event_start_utc` — by design, since this project has no live
in-game pricing mechanism at all, only pregame BBO capture. That means the
instant a game starts, `_pick_quote` for that row freezes forever at
whatever the last pregame snapshot happened to be — it will never update
again, not in five minutes, not ever, for the rest of that row's life.
For BUY, this is caught immediately: `_order_readiness`'s "game has
already started" check refuses the order outright. For SELL, nothing
catches it — a user (or a script acting on their behalf) could submit a
resting sell limit that gets validated against a bid frozen from hours (or
days) before, with the dashboard reporting it as a normal "doesn't cross
the bid" resting order, when the real, current market could be
*anywhere*. This isn't a "your information might be a few minutes stale"
risk like ordinary quote staleness — it's a structural guarantee that the
comparison being used is definitionally disconnected from the live market
the moment a game goes live.

**Checked whether a deeper layer catches this anyway, rather than assuming
the dashboard is the only gate**: read the entire `PolymarketExecutor.execute()`
gate chain in `data_sources/polymarket_execute.py` line by line — ticket-
to-row identity binding, server-recomputed cost (never trusts the caller's
`estimated_cost_usd`), qualification/artifact-qualified distinction,
open-status check, price/size sanity bounds, whole-cent tick enforcement,
unit-cap enforcement, order-type whitelist, interactive human confirmation,
audit-chain write. Genuinely thorough — and genuinely contains **zero**
staleness or market-state checks anywhere, for either buy or sell. The
dashboard's `_order_readiness` (skipped for sells) really was the only
line of defense in the entire system for this specific failure mode.

**Asked before fixing, because this is a real, deliberate-looking design
choice, not an obvious oversight**: presented three options — block sells
once the game has started (narrowest fix, preserves "always allowed to
try exiting" for genuine pregame staleness, which the removed comment was
plausibly written to protect); make sells fully symmetric with buys
(refuse any sell, pregame or in-game, past 5 minutes of quote age); or
leave sell behavior untouched and treat the human confirmation step as
sufficient protection. Operator chose the first, narrowest option.

**Implementation**: extracted the inline "is this event in the past"
check that `_order_readiness` already did into a standalone
`_event_already_started(row) -> bool` helper (previously it was ~6 lines
duplicated nowhere else, now genuinely shared), and added a call to it at
the top of both `preview_order`'s and `submit_order`'s sell branches — a
sell against an in-progress-or-finished game's frozen quote is now
refused with `"game has already started; quote can no longer update"`,
matching the clarity of BUY's existing refusal reasons. Deliberately
re-checked at *both* preview and submit time, not just preview — state
(specifically, whether the game has started) can change in the window
between a user previewing an order and actually submitting it, and
`submit_order` already independently re-validates several other
conditions for exactly this race-window reason, so this needed the same
treatment rather than trusting preview-time state to still hold.

**Verified the fix doesn't regress the legitimate case it wasn't meant to
touch**: `preview_position_sell`/`submit_position_sell` — a second,
entirely separate sell pathway used for closing a position found via
`live_portfolio_view` rather than a specific ledger row — turned out to
already use `_live_bbo(slug)`, a genuine real-time exchange query, not the
frozen pregame cache `_pick_quote` relies on. Read through both functions
end to end to confirm this before concluding no fix was needed there; this
second pathway was never vulnerable to the same failure mode in the first
place, so it was correctly left untouched.

Three new tests, all against the exact `preview_order`/`submit_order`
functions rather than a simulated subset of their logic: a sell refused
once `event_start_utc` is in the past; a sell still allowed (unchanged
behavior) when `event_start_utc` is in the future; and a preview-then-
submit race test where the game starts in the window between the two
calls, confirming `submit_order`'s independent re-check catches it even
when `preview_order` originally allowed it. All three, plus the full
existing 42-test dashboard suite, passed with zero other changes needed —
this exact code path (the ledger-row-based sell branches specifically, as
opposed to the separate position-sell pathway) had **zero** prior test
coverage at all before this pass, buy or sell, which is very plausibly
why this gap went unnoticed through however many sessions of prior work
touched this file.

### Investigated, deliberately fixed with a test instead of a refactor: `_decorate_pick`'s hand-duplicated P&L fallback

`_decorate_pick` recomputes win/loss profit from raw American odds by hand
whenever both `pnl_units` and `research_pnl_units` are zero/blank — a
second, independently-written copy of `pricing.profit_units`'s math,
living in the dashboard rather than imported from the package. Currently
arithmetically identical, but a second hand-maintained copy of settlement
math is exactly the class of risk this whole four-pass series kept
surfacing elsewhere (the Elo formula duplicated four times, `_team_matches`
almost duplicated a second time in the CLV work before being reused
instead).

**Checked how often this fallback actually fires against real data before
deciding how seriously to treat it**: swept every settled row across all
four live ledgers (Main, Flat, Research, Gated Research) checking whether
both `pnl_units` and `research_pnl_units` were ever simultaneously
zero/blank on a real win/loss row. Real count: **zero**. Every settled row
`ledger.settle()` has ever touched already carries a real, correctly-
computed P&L value from the one true implementation; this fallback exists
purely as a defensive net for malformed or legacy data that has never
actually needed to fire.

**Why the obvious fix (import `pricing.profit_units`) was deliberately not
taken**: `dashboard_server.py` has zero imports from the `model_prediction`
package anywhere in its ~4,700 lines — confirmed by grepping the whole
file for `from model_prediction` / `import model_prediction`, finding
nothing, and checking there's no `sys.path` manipulation making the
package importable either. This is a long-standing, consistent,
file-wide pattern (openpyxl itself is even wrapped in a try/except
optional-import), strongly suggesting a deliberate design goal: keep this
file runnable as a genuinely standalone script with no dependency on the
full modeling package's import graph. Introducing the *first-ever*
cross-import here, to fix a formula divergence risk that currently affects
zero real rows, would be a disproportionate architectural change relative
to the actual risk.

**What was done instead**: added a comment at the fallback site explaining
exactly why it's not an import and pointing at the safeguard; added
`tests/test_dashboard_server.py::test_pnl_fallback_formula_matches_pricing_profit_units`,
which sweeps a representative grid of American odds (-500 through +1000)
and unit sizes (0.5 through 2.0) across both win and loss outcomes and
asserts the dashboard's hand-rolled formula produces bit-for-bit the same
answer as the real `pricing.profit_units` for every combination. If either
formula is ever edited without updating the other, this test fails loudly
the next time the suite runs — the actual risk (silent divergence) is
closed without paying the architectural cost of the more invasive fix.

### Investigated and deliberately left alone: rate-limit errors from The Odds API are indistinguishable from a genuine "no market" outcome

`MLBMarketOddsFeed.load()`/`for_game()` (`data_sources/mlb_market_odds.py`)
catch `httpx.HTTPError` (which includes `HTTPStatusError`, e.g. a real 429
rate-limit response) around both the Polymarket and Odds API fetches, and
both a genuine rate-limit and a genuine "there's really no market for this
game today" collapse into the same `MarketUnavailableError
("NO_CALL_MARKET_UNAVAILABLE (...)")` exception type. The error detail
string embedded in the message *does* already distinguish them
(`type(error).__name__` — "HTTPStatusError" vs. e.g. "ConnectError" or
"TimeoutException" — is recorded verbatim), but nothing downstream parses
that string; every caller just treats the exception as "skip this game."

Checked whether this actually causes a different real-world outcome
between the two cases before deciding whether to fix it: it doesn't.
This project has no in-process retry logic anywhere for this kind of
transient failure — every model's real recovery mechanism is the
already-installed, already-verified launchd job re-running the full daily
pipeline every 3 hours regardless of what happened the run before. A
rate-limited fetch this run and a genuinely-absent market this run produce
the exact same operator-visible outcome (one game skipped, picked back up
automatically next cycle if it was transient) either way. The only real
value of distinguishing the two would be operator debugging convenience
("why didn't this game get priced" — being able to tell "we got
rate-limited" from "there's really no market" faster), not correctness.
Left alone as a legitimate but low-value diagnostics improvement, not a
bug — the lowest-severity of the three findings from this pass, correctly
so.

### A self-inflicted bug, found while re-running the suite after the above: a CLV test computed its own snapshot date from raw UTC instead of Eastern time

While re-running the full suite to confirm the SELL-path fix,
`tests/test_fix_regressions.py::test_esports_settlement_populates_clv_from_captured_closing_snapshot`
(written earlier this session, during the CLV wiring work) failed for the
first time — not because of anything touched in this pass, but because
real wall-clock time had drifted close enough to a UTC/US-Eastern day
boundary to expose a latent bug in the test's own fixture setup: the test
computed the snapshot's storage date via
`datetime.fromisoformat(event_start).date().isoformat()` (raw UTC date),
while the actual production code under test
(`_closing_probability_for_moneyline_pick`) correctly computes
`game_date` via `.astimezone(EASTERN).date().isoformat()`. These two
computations agree for the vast majority of the day and disagree for a
few hours near midnight UTC (which is late afternoon/evening US-Eastern,
depending on DST) — meaning this test was quietly relying on being run at
a time of day where the mismatch didn't matter, and finally wasn't. Fixed
by making the test compute `game_date` identically to the production code
it's testing, which is what it always should have done — a reminder that
"uses `datetime.now()`-relative fixture data" tests need to mirror
timezone-conversion logic exactly, not approximately, or they become
silently time-of-day-dependent. Re-ran the full suite immediately after:
524/524 passed, no other casualties from the same class of issue found.

### Verification

524/524 tests passed (4 net new: 3 for the SELL-path fix, 1 for the P&L
formula safeguard), `ruff check src/ tests/` unchanged at the 118-finding
baseline (confirmed dashboard_server.py's own separate 47 pre-existing
findings, never part of this tracked baseline, were unaffected by this
pass's edits by checking the specific line ranges touched), `verify-chain`
0 breaks, dashboard server process killed and restarted to load the new
code (confirmed via `/api/health` and a clean `server.log` with no
startup errors).

## 2026-07-31 (latest, latest) — the sizing formula's dead parameter, finally fixed; the unit range widened to 1U-2U; full derivation, full blast-radius accounting, full before/after arithmetic

This section is deliberately exhaustive — every number in it was recomputed
by hand against the actual formula in `units.py`, not paraphrased from a
test's pass/fail status, because the previous version of this exact
formula shipped for an unknown but clearly nonzero length of time with a
parameter that was *accepted, threaded through six call sites, and never
read*, and the reason nobody caught it for that long is that every single
one of those six call sites, and every existing test, was content to check
"does this produce *a* plausible-looking unit size," never "does changing
*only* the uncertainty input change the output at all." That is exactly
the kind of bug that a five-word regression test closes forever, so this
section spells out the derivation in full precisely so nobody has to
re-derive it from scratch the next time this formula's correctness is in
question.

### The formula, before

```python
def edge_scaled_units(model_probability, model_uncertainty, american_odds, policy=UnitPolicy()):
    edge = abs(model_probability - 0.5)
    raw = policy.min_pick_units + edge * (policy.max_pick_units - policy.min_pick_units) / 0.15
    units = max(policy.min_pick_units, min(policy.max_pick_units, raw))
    units = round(units / policy.unit_increment) * policy.unit_increment
    return min(units, policy.max_pick_units)
```

Trace every reference to `model_uncertainty` in that body: there are
zero. It is a formal parameter, appears in the function's own signature,
appears in every caller's argument list, and is never read. Six call
sites pass a real, meaningfully-varying value into it for nothing:
`eligibility._call_result` (the real qualified-CALL sizing path, used by
every sport's Main/Flat ledger row), `eligibility._research` and
`eligibility._downgrade_research_call` (the "every pick gets units" paths
fixed earlier this session), `ledger.recompute_research_sizing` (the
backfill helper used three separate times already this session), the
`model_recommended` scoring mode inside `ledger.settle()`, and
`recommend_units`'s own internal override (see below — this one is
subtler and gets its own subsection). Every one of those six believed it
was passing uncertainty-aware information into the sizing calculation.
None of them were.

### Concretely, what this meant in practice

Two picks, same `model_probability = 0.65`, same `american_odds = -110`:

- Pick A: `model_uncertainty = 0.01` (the model is very sure of this 65%)
- Pick B: `model_uncertainty = 0.20` (the model computed 65% but flagged
  enormous uncertainty around that estimate — maybe thin recent data, a
  team with almost no rating history, a feature that had to default to
  neutral because real data was unavailable)

Before this fix: **both got sized identically.** `edge =
abs(0.65-0.5) = 0.15`, which is already at the formula's own saturation
point (`0.15` is the denominator — any edge at or above it saturates to
`max_pick_units`), so both Pick A and Pick B were sized at the maximum,
2.0U, with zero distinction between "the model is confident and right" and
"the model computed a number it doesn't really trust." A system that is
supposed to size bets by conviction had, for its most standard sizing
path, no mechanism whatsoever connecting stated uncertainty to stake size.

### The fix

```python
adjusted_edge = max(0.0, abs(model_probability - 0.5) - max(0.0, model_uncertainty))
raw = policy.min_pick_units + adjusted_edge * (policy.max_pick_units - policy.min_pick_units) / 0.15
```

The uncertainty is subtracted from the raw distance-from-50/50 *before*
that distance gets scaled into a unit size, floored at zero (an
uncertainty larger than the raw edge collapses the adjusted edge to
nothing, never a negative number, never inflating sizing). This is not an
invented convention — it is the exact same "probability minus uncertainty"
conservatism `recommend_units`'s own accept/reject gate already applied a
few lines below (`adjusted_probability = model_probability -
model_uncertainty`, used to decide whether the pick clears `min_edge` at
all) — that gate was already correctly uncertainty-aware; the sizing
formula sitting right next to it, doing conceptually the same kind of
haircut, simply never got the memo. Recomputing the two picks above with
the fix: Pick A (`u=0.01`): `adjusted_edge = 0.15 - 0.01 = 0.14`, sizes to
essentially the max, 2.0U. Pick B (`u=0.20`): `adjusted_edge = max(0,
0.15-0.20) = 0.0`, sizes to the floor, now 1.0U (see the range change
below). Same raw probability, now genuinely different stakes, exactly
tracking how much the model itself trusts the number.

### The `recommend_units` half of this bug, and why fixing `edge_scaled_units` alone also fixed it

`recommend_units` (the fuller sizing function, used wherever exposure caps
and a real Kelly comparison matter, not just the raw edge-scaled override)
calls `edge_units = edge_scaled_units(model_probability, model_uncertainty,
american_odds, policy)` and then `units = max(units, edge_units)` — takes
whichever of Kelly-sizing or edge-scaled-sizing is larger. The
full-project logic review flagged this as a *separate*, second-order
problem worth noting on its own: `recommend_units`'s accept/reject gate
uses the properly uncertainty-adjusted probability
(`model_probability - model_uncertainty`), but the `edge_units` line right
below it was handing that same conservatism-blind, dead-parameter function
its `model_uncertainty` argument for nothing — meaning a pick that barely
scraped past the gate specifically *because* of a large uncertainty
haircut could still get sized as if the model were fully confident, via
the `max(units, edge_units)` comparison picking the (wrongly) larger,
uncertainty-blind `edge_units` value over the correctly-smaller Kelly
value. Once `edge_scaled_units` was fixed to actually read
`model_uncertainty`, this entire second-order problem disappeared for
free — `edge_units` is now itself uncertainty-aware, so `max(units,
edge_units)` is comparing two properly-conservative numbers instead of one
conservative and one not. No separate code change was needed in
`recommend_units` itself; it was never the buggy one, it was just
downstream of the one that was.

### The unit range: 0.5U-2.0U -> 1.0U-2.0U

Separate, operator-directed change, made in the same sitting: the
project's own sizing floor/ceiling (`UnitPolicy.min_pick_units`/
`max_pick_units`, both in the `units.py` dataclass default and in
`config/model.yaml`'s `bankroll:` block, which is the value actually used
in production via `config.unit_policy()`) moved from 0.5-2.0 to 1.0-2.0.
1U is now the floor for the *least* confident logged pick (previously
0.5U); 2U remains the ceiling for the *most* confident, unchanged. Because
`edge_scaled_units`'s formula references `policy.min_pick_units`/
`policy.max_pick_units` symbolically rather than hardcoding 0.5/2.0
anywhere in the arithmetic, this was a pure config/default change with
zero formula-code changes required — the existing scaling math
automatically stretches to fill whatever floor-to-ceiling span the policy
declares. Verified this is genuinely true rather than assumed: at
`adjusted_edge=0`, `raw = min_pick_units` exactly (now 1.0, was 0.5); at
`adjusted_edge=0.15` (the saturation point), `raw = min_pick_units +
0.15*(max-min)/0.15 = max_pick_units` regardless of what min/max actually
are. The 0.15 saturation threshold itself (an edge of 15 percentage points
or more maxes out sizing) was deliberately left untouched — that constant
describes "how much edge is 'a lot,'" a property of the *market*/model
calibration, not of the unit range convention, and changing it wasn't part
of what was asked.

### Blast radius, checked exactly rather than assumed

Ran the full test suite immediately after the `edge_scaled_units` formula
fix, before touching the unit range: **515/515 passed, zero test changes
required.** This was initially surprising enough to double-check rather
than accept at face value — confirmed by hand-computing `edge_scaled_units`
before/after for a probability/uncertainty pair with a large uncertainty
value (0.65 / 0.01 vs. 0.65 / 0.20) outside a Python REPL and observing
real, different outputs (2.0 -> 1.0 -> 0.5 as uncertainty rose from 0.01
to 0.20 at the OLD 0.5-2.0 range) — the fix is real, it's just that no
existing test happened to assert an exact `units` value for a call with a
large-enough `model_uncertainty` to cross an 0.25-unit rounding boundary
under the old 0.5-2.0 range. This is itself worth remembering as a
lesson: 515 green tests is not the same claim as "this code path is
covered" — it only means "nothing that already existed noticed."

Widening the unit range to 1.0-2.0 afterward broke exactly one test
(`test_unvalidated_model_is_capped_at_research_minimum`, asserting the old
0.5U floor) — updated to assert 1.0U, the new, correct floor. Added five
new tests specifically exercising the previously-uncovered behavior:
uncertainty monotonically shrinks sizing for a fixed probability;
uncertainty larger than the raw edge floors at `min_pick_units` rather
than going to zero or negative; a (should-never-happen, but must not
silently misbehave) negative `model_uncertainty` is treated as zero rather
than as a sizing *boost*; an exact-50/50 probability still sizes at the
floor regardless of uncertainty (uncertainty only ever shrinks an existing
edge, it doesn't independently gate the floor); and the new 1.0-2.0 range
is asserted directly against the policy object rather than trusted from
downstream test behavior alone.

Full suite after both changes: **520/520 passed** (5 net new tests). Ruff:
118 findings, identical baseline, nothing new. `verify-chain`: 0 breaks.

### Real-data reconciliation — what actually happens to already-logged rows

Deliberately did **not** retroactively resize already-logged
`QUALIFIED_SHADOW_CALL` rows in Main or Flat — this matches this
project's own pre-existing, deliberate convention
(`recompute_research_sizing`'s own docstring: "QUALIFIED_SHADOW_CALL rows
are never touched: their sizing always came from the real, validated,
exposure-capped path, not this one"). A real qualified call's logged size
is treated as an immutable record of the actual decision made at the time,
not something that gets silently rewritten underneath a human who may
already be acting on it. Confirmed this by running
`recompute_research_sizing` against `data/picks.xlsx` directly and
observing `changed: 0` — exactly the expected outcome, not an oversight.

Did re-run `recompute_research_sizing` (with the now-corrected formula and
policy) against every ledger that legitimately holds
`RESEARCH_OBSERVATION` rows -- `data/flat_picks.xlsx` and every
per-sport Research/Gated-Research workbook -- since those are diagnostic,
zero-real-money sizes by explicit design, and this exact recompute
operation has already been used twice earlier this session for
analogous reasons (once to backfill the "every pick has units" fix,
implicitly again during the esports v4->v5 archival). Real counts: 15
rows changed in `flat_picks.xlsx`, 10 in `research/cs2.xlsx`, 7 in
`research/soccer.xlsx`, 40 in `research/tennis.xlsx` (the largest, since
tennis's `status: research` promotion state means essentially its entire
history runs through the uncertainty-blind `_research()` path), 3 in
`research/valorant.xlsx`, 2 each in `research/dota2.xlsx` and
`research/lol.xlsx` — 79 rows total, all now reflecting the corrected
formula and the widened range. `verify-chain` re-checked clean afterward
(0 breaks), full suite re-confirmed green.

## 2026-07-31 (later) — third full-project pass: 4 more real bugs (2 MLB feature, 2 core-ledger dead/miscalibrated parameters), 1 real gap (KBO/NPB rating staleness) fixed with a self-healing fallback

Third round of "find what else can be improved," split across three more
parallel read-only review passes: the shared ledger/eligibility/sizing/
pricing code every sport depends on (untouched by the two earlier passes,
which focused on per-sport model files), the remaining MLB feature
providers not yet reviewed (park factor, weather, bullpen, WNBA
availability, pitcher ERA gap) plus two flagged-but-never-reviewed modules
(`research_cleanup.py`, `research_io.py`), and a dedicated "gaps" pass
looking specifically for missing/half-wired functionality rather than wrong
math. Verified every finding directly against the code before acting, same
discipline as the prior two passes.

### Fixed

**1. MLB: `features/weather.py`'s ballpark coordinate table still keyed the
Athletics under their old franchise name.** `BALLPARK_COORDS["Oakland
Athletics"]` never matched the team name the rest of the pipeline actually
uses -- `park_factors.py` and `mlb_baseline_refresh.py` were both already
updated to ESPN's live `displayName` ("Athletics"), the latter explicitly
excluding "Oakland Athletics" as a legacy name via `_LEGACY_PARK_NAMES`.
`BALLPARK_COORDS.get("Athletics")` returned `None`, silently falling every
Athletics home game to `status="unknown_park"`/`weather_run_factor=1.0` --
losing real weather signal specifically for the team now playing outdoors
in Sacramento, where weather should matter more than most parks, not less.
Fixed: renamed the key and updated coordinates to Sutter Health Park, West
Sacramento (38.5805, -121.5195).

**2. MLB: the live weather feature provider fetched "weather right now"
instead of "weather at first pitch."** `learned_forward.py`'s
`weather_factor` provider called `live_weather(home_team)` with no
timestamp at all, even though `live_weather` already accepts a
`game_start_utc` argument specifically for this, and the real event start
time was already sitting in scope at the call site the whole time -- just
never threaded through the shared 4-argument provider-callback signature.
A slate built hours before first pitch (e.g. a morning cron run building an
evening slate) got a stale "current moment" reading instead of the
forecast for game time -- a real train/serve skew, since
`validation.py`'s training-time weather lookup correctly keys off
`game.start`, not "now". Fixed by widening the provider callback signature
from `(home, away, event_id, game_date)` to `(home, away, event_id,
game_date, event_start)` -- park_factor and probable_starter_era_gap
ignore the new argument, weather_factor now actually uses it.

**3. Core ledger: `edge_scaled_units` silently ignores its own
`model_uncertainty` parameter.** `units.py`: the function accepts
`model_uncertainty` and every caller (`eligibility._call_result`,
`_research`, `_downgrade_research_call`, `ledger.recompute_research_sizing`,
`settle`'s `model_recommended` research-scoring mode, `recommend_units`'s
override) already threads a real, varying value through -- but the
function body never reads it. Two picks with identical
`model_probability` get identically-sized stakes regardless of whether the
model's own uncertainty on that pick is 0.01 or 0.49. Since this is now the
dominant sizing path project-wide (every real CALL, plus every research
observation after the "every pick has units" fix earlier this session),
model confidence has zero effect on stake size anywhere in the system --
only raw distance from 50/50 matters. Not fixed yet in code as of this
writing -- flagged here with enough detail to act on; genuinely changing
a live sizing formula this central deserves an explicit go-ahead and a
before/after backtest comparison, not a silent mid-audit edit.

**4. KBO/NPB: no mechanism kept Elo ratings current (the exact gap already
found and fixed for esports).** Confirmed live before touching anything:
`kbo-tie-aware-elo-v1.json` was 6 days stale, `npb-tie-aware-elo-v1.json`
14 days stale, and nothing in `dashboard_server.py`'s status/alert logic
surfaces *artifact* staleness at all -- only a much looser 30-day check on
the raw games-manifest age, so an operator would see no alert while
ratings quietly rotted for weeks. `esports.py` already had
`refresh_recent_matches` (fetch a short recent window, merge by stable ID
into existing history, never overwrite) wired into `daily` via
`_refresh_esports_ratings`; `international_baseball.py` had no equivalent
-- only a full-window `backfill_international_baseball` that replaces
`games.jsonl` entirely for whatever range it's given, unsafe to run on a
schedule with a short window (would delete every prior season) and
expensive to run with the full multi-year range (2015-present) every day.

Built `refresh_recent_international_baseball_matches`, mirroring esports'
pattern but adapted to this source's actual shape (KBO/NPB's official
schedule client fetches a whole *year* at a time, not a date-windowed
page, so "recent" here means "re-fetch the current calendar year and merge
by `game_id`" rather than a 14-day lookback -- a KBO/NPB season is a few
hundred games, cheap enough to refetch daily, unlike esports' tens of
thousands of matches per title). Wired into `daily` as
`_refresh_international_baseball_ratings`, matching the esports call
exactly (refresh, re-validate, rewrite the artifact and the dashboard's
evidence-consistency snapshot).

**Self-healing fallback (operator-requested, not part of the original
finding)**: if `games.jsonl` doesn't exist for a league at all -- first run
ever, or every prior daily attempt failed before writing anything -- a
current-year-only refresh would permanently strand every earlier season
with no way to recover automatically. `refresh_recent_international_baseball_matches`
checks for this and falls back to a real full `backfill_international_baseball`
(the league's actual `minimum_year`, 2015, through today) in that case,
self-healing without requiring a manual `backfill-international-baseball`
CLI invocation. Also directly addressed a related operator question ("if I
run offline for a week, does that week's data come back?") -- yes, by
construction: since the normal path always re-fetches the *entire* current
calendar year (not an incremental delta since last success), any
in-season gap of any length is fully caught up on the next successful run,
regardless of how long the gap was or whether the immediately-prior
attempt failed. The one real edge case is a gap spanning an actual
calendar-year boundary (e.g. offline Dec 31, back online Jan 5) -- a
current-year-only fetch would never revisit the tail of the old year. Not
a practical risk for these two leagues specifically since KBO/NPB seasons
run March-October; a year-boundary gap only touches the off-season, when
there are no real games to miss.

Two new tests: the normal-path merge (existing older season preserved,
only the current year re-fetched and merged in, verified via a fake client
that records exactly which years were requested) and the fallback path
(no existing file -> every year from `minimum_year` requested, confirming
a real multi-year backfill actually ran instead of a narrow one).
Live-verified against real production data, not just the test suite:
before this fix, `kbo-tie-aware-elo-v1.json`/`npb-tie-aware-elo-v1.json`
were dated 2026-07-25/2026-07-17; ran the new refresh for real (genuine
network calls to the official KBO/NPB schedule pages) and both artifacts'
`trained_through_date` advanced to 2026-07-31/2026-07-26 immediately
afterward.

### Investigated, reported, deliberately not silently fixed

- `recommend_units`'s edge-scaled override sizes off raw (not
  uncertainty-adjusted) probability while its own accept/reject gate uses
  the uncertainty-adjusted one -- compounds finding #3 above. Flagged, not
  changed, for the same reason: a live sizing-formula change needs an
  explicit go-ahead.
- `PickLedger.exposure()`'s same-bet self-collision exclusion matches only
  `event_id + market_type + selection`, not `line`/`sportsbook` -- two
  genuinely different open positions (a moved line, or the same bet shopped
  across two books) could be treated as one decision's own prior instance
  and excluded from exposure sums, undercounting real risk. Flagged,
  currently low real-world impact since exposure caps no longer gate
  CALL vs. NO_CALL project-wide (per this session's earlier "operator
  directive, 2026-07-26" work) -- exposure is now informational, not a
  live safety gate, so this doesn't currently risk over-betting.
- `_market_duplicate_key` (the ledger's dedupe key) excludes `selection`
  by design (correct for 2-way spread/total markets, where both sides can
  never both be legitimate), but soccer moneyline is a real 3-way market
  (home/draw/away) and the key would treat a "home" pick and a "draw" pick
  on the same event/book/model as duplicates of each other. Checked
  whether this is reachable: soccer's actual moneyline selection logic only
  ever picks `max(("home","away"), ...)` -- "draw" is never constructed as
  a `PickRequest` selection anywhere in the codebase today, so this is a
  real latent risk, not a currently-firing bug. Flagged for whoever adds a
  real draw market in the future rather than fixed now, since the fix
  (excluding `selection` from the key is deliberate for other markets)
  needs a market-type-aware key, not a blanket change.
- `research_cleanup.py` (421 lines, previously assumed by an earlier
  review pass to be an active "supersession detection" system) turned out
  to be dead code in production -- its only caller is a manual one-time
  migration script (`scripts/clean_split_research_ledgers.py`), not
  anything `daily` or any other live path invokes. Its own dedup key
  (`_decision_key`) omits `decision`/`record_type`, which could in
  principle discard a genuine CALL in favor of an earlier NO_CALL row for
  the same market -- worth knowing if this script is ever run again, but
  not an active risk today since nothing calls it automatically.

## 2026-07-31 (later) — esports v4 -> v5 rebuild: proper scoring rules replace a P&L-proxy hyperparameter search, plus a 4-implementation Elo consolidation

Follow-up to the "esports/soccer validation" section below, which flagged
`SPORT_K_OVERRIDE` hardcoding K=96 (the exact top of `K_CANDIDATES`) for 4 of
5 titles but explicitly declined to touch it, citing genuine ambiguity from
only one validation/test split. Re-investigated after a fresh full-project
logic review flagged the same symptom independently and the operator asked
for it to actually be fixed this time.

**Root cause, now nailed down precisely (previous investigation didn't have
this)**: K selection was `max(candidate_scores, key=units_at_minus_110)` — a
raw, unnormalized flat-stake P&L sum — instead of `_metrics`' own `brier`
field, which was already computed on every candidate the whole time and
simply never used for selection (a stale docstring literally said "v4: was
min Brier", i.e. an earlier lineage got this right and v4 regressed it).
K is a pure calibration hyperparameter with no volume/quality tradeoff, so
selecting it by a P&L proxy instead of a proper scoring rule has no
principled justification.

**Fix, in two parts, verified empirically against real historical data
(not assumed) before shipping either one:**

1. K-factor: switched to `min(candidate_scores, key=brier)`. Also widened
   `K_CANDIDATES` from `(..., 96.0)` to `(..., 96.0, 112.0, 128.0)` since a
   grid-edge optimum is itself evidence the search was truncated, regardless
   of which metric picks it. Re-ran the real grid search against current
   data (larger than the last investigation's) with the new criterion:
   chosen K landed at 32/32/40/48/32 for lol/cs2/dota2/valorant/rainbow_six
   — every single one now interior to the grid, none at either edge.
   Removed `SPORT_K_OVERRIDE` entirely rather than reconcile it against the
   new auto-selection; there's no principled reason to keep a P&L-justified
   hand-pick once the selection method it was overriding is fixed.

2. Confidence threshold: **initially made the same change** (min Brier) for
   consistency, then caught it empirically breaking before deploying —
   printed the full `threshold_scores` table for all 5 titles and Brier
   improves *almost monotonically* toward the single most restrictive
   threshold every time (e.g. LOL: brier 0.220 at threshold 0.0 down to
   0.134 at threshold 0.30, the top of the widened grid), because
   restricting to an ever-smaller, ever-more-confident subset mechanically
   improves calibration score with no interior stopping point except
   running out of data (rainbow_six's top threshold had only 34
   observations left). `units_at_minus_110`, by contrast, has a genuine
   volume-vs-quality tradeoff baked in and empirically produces a real
   interior optimum (~0.03-0.05) for every title once the grid was widened
   enough to see it. Reverted threshold selection back to
   `units_at_minus_110` and documented in-code why the two hyperparameters
   need *different* selection criteria despite looking like the same kind
   of "pick the best row from a table" problem — this is the actual
   half-right insight the prior "v4: was min Brier" comment was gesturing
   at without ever articulating why K and threshold aren't interchangeable.

Bumped `ESPORTS_MODEL_LINEAGE` to `v5` (genuine methodology change, not a
data refresh) and re-ran `validate-esports --write-artifacts` for real,
which also regenerated `outputs/latest/esports-baseline-validation.json` --
necessary because `dashboard_server.py`'s `production_evidence()` compares
each configured artifact's hash against that snapshot file, and forgetting
to regenerate it after writing new artifacts made
`test_current_configured_production_artifacts_all_valid` fail with
`model_definition_and_backfill_valid: False` for all 4 titles (caught by
running the full suite before considering this done, not by inspection).

Real locked-test numbers for the new artifacts (out-of-sample, never seen
during K/threshold/Platt fitting): lol 70.1% (1899 selected calls), cs2
65.6% (6595), dota2 68.2% (1822), valorant 63.2% (2453), rainbow_six 66.9%
(511) -- all notably more honest and less inflated than the transient
"all thresholds want 0.15-0.30" numbers seen mid-investigation, which were
an artifact of the (correctly rejected) Brier-based threshold selection
cherry-picking tiny high-confidence subsets.

Updated `config/model.yaml`'s LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX blocks to
point at the new `-v5.json` artifacts and to each artifact's own
newly-validated `research_confidence_gate` (DOTA2 and VALORANT dropped from
0.05 to 0.03 -- the re-run search found the real interior optimum lower for
both once the P&L-proxy K stopped distorting the underlying ratings the
threshold search operates on).

Archived all 288 esports Research/Gated-Research rows still logged under
`-v4` (220 settled via `archive_settled_rows`, 62 open via
`remove_open_rows`) to
`data/archive/2026-07-31-retired-esports-v4-models/`, mirroring the MLB
v5/v6 -> v7 archival pattern exactly (a genuine, discrete model-version
retirement, not the continuous daily recalibration this project's esports
titles otherwise do -- the same distinction that made "leave esports alone"
correct for the *earlier* gate-tightening archival earlier this session).
One wrinkle worth recording: the archival script's own printed running
tally undercounted LOL's gated-research row count by 6 relative to the
pre-archival total; re-checked the live ledger file directly afterward (0
rows remaining, not 6) and cross-referenced the audit log, which showed
those 6 were independently cleared by the system's own automatic
"re-forecast replacement for 2026-07-31" mechanism around the same
wall-clock moment (ordinary daily-pipeline activity, unrelated to and not
conflicting with this archival) -- ground-truth-verified before writing the
final MANIFEST.json rather than trusting the script's own print statements.

**Separately, a real cross-cutting maintenance-risk fix**: four
independently-written from-scratch Elo implementations existed across the
project (`features/elo_ratings.py`'s `EloBook` for MLB/NBA/WNBA/NFL/soccer,
`esports.py`'s `NeutralElo`, `international_baseball.py`'s `HomeElo`, and
inline formulas in `models/tennis.py`) -- flagged by the full-project logic
review as "not a bug today, but a future fix to one won't propagate to the
others." Compared all four line-by-line: each has legitimately different,
sport-specific update mechanics (margin-of-victory scaling in three
different shapes, offseason regression vs. per-team decay, recency/
tournament-tier weighting for esports only, tie-handling for KBO/NPB) that
would be reckless to force into one shared update rule without a much
larger, higher-risk rewrite. But all four independently re-derived the
exact same core Elo logistic formula (`1/(1+10^(-diff/400))`) -- the one
place a sign error would be most catastrophic and hardest to notice, since
it would silently reverse every single prediction for that sport. Extracted
that one formula into a shared `expected_win_probability(rating_a,
rating_b, advantage=0.0)` in `features/elo_ratings.py` and pointed all four
call sites at it, leaving every implementation's own update/decay/margin
logic untouched. New `tests/test_elo_ratings.py` (6 tests: coinflip
symmetry, monotonicity, home-advantage direction, the textbook ~10:1 odds
at a 400-point gap, and an explicit regression check against the exact
pre-consolidation formula). Full suite (513 tests, +6 net from this and the
esports rebuild) confirmed byte-identical prediction behavior before and
after -- no existing test's expected numbers changed, which is exactly what
a pure refactor with zero behavior change should produce.

Verification: 513 tests passed, ruff 118 findings (same baseline, nothing
new), `verify-chain` 0 breaks, a live `daily` run completed with zero
failures across every sport, and directly re-queried the live ledgers
afterward to confirm 0 `-v4` esports rows remain anywhere and only `-v5`
rows are being logged going forward.

## 2026-07-31 (later still) — every logged pick gets real units and P&L, dashboard parity fixes, and a real MLB spread/total wrong-side selection bug

Four related fixes from one long stretch of the same session, driven by the
operator directly reading dashboard screenshots and asking pointed
questions rather than by a proactive audit -- worth recording that the
review-by-suspicious-user-questions method caught a real, live-money-
relevant bug (the last item below) that none of this session's several
prior code-only audits had surfaced.

### "Every pick should have units and pnl, hard code this"

Root cause: `eligibility.py`'s `_research()` (used for every NO_CALL reason
except team-banned: stale data, model-state-not-yet-promoted, missing
provenance) and `evaluate_gated_research_eligibility`'s
`_downgrade_research_call` (used when a row clears the base eligibility
checks but fails Gated Research's own edge/confidence bar) both hard-zeroed
`units` rather than using the same `edge_scaled_units` every real CALL
already uses. This meant two structurally different things were both
displaying as "no size, no P&L" in the dashboard: (a) genuinely-untrusted
rows (banned team -- correctly still zero, kept as the one exception) and
(b) perfectly good model opinions that just hadn't cleared a *curation* bar
(Gated Research) or a *promotion* bar (still `status: research`, e.g. every
single Tennis and Soccer row, since neither has been promoted past
research yet) -- there was no reason those should read as "the model had
no opinion" when it very much did.

Fixed both functions to compute a real `edge_scaled_units` size in every
case except `NO_CALL_TEAM_BANNED` (a banned team is never sized regardless
of model opinion -- matches `ledger.py`'s pre-existing
`call_type="no_call"` special case for that one reason). Extended
`ledger.py`'s pre-existing `recompute_research_sizing` (previously a narrow
allow-list of 3 "sizable" reasons) to size every reason except team-banned,
matching the code fix, and used it to backfill 235 already-settled
historical zero-unit rows across Research/Gated Research so old rows show
real sizes too, not just new ones going forward.

**Found the fix was incomplete on first pass**: reviewing the *Flat*
ledger specifically (not just Research/Gated Research) turned up 20 more
zero-unit rows -- MLB Measured Edge margin/totals rows logged via the
*legacy* `forward.py`/`_forecast_mlb_totals_flat` path, which also routes
through `eligibility.py`'s shared `_research()` and therefore had the exact
same latent zero-unit rows, just in a ledger file this pass hadn't
specifically re-checked yet. Ran `recompute_research_sizing` against
`data/flat_picks.xlsx` and `data/picks.xlsx` directly (not just the
per-sport Research/Gated-Research files) to close the gap -- a reminder
that "every ledger" literally means all four files, not just the two that
happen to be split per-sport.

### Dashboard sort order and KPI parity

Two related display bugs, both from directly reading pasted screenshots:

1. All four ledger tables (Ledger/Main, Flat, Flat Research, Gated
   Research) defaulted to sorting `event_start_utc` **ascending** (oldest
   game first) -- the opposite of what anyone actually wants when opening
   a live trading dashboard. Flipped the Main table's default `sortAsc`
   flag and the other three tables' hardcoded comparators to descending
   (`dashboard.html`'s `renderPicks`/`renderFlatTable`/
   `renderResearchTable`/`renderGatedResearchTable`).

2. Research and Gated Research tabs had no KPI summary card (games/win
   rate/P&L/ROI) at all -- Main and Flat both had one, Research/Gated
   Research never got the equivalent block added when those tabs were
   built. Added `#researchKpis`/`#gatedResearchKpis` divs and the matching
   computation, mirroring Flat's existing pattern exactly (computed from
   the currently-filtered row set, not the unfiltered ledger total, so
   "games" in the KPI card always matches what the visible table actually
   shows).

Also added a plain-English explainer to spread picks (operator: "if its
team +1.5, the dashboard should say no on team winning by 2") and then
reframed it to prediction-market YES/NO phrasing per a follow-up
clarification -- a spread pick now reads e.g. "YES: Los Angeles Dodgers
-1.5 (needs to win by 2+)" instead of a bare "Dodgers -1.5", since
`selection` always literally represents the side being bought (i.e. "YES"
on that specific named outcome), which is the correct, unambiguous framing
for a project built entirely on Polymarket-style binary contracts.

### The real bug: MLB spread/total still selected sides by expected value, not by the model's favorite

Investigating "shouldn't 7/30 have 30 picks (10 ML + 10 spread + 10
total)?" led to "I'm only getting 27 MLB for 7/30 in flat" led to checking
*why* 3 rows were missing from the dashboard's default view -- and all 3
had `model_probability < 0.5` for their own selected side, which is
mathematically impossible under correct probability-argmax selection (the
side that wasn't picked would necessarily have had the higher probability).

Root cause: `forward.py`'s `_paired_event_candidates` (feeds MLB spread/
total into Flat via the Measured Edge margin/totals models) still selected
`max(sides, key=lambda side: probability[side] * decimal_odds[side] - 1)`
-- expected value, not probability -- the exact same anti-pattern the
operator corrected system-wide on 2026-07-30 for MLB moneyline, esports,
and soccer (see that section below). This one file was missed during that
original revert because attention that day was on `learned_forward.py` and
the esports/soccer call sites, not this older, `forecast --model
legacy-measured-edge` code path that still feeds live Flat spread/total
picks today. Fixed to plain `max(sides, key=lambda side: probability[side])`,
matching every other sport. Added a regression test that specifically
constructs a probability-vs-price divergence (a 30% favorite at plus-money
odds vs. a 70% favorite at minus-money odds) so this exact class of
regression can't silently reappear a third time.

**Real, quantified impact on already-logged data**: exactly 7 of 44
Measured-Edge Flat rows (16%) were logged on the wrong side under the old
formula -- an exact test, not a sample, because "selected side's own
probability < 0.5" is only possible under the buggy EV-based selection
(spread/total probabilities for the two sides of a market are
complementary by construction, so the higher-probability side is
mathematically always the one with probability >= 0.5). Verified each of
the 7 against its own `rationale` text (which records both raw and
calibrated probability) before touching anything -- all 7 had raw
selected-side probability below 0.5 too (e.g. 0.3488, 0.3774), confirming
none were false positives from calibration shrinkage alone. 3 were settled
(archived via `archive_settled_rows`), 4 were still open (removed via
`remove_open_rows` and relogged correctly via a fresh `flat-forecast` run
once the code fix landed) -- to
`data/archive/2026-07-31-measured-edge-wrong-side-selection-bug/`.

Retroactive effect on the (already tiny, n<10 per market) settled-row
counts: totals improved 30.0% -> 37.5% (both removed rows were losses);
margin/spread ticked down 80.0% -> 77.8% (the one removed row happened to
have won by luck despite being the wrong side). Neither move reflects the
model getting better or worse -- the underlying math didn't change, only
the bookkeeping got corrected for picks that were never actually the
model's real opinion to begin with.

## 2026-07-31 (still later) — CLV wired for esports, tennis, and soccer moneyline

Operator directive: "implement clv for all ledgers and all picks." Turned
out the "settlement already computes CLV generically, just needs a closing
quote passed in" assumption undersold the real gap -- traced the actual
wiring sport by sport rather than assuming it worked everywhere `settle()`
technically accepts closing-price arguments.

**What was actually true beforehand**: `PickLedger.settle()`'s
`probability_clv` computation is genuinely generic (works for any ledger,
any sport) -- the gap was entirely on the *caller* side. MLB's generic
ESPN-based settlement path (`_settle_all_unsettled`) fetches a real closing
quote via `MarketOddsSnapshotStore.closing_quote()`, which is populated by
MLB's own Measured Edge forecast pass writing a combined moneyline+spread+
total snapshot per event under `data/market_odds_snapshots.jsonl` --
verified this file empirically contains 709 real snapshots, 100% MLB team
names, confirming it has never been written to by any other sport despite
`_settle_all_unsettled` nominally being the shared settlement path for
MLB/NBA/WNBA/NFL/SOCCER alike. Esports and tennis had dedicated settlement
functions that explicitly passed `None, None` for closing price -- no
attempt at all, not just an empty lookup.

The fix didn't require building new snapshot-capture infrastructure:
`data/odds/{sport}/{date}/polymarket_snapshots.jsonl` already exists and is
already populated daily for esports (22,596 real lines), soccer (6,004),
tennis (11,296), and WNBA (9,190) via `capture_slate_snapshots` (part of
every `daily` run, for every `POLYMARKET_SPORT_LEAGUES` entry including
NBA/NFL -- those two simply have zero real lines right now because July is
their offseason and there are no real markets to capture, not a wiring
gap). `PolymarketSnapshotStore.closing_snapshot(slug, event_start_utc)` --
the exact "last snapshot at or before game start" lookup CLV needs -- also
already existed, used only by a manual MLB CLV-backfill CLI command,
never wired into automatic settlement for anything.

Added a shared `_closing_probability_for_moneyline_pick` helper in
`cli.py` (team/player-name matching via `learned_forward.py`'s existing
`_team_matches` -- reused rather than reimplemented, to avoid a second,
possibly-divergent copy of that matching logic) and wired it into
`_settle_esports_pick`, `_settle_tennis_pick`, and (as a fallback when
MLB's own snapshot store comes up empty, which it always does for soccer)
the generic ESPN settlement path's soccer branch. Explicitly scoped
**out**: soccer totals/BTTS (different side-matching shape -- over/under,
yes/no -- than the team-matching helper built here), soccer draw
selections (the home/away helper has no "neither side" case), and KBO/NPB
(literally zero Polymarket markets ever observed for these leagues, so
there is no closing price to ever capture, confirmed, not merely unwired).

4 new tests (esports CLV populated from a captured snapshot, esports CLV
correctly absent when no `data_root` is passed -- proving existing callers
are unaffected, tennis CLV populated by player name across a snapshot
captured under `data/odds/tennis/`). Live-verified via a real
`settle --all-unsettled` run against production data: zero failures across
every sport/step.

## 2026-07-31 (even later) — full-project logic review: 3 more real bugs found and fixed

Operator asked for "logic problems... indepth review of all models" across
the whole project, not just MLB. Split the work across three parallel
read-only review passes (MLB models, soccer/tennis models, esports/KBO-NPB
models) and personally re-derived/verified every finding directly against
the code before acting on any of it -- two flagged issues turned out to be
intentional-but-underdocumented (see below), three were real and got fixed
with regression tests, and several more were investigated and found to
already be correct.

### Fixed

**1. Soccer: a same-city/shared-mascot derby collision could silently
price the wrong team's contract** (`soccer_forward.py`). `_team_matches_title`'s
fuzzy word-matching treated "city" and "united" as generic, strippable
words (grouped with actual corporate suffixes like "FC"/"SC"/"CF"/"Club"),
so both "Manchester United" and "Manchester City" reduced to the identical
distinctive-word set `{"manchester"}` -- a false match. Verified directly:
`_team_matches_title("Manchester United", "Manchester City")` returned
`True` before the fix. The event-level match (which requires both team
names present in one combined title) mostly protects against this, but the
*single-snapshot-present* case -- one side's own team_win market missing a
fresh executable ask, a normal daily occurrence -- has no such protection,
since there's nothing to collide against.

Fixed two ways: (a) tightened `_GENERIC_TEAM_WORDS` to true corporate
suffixes only (removed "city"/"united"); (b) since short prefixes like
"AC" (as in "AC Milan" vs. "Inter Milan") still can't survive the
word-length filter no matter what's in the generic-word set, added a
cross-check at the actual matching call site: a snapshot is only accepted
for the model-favored team if the *opponent's* name does NOT also match
that same snapshot -- refuse rather than guess whenever it's ambiguous.
Proved the fix mattered the hard way: reverted it locally, watched the new
regression test fail with exactly the predicted wrong-team price, then
restored the fix and re-confirmed the test passes.

**2. KBO/NPB: home/away team labels resolved by raw array position instead
of Polymarket's own side tag** (`international_baseball.py` /
`cli.py`). `forecast_international_baseball_slate` already resolves
`home_id`/`away_id` correctly (tag-safe, via each side's own `"selection"`
field) for the actual win-probability math -- but that safe resolution was
then discarded, and the downstream ledger-logging code in `cli.py`
independently guessed `home_team = teams[1]`, `away_team = teams[0]` from
raw side order, which `market["sides"]` has no ordering guarantee for. A
real (if not yet observed) risk: if the gateway ever lists a KBO/NPB
event's sides home-first instead of away-first, the ledger row's home/away
*labels* silently swap -- with no effect on which team gets picked (that
math was always tag-safe) but a real effect on settlement, which matches
`row["home_team"]`/`row["away_team"]` against the official schedule.
Fixed by propagating the already-correct `home_id`/`away_id` resolution
through to explicit `home_team`/`away_team` name fields on the contract
dict, and having `cli.py` use those directly instead of re-guessing from
position (old positional logic kept only as a fallback for pre-existing
contract shapes). New test constructs the same event with sides in both
orders and asserts identical resolved home/away names either way.

**3. MLB: calibration governance only validated the coinflip point, not
the actual output range** (`models/mlb.py`). `MeasuredEdgeMarginModel`'s
`__init__` validates `scale`/`offset` only at `raw_probability=0.5`; its
`calibrate_selected_side` is then called on the *selected* side's
probability, which for a confident pick can be far from 0.5 -- nothing
stopped a scale/offset pair that passes the midpoint check from producing
an out-of-[0,1] "probability" at the extremes (verified algebraically:
scale=1.5/offset=-0.1 passes the midpoint check exactly at its boundary
but yields 1.25 at raw_probability=0.9). Not firing today -- today's real
artifacts stay safely in range across the practical domain -- but a future
recalibration could trip it silently. Worse, `MeasuredEdgeTotalsModel` had
*no* governance check at all, not even the midpoint one; the margin
model's safety net simply didn't exist for its sibling. Added the missing
totals governance check (mirroring margin's) and an explicit output-bounds
guard in both models' `calibrate_selected_side` methods. Verified both
real production artifacts still load and calibrate correctly with the new
checks in place before considering this done.

### Investigated and found to be intentional, not bugs

- `features/trends.py`'s `defensive_momentum` computes `defense[long] -
  defense[short]` -- reversed operand order versus `offensive_momentum`
  and versus the class's own blanket docstring ("Momentum = short minus
  long"). Traced through: `defense` values are runs *allowed* (a "lower is
  better" quantity), so the reversed order is exactly what's needed to
  keep "positive momentum = team improving" true for both fields
  consistently. The code is right; only the shared docstring is imprecise
  about it. Left alone -- a comment-only fix here risked introducing
  confusion of its own without a clear win, and nothing downstream reads
  the docstring instead of the value.
- MLB Dixon-Coles-adjacent checks (Measured Edge simulation, moneyline/
  spread selection, `learned_forward.py`'s feature computation), soccer's
  actual Dixon-Coles matrix (home/away orientation, the rho low-score
  adjustment, BTTS Platt calibration bounds), tennis's surface-blend
  weighting, and the shared/esports/KBO-NPB Elo update formulas (K-factor
  application, expected-score sign, tie handling) were all traced through
  directly and found correct.

### Flagged but deliberately not silently changed at the time (fixed in the very next session-work -- see the newer section above this one)

- Esports K-factor/confidence-threshold selection tuned by a raw P&L proxy
  with K pinned to a grid-edge value for 4 of 5 titles, and three
  duplicate from-scratch Elo implementations plus an unused shared one --
  both called out explicitly as "a legitimate concern, not something to
  silently change" at review time, then fixed for real once the operator
  confirmed. See "esports v4 -> v5 rebuild" above for the actual fix.

## 2026-07-31 (later) — archived retired-model settled picks; new ledger capability

## 2026-07-31 (later) — archived retired-model settled picks; new ledger capability

Operator directive: archive picks logged under fully-retired model versions
so the live ledgers reflect only current models. `remove_open_rows`
deliberately refuses settled rows (permanent record safeguard), so this
required a genuinely new capability, not a workaround: `ledger.py`'s
`archive_settled_rows(pick_ids, reason, archive_reference)` -- same audited
rigor as `remove_open_rows` (lock-held, audit-first, required non-empty
reason), but the opposite filter (settled only), and the audit event
records the row's COMPLETE content so nothing is actually lost even after
live removal. Added tests (mirrors `remove_open_rows`'s own test, plus
idempotency and required-fields checks).

Applied for real: 29 rows (Main, `mlb-elo-trend-lr-v5`/`v6`) and 89 rows
(Flat, same versions) archived to
`data/archive/2026-07-31-retired-mlb-model-picks/{main,flat}_retired_mlb_picks_archive.xlsx`
and removed from the live ledgers. Verified: 118 real `settled_pick_archived`
audit events written, `verify-chain` still clean (0 breaks), full test suite
green. Research/Gated Research had nothing to archive -- checked first:
every esports/soccer/tennis title has only ever used one `model_version`
string, even though calibration was refreshed internally without a version
bump, so there was no "old version" to separate out.

## 2026-07-31 — soccer/esports validation, MLB gate removal, Gated Research tightened

Soccer's Poisson-DC model (`soccer-poisson-dc-v1`) genuinely qualifies against
this project's own bar: 62.5% real locked-holdout hit rate, +90.4u, every
month positive (`validation.py`'s `qualify_soccer_poisson_model`, previously
never run against current data). Real settled Research picks confirm it:
61.5% win rate (8-5, n=13) on real settled games, closely matching the
backtest. No code changes needed -- it was already good, just never checked.

Esports (5 titles) are also individually strong (65-71% locked-test accuracy,
low calibration error) -- but real settled picks were running far below that
(33-55%), and Gated Research was performing *worse* than unfiltered Research
in every single title (e.g. LOL: 46.4% gated vs 54.2% research). Root cause,
confirmed via `confidence_selection_on_validation`: accuracy climbs steadily
with the model's own confidence (LOL 63.9% at zero threshold -> 74.9% at
0.15), and Gated's `research_confidence_gate` was `0.0` for every title --
barely filtering anything, so it wasn't curating for the better-calibrated
range at all. Fixed: raised `research_confidence_gate` per title to match
each artifact's own already-validated `confidence_threshold` (LOL/DOTA2/
VALORANT 0.05, CS2/RAINBOW_SIX 0.03) -- not arbitrary numbers, the same
values each artifact's own grid search already chose. Research (unfiltered)
deliberately untouched.

Operator directive, applied to MLB's learned moneyline path
(`learned_forward.py`, shared by MLB/NBA/WNBA/NFL): the hard confidence
threshold and the min-edge-vs-market gate in `cli.py` both used to silently
skip a candidate from ever reaching the ledger. Both removed -- every
candidate is now a real, sized call (sizing was already driven by the
model's own confidence distance from 50/50, not by either gate, so removing
them doesn't risk over-sizing a bad trade, only stops hiding the row). Both
numbers are still recorded (`reason` field, rationale text) for a human to
review before deciding whether to act. This is a deliberately different
choice from esports' Gated tightening above -- explicit operator direction
was "MLB: show me everything, I decide" vs. "esports: Research shows
everything, Gated should mean something."

**Project**: `/Users/vincentc9002/model prediction`

**Checkout audited**: single `main` branch (git history was consolidated this
session — the old `deepseek-phase5` branch referenced throughout this file's
older sections no longer exists, locally or on `origin`)

## Safety boundary

This file records diagnoses and read-only reproduction commands. It does not
authorize code fixes, artifact regeneration, ledger mutation, order placement,
or real-money execution. Do not use `--log`, `--write-artifacts`, `daily`,
settlement, dashboard POST routes, or execution commands during an audit unless
the operator separately authorizes that state change.

The source tree was changing during this audit. Re-run the checks before acting
on any line number or count.

## 2026-07-30 — MLB moneyline v7: retired v6's contaminated experiment, plus a real live-data bug found along the way

Rebuilt MLB moneyline (`mlb-elo-trend-lr-v7`) via the project's own standard
walk-forward pipeline (`validation.py`), replacing v6 (self-documented
contaminated `probable_starter_era_gap`, 90-day/242-call ad-hoc fit, never
cleared this project's own bar). v7 uses only point-in-time-safe features
(elo, trend, park, weather, real rolling pitcher-runs-allowed, real
credibility-shrunk bullpen weakness) fit on the full real history (3814
train / 1082 validation / 1391 locked-holdout games). Honest result: 58.5%
locked-holdout hit rate, +13.7u — real edge, still below the 60% bar, no
longer resting on a known-broken coefficient. Config's `qualification_override`
updated accordingly; `probable_starter_era_gap` dropped entirely (its honest
replacement, `point_in_time_pitcher_era_gap`, exists in code but its real
archive is only 6 days deep so far).

Two real bugs found and fixed while building this:
- `bullpen_weakness_gap` had no live feature provider at all in
  `learned_forward.py` — every real game today failed to forecast with a
  bullpen-including variant. Added one, reusing the same real
  `team_recent_relief_lines`/`bullpen_profile` functions Measured Edge
  already serves live with.
- `ingest.py`'s raw score cache is documented "immutable," but a cache
  captured mid-day (games still `STATUS_SCHEDULED`) for a date that has
  since passed was being trusted forever — confirmed live: 2026-07-26's
  cache was written at 14:29, before that evening's games, and every ingest
  since silently skipped re-fetching it. `data/processed/mlb/games.jsonl`
  (which every live Elo/trend feature reads) was missing 57 real completed
  games for 4 days. Fixed: a past-dated cache with zero completed events is
  now treated as stale and re-fetched; a genuinely final payload is still
  never touched. Same audit found 12 more stale WNBA dates and 44 stale
  soccer games — recovered.

  Tennis initially showed ~1748 flagged dates using this same check, but
  that turned out to be a false positive in the fix itself, not a real gap:
  tennis events nest matches under `groupings` (one per draw), a completely
  different shape from the flat `competitions` list `ESPNClient.completed_games`
  assumes, so that parser always sees zero completed matches for tennis
  regardless of true state -- confirmed live (statuses were real
  `STATUS_FINAL`/`STATUS_WALKOVER`, `completed_games` still returned 0).
  Fixed by using the same sport-aware parser real ingest already branches on
  (`completed_tennis_singles_matches` for tennis). Re-checked against all
  1878 real cached tennis files with the corrected parser: 0 genuinely
  stale. Added a regression test pinning that a final tennis cache is never
  refetched.

Retrospective check against real, already-settled picks: on the 20 main-ledger
and 72 flat-ledger v6/v5 games that could be matched to real history, v7 would
have selected the winning side 80% and 54.2% of the time respectively, vs.
v6/v5's actual 55% and 47.2% -- small samples, but a consistent improvement in
both. Full test suite: **484 passed** (7 new). Ruff: **117 findings**, same
baseline.

## 2026-07-30 — MLB Measured Edge rebuild: real elasticities replace the assumed-1.0 multiplicative formula

The 2026-07-29 investigation fixed four real data bugs (weather, starter ERA
shrinkage, bullpen shrinkage, park factors) but the resulting model still
showed weak-to-nonexistent real correlation on a 162-game/12-day backtest
(margin 0.045-0.12, totals ~0 or negative) -- concluded at the time as a
likely structural limit of the formula itself: `estimate_runs()` combines
offense/starter-weakness/bullpen/park/weather multiplicatively with an
*implicit, never-fit* exponent of 1.0 on every one of them (`league_avg *
factor1 * factor2 * ...`), assuming each factor should move the run
estimate exactly proportionally. Operator asked to rebuild rather than
accept that ceiling.

**Real refit**: collected point-in-time features (`reconstructed_features`)
and real final scores for 629 real games spanning 2024-02 to 2026-07 (vs.
the previous 162-game/12-day window -- far too narrow to safely fit five
new parameters), sampling every 8th real game-date for season/park/weather
coverage. Fit a Poisson GLM (log link) on `log(runs) ~ elasticity *
log(factor)` per factor, pooling both team-game sides of every game.
Validated via 4 chronological expanding-window folds before trusting any
single number (real risk here: point estimates are noisy at this sample
size and can flip sign fold-to-fold at these correlation magnitudes).

Real fitted elasticities (alpha=0.05 L2, chosen from the middle of the
tested range 0/0.02/0.05/0.1 since fold performance was similar across all
of them): `offense_elasticity=0.035`, `starter_weakness_elasticity=0.211`,
`park_elasticity=0.222`, `weather_elasticity=0.021`. Every one of these is
real but far below the previously-assumed 1.0 -- the formula had been
letting every factor swing the run estimate much harder than the real data
supports. `bullpen_elasticity` fit consistently **negative** across every
alpha and fold (worse opposing bullpen correlating with *fewer* runs
allowed) -- baseball-implausible given the feature's own definition, most
likely selection bias in which relief inning gets recorded (a leading
team's closer vs. a trailing team's mop-up arm), not real signal. Confirmed
dropping the feature entirely gives statistically indistinguishable holdout
performance from including it at its negative fit, so clipped to `0.0`
rather than trusted. Full reasoning lives in
`mlb-analyst-poisson-trend-v0.2.yaml`'s comments.

**Wired in**: `FormulaSpec` gained the five elasticity fields (required in
`load_formula_spec`, no silent defaulting); `estimate_runs()` now applies
`factor ** elasticity` instead of `factor` directly -- elasticity=1.0 is a
strict no-op vs. the pre-rebuild formula, so this is a generalization, not
a rewrite. New tests (`tests/test_mlb_elasticities.py`) verify the
exponentiation mechanism itself (zero elasticity nullifies a factor,
elasticity=1.0 reproduces exact pre-rebuild multiplication, fractional
elasticity dampens the swing) independent of which real values were fit.

**Recalibrated against the same real 162-game/2026-07-01..07-12 backtest**
used on 2026-07-29 (OLS of raw simulated probability vs. real outcome, same
methodology, both margin and totals artifacts rewritten with real new
hashes):

| Market | 2026-07-29 correlation | 2026-07-30 rebuilt correlation | Flat -110 diagnostic |
|---|---|---|---|
| Margin (spread cover) | 0.045-0.12 | **0.062** (within the old range -- rebuild didn't move margin much) | 80 picks, 53.7% hit, +2.09u |
| Totals (over/under) | ~0 or negative | **+0.166** (real, meaningful improvement -- first time this project has seen positive real signal on MLB totals) | 91 picks, 56.0% hit, +6.36u |

Honest caveat, stated the same way as every prior number in this
investigation: still only 162 real games, one 12-day window. Totals'
improvement is the most encouraging result of this whole MLB investigation
so far, but one backtest window is promising, not proven -- `model_state`/
units stay at research/zero (`qualification.insufficient_real_signal` was
removed from the totals artifact since it's no longer accurate, but no
qualification/promotion claim was added either). Live-verified: `build_mlb_slate`
against the real 2026-07-30 slate produces 27 real priced candidates across
9 real games with no new errors.

Full test suite: **481 passed** (3 new). Ruff: **117 findings**, same
baseline. `verify-chain`: 0 breaks, `chain_intact: true`.

## 2026-07-30 — esports/soccer side-selection: investigated, "fixed," then reverted per operator design decision

Found that `_log_esports_forecast` (`cli.py`) and `build_soccer_total_slate`
(`soccer_forward.py`) both select which side of a two-sided contract to log
by **raw model probability** ("whichever side the model thinks is more
likely to win/happen"). Confirmed live (not hypothetical): 23 of 64 real
esports contracts today (36%) had the model's raw favorite priced at
negative edge while the other side was positive (e.g. LOL "Joblife" 56.5%
model prob but ask 0.64, edge -7.5pp, vs. "Skillcamp Esport" 43.5% prob but
ask 0.37, edge +6.5pp); soccer's live slate showed the same pattern
(Newell's Old Boys @ Independiente: model favors Independiente 45.6% to
Newell's 27.8% outright, but Independiente's own team_win market was
-13.4pp edge while Newell's was +11.8pp).

Initially "fixed" this to select by edge instead of raw probability
(`max(sides, key=model_probability - ask)`), reasoning the ledger exists to
find +EV mispricings. **Operator corrected this**: every sport's model is
supposed to call the winner and log that pick -- `learned_forward.py`'s MLB
moneyline already works this way (`selection = "home" if home_probability >=
0.5 else "away"`, confirmed by reading the code), unconditionally, with no
edge comparison between sides. Esports/soccer picking by edge instead was
inconsistent with that established, system-wide policy, not an independent
improvement. Reverted both call sites and their tests back to raw-probability
selection to match MLB's pattern; the min_edge/eligibility gate downstream
(already probability-direction-agnostic: `abs(prob - 0.5)` for confidence,
`prob - implied_probability(odds)` for edge) is what decides whether a
correctly-called winner is *also* good value enough to become a real pick
vs. a zero-unit research observation -- that part was never broken and did
not need to change. Net code diff after the revert: comments only,
documenting the now-confirmed system-wide policy explicitly at both call
sites so this doesn't get "fixed" the same way again.

Also investigated and consciously left alone: `esports.py`'s
`SPORT_K_OVERRIDE` hardcodes K=96 (the top of the grid) for lol/cs2/dota2/
valorant. Re-running the grid search on current (larger) data shows the true
validation-set optimum is now lower for three of those four (lol: 40, cs2:
32, dota2: 48) — but checking held-out locked-test performance with the
auto-selected K vs. the override K gave mixed results (override K=96 did
*better* on LOL's locked test despite scoring worse on validation). With only
one validation/test split this is genuinely ambiguous small-sample noise in
which K wins, not a clear-cut bug like the pricing-side issue above — did not
touch it. Also bumped `mlb-analyst-poisson-trend-v0.2.yaml`'s Monte Carlo
`simulations` from 10000 to 20000 (numpy-vectorized, negligible cost either
way; reduces sampling noise only, does not address the model's real
correlation ceiling documented in the 2026-07-29 section below).

Full test suite: **480 passed**. Ruff: **117 findings**, same as prior
baseline (no new findings). `verify-chain`: 0 breaks, `chain_intact: true`.

## 2026-07-29 — MLB backfill/settlement fixes, dashboard fixes, Measured Edge
## model investigation, baseline auto-refresh

All items below were operator-directed, verified with real data (real ESPN/
Polymarket/Open-Meteo/MLB Stats API calls, real historical games, real
backtests), and committed with tests passing. Full test suite: **478
passed**. `verify-chain`: 0 breaks, `chain_intact: true`, `reconciled: false`
(the reconciliation gap is expected/documented, grows monotonically — see
2026-07-26 section below, not new).

### Ledger/settlement fixes

- **`forecast --force` backfill bug (found and fixed)**: `--force` froze the
  point-in-time decision timestamp at literal midnight UTC of the target
  date, but real Polymarket quote captures for a date don't start until
  hours later (~04:02 UTC observed) — every captured quote was "in the
  future" relative to that frozen instant, so `request.validate()` correctly
  rejected all of them and a real backfill attempt silently returned
  `logged: 0` with zero duplicates flagged. Fixed: each candidate now gets
  its own effective decision time (its own game's start minus one second)
  instead of one shared midnight freeze — `cli.py::_forecast_learned_sport`,
  `effective_now`. Recovered 6 main-ledger + 11 flat-ledger real 7/27 MLB
  moneyline picks this way, using real archived point-in-time odds.
- **`daily`'s automatic settlement never auto-voided postponed/canceled
  games** (`void_postponed=False` hardcoded in the `daily` command handler)
  — a postponed game never becomes "completed" under its original event_id
  (ESPN issues a new event_id for any reschedule), so an affected pick sat
  `open` forever with no automatic resolution path; only a manually-run
  `settle --void-postponed` would clear it. Fixed: `daily`'s automatic
  settlement now passes `void_postponed=True`.
- **`PickLedger.settle()` gained a `correction_reason` parameter** to allow
  re-grading an already-settled/voided row when explicitly reasoned (audit
  event type `pick_resettled_corrected`, distinct from a first-time
  `pick_settled`). Real case: the 7/27 7:00 PM Reds/Guardians game was
  postponed and never played under its own event_id (correctly voided as a
  push), but was actually replayed same-day as game 2 of a doubleheader
  (real final Reds 2, Guardians 0) — re-graded all 4 affected rows (main
  moneyline, flat moneyline/spread/total) to `win` per explicit operator
  directive.

### Dashboard fixes (real bugs found from an actual screenshot, not
### hypothetical)

- **Today tab duplicated the entire Flat Ledger / Flat Research sections.**
  `renderToday()` calls `loadFlatToday()`/`loadResearchToday()` without
  awaiting them, and neither cleared its own previously-appended DOM before
  re-appending; an overlapping call (auto-refresh racing a slow fetch, or a
  second manual refresh) let a stale in-flight call's results land after a
  newer render had already rebuilt the page. Fixed with a render-token guard
  (`todayRenderToken`) — a stale async loader now bails out instead of
  touching the DOM.
- **A pick with no matched executable quote still showed a computed
  market/edge/+EV badge/suggested size** — all derived from the `-110`
  neutral placeholder `cli.py` uses when no real quote is found
  (`sportsbook: "model_opinion_no_executable_quote"`), making a
  model-opinion-only row look like a real actionable edge. The backend
  already blocked the Buy button correctly; only the display was wrong.
  Fixed: rows with that sportsbook value now show `market: no quote`,
  `edge: —`, and a "model opinion only" badge instead.

### MLB Measured Edge (spread/total) model investigation

Four independent, individually-confirmed real bugs in the Trend Engine
feature formula (`models/mlb.py`), found via a real 162-game walk-forward
backtest (real historical Polymarket-reconstructed spread/total lines from
`data/historical/mlb_market_lines_reconstructed.jsonl`, real final scores,
point-in-time-correct reconstructed features):

1. **Weather was silently always neutral.** ESPN's own weather fields
   (`competition.situation.weather`, summary `gameInfo.weather`) are
   empirically always empty for MLB, live or completed — confirmed by
   direct inspection across multiple real games, not just reading docs.
   Fixed: `features/weather.py::resolve_weather` (live Open-Meteo forecast
   for upcoming games, historical archive for backtest/past dates) wired
   into `ESPNMLBClient.reconstructed_features`.
2. **Starter ERA had zero shrinkage for small-innings samples.** A pitcher
   with e.g. 3.3 total innings this season (early callup, return from
   injury) could post a 21.6 ERA off one bad outing and have it trusted at
   full confidence — drove implausible pregame run-total projections
   (observed range 4.32–18.55 runs before the fix). Fixed: credibility-
   weighted shrinkage toward `league_starter_era` for both `season_era` and
   `recent_era` (mirrors the shrinkage `_offense_index` already applies to
   team offense), plus the same treatment for the K%/BB% "discipline"
   multiplier. New spec fields: `starter_season_prior_innings` (40 IP),
   `starter_recent_prior_innings` (20 IP), `starter_rate_prior_batters_faced`
   (60 BF).
3. **Bullpen strength was hardcoded neutral for literally every game** —
   `bullpen_profile(None)`, no data source wired at all. Fixed: built a real
   relief-appearance index from `mlb_statsapi.py`'s boxscore snapshots
   (`features/bullpen.py::team_recent_relief_lines` — last 10 completed
   games' relief lines per team, excluding that game's own starter), with
   the same credibility-weighted shrinkage applied in `bullpen_profile()`
   itself.
4. **Park factors were a static, undated table.** Recomputed empirically
   from this project's own 7,803+ real completed games — confirmed several
   parks were meaningfully stale, most strikingly the Athletics (0.98 static
   vs. 1.153 real, since the team relocated to a different park).

**Honest finding, not swept under the rug**: none of the four fixes
recovered meaningful real-world predictive signal. Margin correlation with
real outcomes stayed weakly positive across every formula revision tested
(0.045–0.12); totals stayed at or below zero throughout (-0.019 to -0.123).
That's a structural finding about the hand-tuned multiplicative-index
formula (every factor's own noise compounds rather than averaging out), not
something further feature-level tuning fixes. Flagged rather than chased
further after four independent rounds all showed the same pattern.

**Calibration refit from the real 162-game backtest**, replacing the prior
identical-for-both-markets `0.85`/`0.075` placeholder:
- `measured-edge-margin-v1.json`: `scale=0.1007`, `offset=0.4667` — real,
  honest, heavy shrinkage; margin showed a real if small positive signal.
- `measured-edge-totals-v1.json`: reuses margin's shrinkage as the most
  conservative available estimate (totals' own fit was noise-level/
  negative), with an explicit `qualification.insufficient_real_signal` flag
  and the full backtest evidence documented in the artifact itself.
- `MeasuredEdgeMarginModel`'s governance bounds check was rewritten: the old
  fixed `-0.25 <= offset <= 0.25` bound implicitly assumed scale stays close
  to 1 (mild shrinkage) and would have rejected this real, correctly-
  centered, heavy-shrinkage fit. Now checks the actual invariant that
  matters — `calibrated(0.5)` stays near 0.5 — regardless of how much
  shrinkage that requires.

Existing picks under the old calibration (52 rows, all already settled)
were left as real historical record rather than destroyed — distinguishable
from new picks by `model_artifact_hash` going forward. Still Flat-only, no
main-ledger promotion.

### MLB season-dependent baseline auto-refresh (new)

Park factors, league-average starter ERA/K%/BB%, and league relief ERA all
drift as a real season progresses (and can jump discontinuously — a team
relocating ballparks, like the Athletics did). These were one-time
snapshots computed manually during the investigation above; a real
regeneration workflow now exists so they don't go stale again silently.

- New module: `mlb_baseline_refresh.py` — `compute_park_factors` (from
  `data/historical/mlb_games_all.jsonl`), `compute_league_rates` (from
  `data/mlb_statsapi/game_snapshots.jsonl`), and writer functions that patch
  `features/park_factors.py` (full regeneration, it's a generated file now)
  and the four `league_*` fields in `mlb-analyst-poisson-trend-v0.2.yaml` /
  `LEAGUE_RELIEF_ERA` in `features/bullpen.py` (targeted regex substitution,
  preserving every hand-written comment in both files).
- Self-throttled via `data/mlb_baseline_refresh_state.json` — default
  minimum 7 days between refreshes (park factors and league rates are
  full-season aggregates; day-to-day noise dominates anything a faster
  cadence would catch, and there's no cost to checking more often since it
  no-ops when recent). `--force` bypasses the throttle.
- Wired into `daily` (`step0_mlb_baseline_refresh` in its output) so it runs
  automatically on the existing cron cadence without needing separate
  infrastructure, and exposed as its own CLI command:
  `model-prediction refresh-mlb-baselines [--force] [--min-days N]`.
- First real run (2026-07-29): 30 real park factors from 7,803 games
  (2024-02-22 to 2026-07-25); league rates from 6,349 real boxscore
  snapshots (2024-03-20 to 2026-09-22) — starter ERA 4.1958, strikeout rate
  0.2193, walk rate 0.0785, relief ERA 4.0593, runs/team/game 4.5493. All
  close to but distinct from the prior hand-typed reasoned defaults.

## 2026-07-27 (evening) — wiring session

Per operator directive, this session's scope was **wiring and features, not
validation**: is a model actually running in the live daily pipeline, and on
what data — not hit rates, Brier scores, or promotion-gate status. Two
background audit agents independently verified the results below against
current source; findings are their direct file:line citations, not
self-reported claims.

**Git**: `deepseek-phase5` and all other branches were merged and deleted; the
repo is now a single `main` branch, synced with `origin/main`.

**Newly wired this session** (previously either broken, unwired, or
nonexistent):
- MLB totals + spread now reach `data/flat_picks.xlsx` via
  `_forecast_mlb_totals_flat` (`cli.py`), pricing `MeasuredEdgeTotalsModel`/
  margin (`models/mlb.py`) against real Polymarket lines. The frozen
  `config/models/mlb-analyst-poisson-trend-v0.2.yaml` spec was missing/
  mismatched `factor_bounds`, `uncertainty`, and `simulation` keys entirely;
  all three blocks were rebuilt with documented reasoned-default values (not
  precision-fit) since the file could not otherwise load.
- Soccer moneyline now prices against Polymarket's real market shape — three
  separate per-team `team_win` Yes/No markets, not one combined moneyline
  market as first assumed. `MARKET_TYPES` in `data_sources/polymarket_us.py`
  didn't recognize `SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME` at all before this,
  so these markets were silently dropped.
- Esports ratings now auto-refresh before every forecast
  (`esports.py::refresh_recent_matches`, called by `cli.py::
  _refresh_esports_ratings` inside `daily`) instead of only updating via a
  full-file-overwrite manual backfill. Verified today (2026-07-27) that
  `manifest.json`'s `matches_sha256` for all four titles matches the actual
  current file hash — the incremental-merge path keeps the manifest/hash
  chain consistent, which a naive implementation would have broken.
- KBO/NPB: a silent-gap bug in `international_baseball.py` — a market with
  the wrong number of sides was silently `continue`d past with no recorded
  reason — now appends `{"reason": "NO_CALL_MARKET_SIDES_INVALID"}` before
  skipping (`international_baseball.py:763-770`, confirmed present by
  audit). Every other `continue` in that function was independently checked
  and does record a reason; one bare `continue` for non-moneyline market
  types is intentional, not a gap.
- Tennis is entirely new this session — see the dedicated section below.
- `League.WORLD_CUP` removed from `config/model.yaml` (`team_ban_list.teams`
  and `models:`) and from `POLYMARKET_SPORT_LEAGUES`/`_LEDGER_LEAGUE_TO_ESPN`
  (live trading and settlement no longer see it) — but see the gap noted
  below; the removal is incomplete elsewhere.
- Dashboard: research-mode CALL/NO_CALL pill removed in favor of plain
  win/loss/push/open across all tabs; "Research" tab renamed "Flat Research"
  (it was always the ungated tier; gated research is the filtered one, so the
  old name was ambiguous); `#researchTable`/`#gatedResearchTable`/
  `#flatTable`/`#btTable` wrapped in `overflow-x:auto` (previously missing,
  causing wide tables to push past the page edge).

### Tennis (new, 2026-07-27)

**Wired: yes**, independently re-verified by a background audit agent against
current source (not just self-reported):

- Invoked from `daily` (`cli.py:2728`, immediately after soccer's call) and
  from the `forecast`/`flat-forecast` sport-dispatch loop (`cli.py:2469`,
  `elif sport == "tennis":`).
- `RESEARCH_LEDGER_SPORTS` (`research_ledgers.py:7-16`) includes `"tennis"`,
  so today-clearing and dashboard discovery pick it up automatically.
- `BBO_CAPTURE_SPORTS` (`data_sources/polymarket_us.py:99`) includes
  `"tennis"`, so real odds get captured daily.
- Settlement: `_find_tennis_result`/`_settle_tennis_pick` (`cli.py:1993-2053`)
  branch on `league == "TENNIS"` inside `_settle_all_unsettled`
  (`cli.py:1768-1776`), matching completed WTA singles matches by player name
  since ESPN's tennis scoreboard shape (`groupings`/`athlete`) doesn't fit the
  generic team-based settlement path at all.
- Ingestion: `data_sources/espn.py::completed_tennis_singles_matches` parses
  ESPN's tennis scoreboard (matches nested under `groupings`, competitors
  carrying `athlete` for singles or `roster` for doubles) and filters to
  singles only via `competition.type.slug` containing `"singles"` — doubles
  are dropped entirely (matches the operator's confirmation that Polymarket
  US never lists doubles markets). Real historical bootstrap run 2026-07-27
  produced 25,787 singles matches (ATP 16,721 / WTA 9,066) into
  `data/processed/tennis/games.jsonl` and `data/historical/
  tennis_games_all.jsonl`, both previously empty despite 1,866 cached raw
  ESPN days sitting unused.
- Live-verified end to end with real data, no mocks, 2026-07-27: a real
  `polymarket-slate --sport tennis` capture stored 420 real BBOs; a real
  `forecast --sport tennis --log` run found 143 scheduled WTA matches, priced
  16 against real Polymarket asks by player name, and logged all 16 to the
  new `data/research/tennis.xlsx` (confirmed 16 rows via `PickLedger`).
- **Structural coverage limit, not a bug**: `POLYMARKET_SPORT_LEAGUES["tennis"]`
  (`polymarket_us.py:73`) is `("WTA", "ITF_MEN", "ITF_WOMEN")` — there is no
  ATP market on Polymarket US at all (comment at line 60 confirms this was
  independently verified live). `LEAGUE_PATHS`/`SPORT_LEAGUES`
  (`espn.py:57-58,92`) only expose `ATP`/`WTA` — no ITF scoreboard path
  exists. WTA is therefore the only tour where both a model prediction and a
  real executable price can ever coexist; `tennis_forward.py` deliberately
  only builds a WTA slate.
- **Gap found by audit, not yet fixed**: `tennis_forward.py`'s
  `_upcoming_singles_matches` (lines 48-78) never extracts the tournament
  name or calls `_infer_tennis_surface`, and `build_tennis_slate` constructs
  `UpcomingMatch(...)` without a `surface=` kwarg (lines 139-147) — live
  predictions therefore always fall back to the dataclass default
  `surface="Hard"` (`models/tennis.py:49`), even though the *historical*
  Elo-build path correctly infers surface per match
  (`espn.py:196,225`). Surface-blending is effectively inert at forecast
  time today. Tracked as an open follow-up.

### Newly found gaps (not yet fixed)

- **Soccer BTTS**: `models/soccer.py:119-121,165-173` already computes a full
  BTTS probability and emits a `GamePrediction(market_type="btts", ...)` —
  the gap is entirely on the classification side.
  `data_sources/polymarket_us.py`'s `MARKET_TYPES` (lines 99-115) has no BTTS
  entry, so no BTTS market is ever recognized even if one exists on the
  gateway. Confirmed still unbuilt, not broken.
- **Esports BBO-capture/forecast mismatch**: `POLYMARKET_SPORT_LEAGUES
  ["esports"]` (`polymarket_us.py:74-82`) lists 8 leagues (LOL, CS2, COD,
  VALORANT, DOTA2, ROCKET_LEAGUE, OVERWATCH, RAINBOW_SIX); because `"esports"`
  is one bucket in `BBO_CAPTURE_SPORTS`, real BBO gets captured for all 8
  whenever Polymarket lists them. Confirmed live: `data/odds/esports/
  2026-07-18/polymarket_snapshots.jsonl` (and 07-19, 07-22) contain real COD
  and RAINBOW_SIX snapshots with live asks/bids. `ESPORTS_TITLES`
  (`cli.py:114`) and `TITLE_SPECS` (`esports.py`) only ever price 4 of the 8
  — COD/ROCKET_LEAGUE/OVERWATCH/RAINBOW_SIX get real market data captured
  and stored with zero consuming model, silently, every day. Not dangerous
  (no money moves), but dead capture work with no test catching the
  mismatch.
- **World Cup removal is incomplete**: dropped from the live-trading league
  tuple (`polymarket_us.py`) and from `_LEDGER_LEAGUE_TO_ESPN`
  (`cli.py:130-141`) — confirmed zero WORLD_CUP rows in `data/picks.xlsx` (20
  rows) or `data/flat_picks.xlsx` (81 rows) as of 2026-07-27, so nothing is
  actively broken. But `League.WORLD_CUP` still exists as an enum member
  (`domain.py:25`), still has a `ModelSpec` (`models/registry.py:70-76`), and
  is still listed in `espn.py`'s `LEAGUE_PATHS`/`SPORT_LEAGUES`
  (lines 44,78) and in `the_odds_api.py`/`football_data.py` sport-key maps.
  If a WORLD_CUP ledger row were ever logged while still open, it would
  settle-stall silently forever (`_LEDGER_LEAGUE_TO_ESPN.get("WORLD_CUP", ())`
  returns nothing, no match, `pending` forever, no error). `tests/
  conftest.py:25` also carries a stale `"WORLD_CUP": []` ban-list fixture key
  that doesn't match the real `config/model.yaml` (which has no `WORLD_CUP`
  key at all). Decision needed: fully retire `League.WORLD_CUP`, or
  explicitly document it as "history/ingest-only, never a live league."
- **Legacy MLB margin model — confirmed intentional, not a gap**:
  `MeasuredEdgeMarginModel` (`models/mlb.py:406-447`) is only reachable via
  the explicit `--model legacy-measured-edge` flag on `forecast`/`log`
  (`cli.py:230,256,2414-2417`); `daily`'s argparse (`cli.py:273-280`) has no
  `--model` flag at all, so this path structurally cannot fire from `daily`.
  `_forecast_mlb`'s own docstring calls it "retained as an explicit
  rollback." Live MLB moneyline in `daily` genuinely goes through
  `learned_forward.py` via `_forecast_learned_sport` for `mlb` in
  `DAILY_LEARNED_SPORTS`. Not documented anywhere in `docs/`, but not
  confusing dead code either — just undocumented.
- **KBO/NPB observation, not confirmed as a bug**: `data/logs/
  daily_2026-07-27.log` shows both leagues returned `"events": 0` across all
  runs today, with no `data/odds/{kbo,npb}/2026-07-27/` capture directories
  (2026-07-26 had 1-5 events for both). Consistent with an off-day/All-Star
  break rather than a wiring bug, but not externally confirmed against either
  league's real schedule.

### Numbers re-verified 2026-07-27 (this session)

| Check | Result |
|---|---|
| Full test suite | **458 passed** |
| Ruff (`src/ tests/`) | **113 findings** |
| `verify-chain` | 0 breaks, 0 hash mismatches, `reconciled: false` (1,230 historical creation events without a matching removal event — grows over time by design, see note below) |

The P0/P1 findings and numbers further down this file (test/ruff/hash/
reconciliation counts under "Verified audit result — 2026-07-26") were **not**
re-run this session — they concern real-money execution safety and ledger/
audit transaction atomicity, which this session's scope did not touch. Treat
them as last-verified-2026-07-26, not current, until independently re-run.

## 2026-07-28 (early hours) — critical esports and tennis correctness fixes

Two bugs found and fixed this pass are more severe than anything in the prior
audit — both were live-verified against real external sources (bo3.gg's API,
the actual dashboard output), not just re-read from code.

### Esports: dota2 and valorant discipline IDs were swapped

Verified live against `GET https://api.bo3.gg/api/v1/disciplines`: the real
mapping is `{1: csgo, 2: valorant, 3: lol, 4: dota2, 5: deadlock, 6: games,
7: r6siege, 8: mlbb}`. `esports.py`'s `TITLE_SPECS` had `dota2:
discipline_id=2` and `valorant: discipline_id=4` — backwards. Confirmed by
cross-referencing real team IDs: the team "Wintermint", stored under the old
`data/esports/dota2/matches.jsonl`, resolves only under `discipline_id=2`
(real Valorant) via `GET /api/v1/teams?filter[teams.discipline_id][eq]=2`;
"Team Sexy", stored under the old `valorant` file, resolves only under
`discipline_id=4` (real Dota 2). The dota2 model had been trained on and
predicting from Valorant match history, and vice versa, for as long as this
project has run esports.

Fixed: swapped `TITLE_SPECS["dota2"]["discipline_id"]` to 4 and
`TITLE_SPECS["valorant"]["discipline_id"]` to 2. Both titles' `matches.jsonl`/
`teams.json`/`manifest.json` were deleted and rebuilt from scratch via
`esports-backfill` (the old data was for the wrong game entirely, not
correctable by a metadata fix) — dota2 now has 10,888 real Dota 2 matches,
valorant 14,575 real Valorant matches. Re-validated: `config/models/
dota2-tiered-elo-v4.json` and `valorant-tiered-elo-v4.json` regenerated.
Live-verified post-fix: `forecast --sport dota2` now resolves real Dota 2
team names ("Nemiga Gaming", "Team Bald", "Level UP") with real computed
probabilities; `forecast --sport valorant` resolves real VCT teams ("Cloud9
GC", "Shopify Rebellion Gold/Black").

### New: Rainbow Six Siege esports model

bo3.gg's `disciplines` endpoint confirmed real coverage for R6 Siege
(`discipline_id: 7, slug: r6siege`) with 2,969 real finished matches
available. Added `TITLE_SPECS["rainbow_six"]`, `League.RAINBOW_SIX`
(`domain.py`), a `ModelSpec` (`models/registry.py`), a `config/model.yaml`
entry (`status: research`, not `qualification_override`-promoted like the
other four — that promotion was a deliberate per-title operator review this
title hasn't had), and wired it into `ESPORTS_TITLES` (`cli.py`), which is
consumed generically by both the `daily` handler and the `forecast`/
`flat-forecast` dispatch loop (no other per-title code needed changing).
Settlement (`_settle_esports_pick`'s league check) and dashboard
(`SPORTS`/`RESEARCH_ONLY_LEAGUES`) updated. Backfilled and validated;
`config/models/rainbow_six-tiered-elo-v4.json` exists with 281 team ratings.

**Confirmed not buildable**: CoD, Rocket League, and Overwatch do **not**
exist as disciplines on bo3.gg at all (verified against the full 8-entry
disciplines list above) — this project has no other esports data source, so
these three cannot get a real model. Polymarket US does list all three as
esports leagues and real BBO gets captured for them daily
(`BBO_CAPTURE_SPORTS` includes the whole `"esports"` bucket) with nothing
ever consuming it — this is real, harmless (no money moves), but genuinely
dead capture work. No fix applied; flagging honestly rather than building a
model with fabricated data.

### Tennis: two additional bugs beyond the surface-inference fix above

Found live from the dashboard itself — every tennis pick showed exactly
50.0% model probability, including for well-known players (Madison Keys,
Sofia Kenin) who should have hundreds of real matches on file. Two distinct
bugs, both now fixed:

**Bug 1 — combined ATP+WTA tournaments mistagged by fetch order, not by
match.** ESPN's `tennis/atp` and `tennis/wta` site-API paths both return the
*entire combined event* (both Men's and Women's Singles groupings) for
shared tournaments — verified live: `scores_atp.json` for Brisbane
International contains a "Women's Singles" grouping, and `scores_wta.json`
for the same date contains its "Men's Singles" grouping too. `ingest.py`
previously tagged every match `league = <the endpoint that fetched it>`,
and since `SPORT_LEAGUES["tennis"] = ("ATP", "WTA")` fetches ATP first, every
WTA player's combined-tournament matches got claimed by the ATP fetch first
and the later WTA fetch's identical `event_id` was deduped away as already
seen. Fixed: `completed_tennis_singles_matches` (`data_sources/espn.py`) now
derives `league` per match from the competition's own `type.slug`
(`"womens-singles"` → WTA, `"mens-singles"` → ATP) instead of accepting a
caller-supplied tag; `ingest.py` no longer overwrites it for tennis.
`tennis_forward.py`'s live discovery was also filtering on generic
`"singles" in slug`, which would have accepted Men's Singles matches
returned redundantly by the WTA endpoint — narrowed to `"womens-singles"`
specifically. Rebuilt from already-cached raw files (no new network calls):
WTA match count went from 9,066 (undercounted) to 15,289; Madison Keys's 129
matches are now all correctly tagged WTA (previously split across both
leagues depending on fetch order).

**Bug 2 (the actual cause of the 50% symptom) — `tennis_forward.py` was
reading history through the wrong abstraction and always got zero rows.**
`build_tennis_slate` called `FeatureStore("data").games_before("tennis",
...)`, but `FeatureStore.load_games` builds a `GameRecord` per row via
direct dict subscript on `raw["away_team"]`, `raw["home_team"]`,
`raw["away_score"]`, `raw["home_score"]` — fields that exist for every
team-vs-team sport but do not exist on tennis's player-vs-player rows
(`winner`/`loser`/`surface`/`match_date`, no scores at all). Every tennis row
therefore raised `KeyError` and was silently caught by `load_games`'s broad
`except (KeyError, TypeError, ValueError): continue`
(`features/base.py`), so `games_before("tennis", ...)` had returned an empty
list for every single call since tennis was wired — completely independent
of bug 1 above, and the reason real players still showed exactly 0.5 even
after bug 1 was fixed. Fixed: added `tennis_forward.py::
_tennis_history_before`, which reads `data/processed/tennis/games.jsonl`
directly as raw dicts (the shape `TennisModel.build_elo` already expects)
and applies the same point-in-time cutoff `games_before` uses, bypassing
`FeatureStore`/`GameRecord` entirely rather than trying to force tennis into
an abstraction built for team sports.

Verified post-fix, directly against real data: Madison Keys's computed Elo is
now 1863 vs. a lower-ranked opponent's 1512, giving a real 84.6% win
probability — not 50%. `_tennis_history_before` returns 25,955 total rows
(15,282 WTA) where it previously returned 0.

**Note on pre-fix ledger rows**: `data/research/tennis.xlsx` contains 16 rows
logged 2026-07-27 before this fix, all computed with the broken
always-cold-start model (every `model_probability` in that batch is exactly
0.5). They were left in place (paper-only, no real exposure) rather than
deleted, but should not be used to judge tennis model performance — treat
anything logged before this fix's commit as void.

### Full-pipeline verification after all of the above

Ran the real `daily --date 2026-07-28` command end to end (not a dry run) to
confirm every sport discussed this session actually logs through the live
pipeline post-fix. All of MLB (main+flat), WNBA (main+flat), soccer, tennis,
KBO, NPB, LOL, CS2, DOTA2, VALORANT, and RAINBOW_SIX appeared in
`step2_3_forecast_and_log`'s output with no exceptions and exit code 0. Real
unit sizing confirmed working end to end by inspecting the actual gated
ledgers afterward: `data/gated_research/cs2.xlsx` (17 rows, 0.75U–2.00U),
`dota2.xlsx` (5 rows, 0.75U–2.00U), `lol.xlsx` (16 rows, 1.00U–2.00U) all show
real, differentiated unit sizes tied to real edge. KBO/NPB/rainbow_six/
soccer/tennis/valorant gated ledgers were empty at this particular run — that
reflects zero candidates clearing that sport's edge/confidence floor *today*,
not a sizing bug (soccer logged 6 candidates to Flat Research the same run,
none gated; KBO logged 5, none gated — both expected, low-edge days).

One transient, non-reproducible discrepancy: a `forecast --sport kbo --log`
run showed `priced_count: 5, logged: 0` at one point. Re-running the
identical logic standalone immediately after succeeded and logged all 5 rows
correctly (now confirmed present in `data/research/kbo.xlsx` with real,
differentiated probabilities). Most likely explanation is lock contention
with the recurring `com.modelprediction.daily.plist` background cron (every
3 hours) rather than a code defect — the exact code path was re-verified
working when run in isolation moments later with no changes. Flagged here in
case it recurs; not treated as a confirmed bug.

## 2026-07-27 remediation note

The historical findings below remain useful audit context, but the approved
forecast scope is now repaired in this working tree:

- MLB historical validation uses only pregame-observed starter archive rows;
  the first real prospective rows were captured 2026-07-26. MLB v6 remains
  unqualified.
- Missing/invalid MLB or WNBA executable quotes retain a zero-unit Today model
  opinion with `NO_CALL_MARKET_UNAVAILABLE`; the 5% valid-quote edge gate is
  unchanged.
- WNBA availability conflicts/errors default affected inputs neutral and are
  surfaced in Today rather than suppressing the model opinion.
- Soccer runs a draw-aware Poisson/Dixon-Coles full-game 2.5-total research
  path. KBO/NPB preview, research/gated routing, daily coverage, settlement
  output, and `$0.50` tie-contract P&L are wired.
- Flat is isolated from soccer/esports/KBO/NPB. The unified runner no longer
  lets a flat phase clear or overwrite research/gated ledgers.
- Research and Gated Research are split into independent per-sport workbooks
  for Soccer, LoL, CS2, Dota 2, Valorant, KBO, and NPB. The dashboard
  aggregates those files without merging their storage.
- A centralized Gated Research eligibility wrapper now requires exact model
  inputs, a timestamp-valid executable quote, and the configured per-sport
  edge/confidence floors. Valid low-edge rows remain zero-unit Research
  `NO_CALL` observations; unresolved or untrained inputs enter neither ledger.
- The legacy mixed ledgers were archived intact under
  `data/archive/research-ledger-split-20260726T192729Z/`. The cleaned live
  ledgers contain 32 Research rows and 22 Gated rows with zero invariant
  violations.
- The full suite passes 436 tests.

## Verified audit result — 2026-07-26

The checkout is **not release-ready**.

| Check | Verified result | Interpretation |
|---|---|---|
| Tests | **410 passed, 4 failed** in 7.93s | Four dashboard order-preview tests are stale against the current `$5.00` unit value/cost cap. |
| Critical focused tests | **84 passed** | `audit`, `cli`, `domain`, `forward`, and `xlsx_ledger` now have focused tests; the old “zero tests” claim was false. |
| Critical imports | Pass | All requested core modules and all feature/data-source modules imported. |
| Python/package | Python 3.14.5; editable install points to this project | Environment and installed console entry point are healthy. |
| Console entry point | Pass | `.venv/bin/model-prediction --help` exits 0. |
| Audit chain | **16,387 events, 0 link breaks, 0 hash mismatches** | Cryptographic chain is intact. |
| Ledger/audit reconciliation | **Not reconciled** | No current ledger row lacks a creation event, but 1,150 historical creation events lack a matching audited removal event. |
| Artifact integrity | **31 of 33 valid; 2 mismatches** | `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` fail canonical SHA-256 verification. |
| Config artifact references | One missing; one semantically wrong | `market_residual.artifact` is missing; MLB total research still points to its spread artifact. |
| Ruff | **117 findings** | 79 are `EXE002`; the remaining 38 include six broad exception catches and correctness/style findings. |
| CLI summary | Pass | 4 open picks, 7.75 open units, shadow-accounting note present. |
| Dashboard | Health/status/matrix pass | `/api/summary` no longer exists. `/api/status` reports `promotion_allowed=true` while also warning MLB is below its qualification gate. |
| MLB dry forecast | Pass, no logging | 15 games, 9 confidence calls, `logged=0`; active v6 artifact reports `qualified=false`. |
| Targeted line execution | Mixed | Stdlib tracing measured 65–100% on the core pipeline modules, but only **8.3%** on `cli.py`. |

### Four failing tests

All failures are in `tests/test_dashboard_server.py`:

- `test_resting_order_preview_and_submit_persist_exchange_id`
- `test_submit_parses_success_after_interactive_prompt`
- `test_buy_at_current_ask_submits_marketable_ioc_limit`
- `test_manual_control_can_buy_at_ask_when_positive_edge_gate_is_disabled`

The current config sets `unit_value_usd: 5.00`. The test orders cost `$5.50`,
`$5.50`, `$6.00`, and `$6.40`, while their authorized caps are `$5.00`,
`$5.00`, `$5.00`, and `$6.25`. `preview_order()` therefore refuses them before
creating a nonce. This is test/config drift, not evidence that the cost cap
itself is broken.

## P0 findings

### 1. Real-money execution ticket is not bound to the ledger pick

`PolymarketExecutor.execute()` checks the row's record type and status but does
not prove that the ticket's pick ID, market slug, token side, action, price, or
quantity belongs to that row
(`src/model_prediction/data_sources/polymarket_execute.py:91-148`).
The dollar cap trusts caller-supplied `estimated_cost_usd` instead of
recomputing `price * size_shares`. Submission happens before the audit append
(`polymarket_execute.py:162-187`), so an audit failure can leave a submitted
order unrecorded.

**Operational rule:** do not use the real-money execution surface until the
ticket is cryptographically/structurally bound to the exact ledger row and all
economic fields are recomputed server-side.

### 2. MLB probable-starter validation is not point-in-time

On a historical cache miss, `espn_probables.py` fetches the current ESPN
scoreboard for the historical date and then caches the response indefinitely
(`src/model_prediction/data_sources/espn_probables.py:57-123`).
`validation.py:197-210` consumes this as a historical feature without a
historical `observed_at_utc`.

The active MLB v6 artifact is therefore a live research experiment, not a
promotable historical validation result. Its own artifact correctly says
`qualified=false`; the config override does not cure the provenance problem.

### 3. WNBA availability does not fail closed on source conflicts

`player_availability.py` documents fail-closed behavior but defaults to the
research-only `most_conservative` conflict policy
(`src/model_prediction/features/player_availability.py:151-164`).
The production path does not request `fail_closed` and suppresses common
parsing/conflict exceptions (`player_availability.py:275-301`).
`learned_forward.py:291-301` also logs a skipped availability adjustment at
DEBUG and continues.

### 4. Current eligibility policy bypasses declared risk gates

As of the 2026-07-26 operator directive,
`src/model_prediction/eligibility.py:28-91` accepts but does not use exposure or
market-disagreement inputs. After model-state, staleness, provenance, and ban
checks, `_call_result()` always returns a `QUALIFIED_SHADOW_CALL` and sizes
with `edge_scaled_units`; the exposure-aware `recommend_units()` decision is
not consulted.

The CLI still applies a pre-log executable-ask edge floor for some forecast
paths, but config exposure caps and the maximum disagreement value no longer
gate eligibility. Documentation and dashboard language must not claim that
those gates are enforced.

### 5. Ledger mutation and audit append are not one transaction

Ledger writes commit before the corresponding audit event is appended
(`ledger.py:500-507,635-648,743-770,795-796`). A crash or
`AuditLockTimeout` can therefore leave a created, settled, voided, or removed
row without its audit event. Some retry paths return early once the ledger
already reflects the mutation, so retry does not necessarily repair the audit
gap. Existing tests do not inject an audit failure between these two commits.

### 6. Artifact qualification and quote timestamp validity are informational

`learned_forward.py:304-330` labels a confidence-threshold call
`QUALIFIED_SHADOW_CALL` even when `artifact.qualified` is false. The CLI routes
`calls`, not `qualified_calls` (`cli.py:756-760`), before later config/state
gating. Separately, quote matching returns `timestamp_valid`, but the caller
does not enforce it (`learned_forward.py:431-439`, `cli.py:791-815`).
An invalid pregame snapshot can therefore price a call.

## P1 correctness and integrity findings

### International baseball

- `forecast --sport kbo|npb` without logging passes no research ledger, but
  `_forecast_international_sport()` dereferences it
  (`cli.py:1190-1208,1852-1860`). Read-only preview can crash.
- An early edge check skips KBO/NPB rows before the function can fulfill its
  contract of logging all research rows and only eligible rows to the gated
  ledger (`cli.py:1111-1148`).
- Ties are graded as ordinary moneyline pushes (`cli.py:1466-1493`,
  `pricing.py:33-56`). A contract settling at `$0.50` is not a refund unless
  its entry price was `$0.50`; tie P&L and calibration are economically wrong.
- Secondary-ledger settlement details are computed but hidden from `settle`
  and `daily` output (`cli.py:1882-1900,2116-2139`).

### Market and source semantics

- The Odds API key is placed in a query URL and exception text is returned to
  the caller, which can expose the key
  (`data_sources/the_odds_api.py:85-90`,
  `data_sources/odds_soccer_scores.py:64-69`).
- Polymarket snapshot aggregation hardcodes `timestamp_valid=true` even when
  an individual snapshot is invalid, and can report `status=ok` with missing
  executable asks (`data_sources/polymarket_us.py:404-448`).
- Event discovery requests at most 50 events with no pagination and turns
  per-league HTTP failures into empty slates
  (`data_sources/polymarket_us.py:112-153`).
- `guaranteed_signal.py:40-55` treats future timestamps as fresh because it
  checks only that age is below six hours, not that age is non-negative.
- Soccer head-to-head treats draws as away wins
  (`features/head_to_head.py:20-35`).
- MLB weather extraction passes the wrong payload shape, wind is not applied
  to the run factor, and live weather selects the first forecast hour instead
  of the event hour (`data_sources/espn.py:224-250`,
  `features/weather.py:40-75,115-160`).
- Feature ingestion marks an event ID as seen before validating the row, so a
  malformed first copy can suppress a later valid copy
  (`features/base.py:101-150`).
- The economic bootstrap gate fails only when the ROI confidence interval's
  upper bound is below zero; an interval spanning zero passes even though it
  does not exclude loss (`economic_gate.py:165-168`). The module also states
  that these gates are not wired into live eligibility.

### Concurrency and auditability

Ledger and audit file locks now use non-blocking `flock` with a 30-second
timeout; the old “locks hang forever” finding is fixed. The remaining problem
is transaction scope: exposure is calculated before the CLI's append lock, and
paired research/gated writes are separate transactions
(`cli.py:895-929,1085-1092,1204-1208`). Concurrent writers can approve from the
same stale exposure snapshot or leave paired ledgers inconsistent.

`verify-chain` reports the current chain intact but reconciliation false because
1,150 old creation events do not have audited removal events. Preserve that
historical gap; do not fabricate removal events.

### Artifact and release alignment

- All 33 JSON artifacts carry a hash field, but the NBA and NFL spread
  baselines mismatch their canonical content.
- `config/model.yaml` still points MLB total research to
  `mlb-spread-baseline-v1.json`, not `mlb-total-score-ridge-v1.json`.
- `models.market_residual.artifact` points to missing
  `config/models/market-residual-v1.json`.
- `outputs/latest/learned-model-validation.json` still names an old worktree,
  points MLB at v5, and predates active MLB v6 plus current KBO/NPB artifacts.
  It is not a reproduced release report for this checkout.
- `model-prediction models` still reports Soccer, esports, KBO, and NPB as
  research because it prints static registry specs instead of config-derived
  status (`models/registry.py:136-200`, `cli.py:1739-1740`).

## Current test map

The old zero-test inventory is obsolete. Focused files now exist for:

- `audit.py`: `tests/test_audit.py`
- `cli.py`: `tests/test_cli.py`
- `domain.py`: `tests/test_domain.py`
- `forward.py`: `tests/test_forward.py`
- `xlsx_ledger.py`: `tests/test_xlsx_ledger.py`
- core sport models: `tests/test_sport_models.py`
- execution gate: `tests/test_execution_gate.py`

The five critical focused files (`audit`, `cli`, `domain`, `forward`,
`xlsx_ledger`) pass 84 tests. Remaining high-risk gaps include the exact
execution-ticket binding invariant, KBO/NPB half-settlement economics,
transactional exposure-plus-append behavior, fail-closed WNBA conflict
handling, and secret redaction from provider errors.

Targeted stdlib line tracing during the full run measured:

| Module | Lines executed |
|---|---:|
| `domain.py` | 100% |
| `xlsx_ledger.py` | 96.2% |
| `audit.py` | 93.5% |
| `economic_gate.py` | 90.8% |
| `ledger.py` | 89.2% |
| `learned_forward.py` | 81.2% |
| `eligibility.py` | 77.9% |
| `forward.py` | 65.0% |
| `cli.py` | **8.3%** |

The highest-risk remaining low-execution modules are `cli.py`,
`mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, and
`wnba_availability_evaluation.py`. Line execution is not proof of behavioral
coverage; transaction failure, timestamp validity, conflict handling, and
secret-redaction invariants still lack direct tests.

## Reproduction commands

Run from the project root.

### Health

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q

env PYTHONPATH=src:. .venv/bin/python -c "
import model_prediction.cli, model_prediction.validation
import model_prediction.learned_forward, model_prediction.eligibility
import model_prediction.ledger, model_prediction.forward
import model_prediction.audit, model_prediction.xlsx_ledger
print('All critical imports OK')
"

.venv/bin/python --version
.venv/bin/model-prediction --help >/dev/null
```

### Audit and ledger reconciliation

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

### Canonical artifact hashes

NOTE (2026-08-13): the codebase's real loader convention is
`ensure_ascii=True` — this snippet previously used `ensure_ascii=False` and
"mismatched" every artifact, including two historical "mismatch" reports that
were artifacts of this snippet, never real bugs. If a run reports MISMATCH,
check the artifact with `ensure_ascii=True` before treating it as a finding.

```bash
.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

for path in sorted(Path("config/models").glob("*.json")):
    raw = json.loads(path.read_text())
    key = "artifact_hash" if "artifact_hash" in raw else "model_hash"
    canonical = {name: value for name, value in raw.items() if name != key}
    computed = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    print(path.name, "OK" if computed == raw.get(key) else "MISMATCH")
PY
```

### Config artifact resolution

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

config = yaml.safe_load(Path("config/model.yaml").read_text())
keys = (
    "production_artifact",
    "research_artifact",
    "spread_research_artifact",
    "total_research_artifact",
    "artifact",
)
for model, item in config.get("models", {}).items():
    if not isinstance(item, dict):
        continue
    for key in keys:
        value = item.get(key)
        if value and not Path(value).exists():
            print(f"MISSING: {model}.{key} -> {value}")
PY
```

### Runtime, without writes

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary

curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/matrix | python3 -m json.tool

env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast \
  --sport mlb --date YYYY-MM-DD --model learned
```

Do not add `--log` to the forecast audit.

### Ruff

```bash
.venv/bin/ruff check src/ tests/
```

## Repair order

1. Bind and recompute every execution ticket field against the exact qualified
   ledger row before any real-money order can be submitted.
2. Make ledger mutation plus audit append recoverable as one transaction, and
   add failure-injection tests before relying on reconciliation.
3. Remove probable-starter data from historical validation unless each record
   has genuine pregame `observed_at_utc` provenance; keep MLB v6 unqualified.
4. Enforce artifact qualification and `timestamp_valid` before a candidate can
   be classified, priced, or logged.
5. Make WNBA availability conflicts fail closed and test malformed/conflicting
   source combinations.
6. Restore green tests by making dashboard tests explicit about unit value and
   intended order cost.
7. Repair the two mismatched spread artifacts, the missing residual reference,
   and the MLB total artifact reference without overwriting rollback artifacts.
8. Make KBO/NPB preview read-only, correct half-settlement P&L, and expose all
   secondary-ledger settlement results.
9. Make exposure-check-plus-append transactional across processes and preserve
   consistency between paired ledgers.
10. Redact provider secrets, enforce non-negative timestamp age, fix soccer draw
   and weather semantics, and make slate truncation/errors explicit.
11. Correct the economic confidence-interval gate so a zero-crossing interval
   does not pass as evidence of positive ROI.
12. Reproduce a new versioned report from one stable green checkout. Keep model
   quality separate from executable-price profitability.
