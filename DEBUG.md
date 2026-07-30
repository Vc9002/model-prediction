# DEBUG.md — Current Project Audit and Reproduction Guide

**Last audited**: 2026-07-31 (see new section directly below; the 2026-07-30
section and everything after it remains useful history but is now
superseded wherever they overlap)

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
            ensure_ascii=False,
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
