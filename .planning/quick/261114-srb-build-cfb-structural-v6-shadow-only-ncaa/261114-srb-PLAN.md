---
phase: quick-261114-srb
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - scripts/cfb_v6_lib.py
  - scripts/cfb_v6_history_backfill.py
  - scripts/cfb_v6_input_audit.py
  - scripts/cfb_v6_market_check.py
  - scripts/cfb_v6_research.py
  - outputs/ncaaf/cfb_v5_market_reference_games.json
  - outputs/ncaaf/cfb_v6_history_audit.json
  - outputs/ncaaf/cfb_v6_history_audit.md
  - outputs/ncaaf/cfb_v6_input_audit.json
  - outputs/ncaaf/cfb_v6_input_audit.md
  - outputs/ncaaf/cfb_v6_frozen_fit.json
  - outputs/ncaaf/cfb_v6_research.json
  - outputs/ncaaf/cfb_v6_research.md
  - outputs/ncaaf/cfb_v6_market_check.json
  - outputs/ncaaf/cfb_v6_market_check.md
  - src/model_prediction/features/cfb_features_v6.py
  - src/model_prediction/models/cfb_structural_v6.py
  - config/models/cfb-structural-v6.json
  - tests/test_cfb_structural_v6.py
  - docs/DEBUG.md
  - .planning/quick/261114-srb-build-cfb-structural-v6-shadow-only-ncaa/261114-srb-SUMMARY.md
autonomous: true
requirements:
  - V6-ITEM-1-ADDITIVE-MATCHUP
  - V6-ITEM-2-JOINT-RATINGS
  - V6-ITEM-3-PACE
  - V6-ITEM-4-ROLLING-BASELINE
  - V6-ITEM-5-WALK-FORWARD-REFIT
  - V6-ITEM-6-HISTORY-BACKFILL
  - V6-ITEM-7-MARKET-RESIDUAL
  - V6-AUDIT-WEATHER
  - V6-AUDIT-QB
  - V6-ACCEPTANCE

estimate:
  tokens: 215000
  raw_tokens: 215000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "The v5 market evidence is regenerated from the ledger DB and the v5 artifact before any v6 number is computed: totals corr -0.033, model side 218-257, model total SD 4.13; spreads corr +0.009, side 264-233, MAE 14.64 vs line 11.65, margin SD 11.78 / 16.35 / 22.37 (model / line / actual). The harness stops if it cannot."
    - "Every v6 projection uses only games whose kickoff plus the result buffer is at or before the decision time; adding a later game changes nothing (pytest proves it)."
    - "v6 expected points are additive (league average + offense deviation + opponent defence deviation + fitted home terms); the old averaging form fails the same test."
    - "Team strength comes from a joint ridge solve over every rated team, each opponent entering through its own rating; no conference table, hard-coded team lists or fixed 49.5 / 54.0 total baselines exist in v6."
    - "The v6 configuration is frozen with a sha256 on train/validation before the locked holdout or the 575 market check runs; both reports carry that hash and the holdout is scored once."
    - "Holdout Brier, log loss, calibration slope and margin/total log score are reported for v6 vs v5 on the common event cohort, with backfill-only games reported separately."
    - "The market check reports corr, slope, model-side record, MAE vs line, SD, over% by line bucket and the 21+ line favourite margin for v5-calibrated and v6 on the common covered set (v6 covered n stated, v5 full-set numbers alongside), and states plainly whether v6 shows positive out-of-sample correlation. No promotion decision is made."
    - "The v6 arithmetic passes a synthetic selfcheck (point-in-time, additive form, baseline) before the cache is built, so the holdout is never spent on untested math."
    - "v6 is inert: no serving module imports it and no route in config/production.yaml or config/model.yaml names it (pytest proves it in a clean subprocess), while its artifact loads and verifies its own hash."
    - "Live history files data/historical/* and data/processed/ncaaf/* are only read; the backfill lands under data/research/ncaaf_v6/."
    - "Item 7 (market-anchored residual) is recorded as BLOCKED with a data inventory and the unblock data named, unless lined NCAAF events outside the 575 that predate them are found."
  artifacts:
    - path: scripts/cfb_v6_history_backfill.py
      provides: "history gap audit and read-only ESPN groups=80/81 backfill builder"
    - path: scripts/cfb_v6_input_audit.py
      provides: "weather and QB input audit (read-only)"
    - path: scripts/cfb_v6_market_check.py
      provides: "575-game market-relative harness with v5 reproduction gate and lines inventory"
    - path: scripts/cfb_v6_research.py
      provides: "v6 cache, walk-forward fit, freeze, single holdout scoring, src parity gate, artifact writer"
    - path: src/model_prediction/features/cfb_features_v6.py
      provides: "joint ridge ratings, additive matchup, rolling baseline, v6 history loader, CFBV6MatchupFeatures"
    - path: src/model_prediction/models/cfb_structural_v6.py
      provides: "CFBStructuralV6Model challenger class and fail-closed artifact loader"
    - path: config/models/cfb-structural-v6.json
      provides: "challenger-only artifact, qualification.status research, self-verifying artifact_hash"
    - path: tests/test_cfb_structural_v6.py
      provides: "additive form, point-in-time, opponent rating, rolling baseline, raw weather, fail-closed, inertness"
  key_links:
    - from: scripts/cfb_v6_research.py
      to: src/model_prediction/features/cfb_features_v6.py
      via: "import after Task 2; parity gate proves bit-exact equality with the Task 1 cache"
    - from: scripts/cfb_v6_market_check.py
      to: scripts/replay_cfb_v4_all_settled.py
      via: "load_settled_rows and the replay_ledger point-in-time cut, loaded with importlib"
    - from: config/models/cfb-structural-v6.json
      to: outputs/ncaaf/cfb_v6_frozen_fit.json
      via: "artifact training block carries the frozen-fit sha256 and the backfill file sha256 and row count"
---

<objective>
Build cfb-structural-v6, a SHADOW-ONLY NCAAF challenger that fixes the five projection defects
the 2026-11-14 loss review found in the v5 feature module, and judge it with a market-relative
acceptance harness as well as the locked walk-forward holdout. No promotion, no routing change,
no edit to the v5 feature module or the v5 model module (v5 serves live and its calibration was
fitted on that module's output).

Purpose: v5 has no market-relative skill (totals corr -0.03, spreads +0.01 over the lined 2026
games) and compressed projections (model margin SD 11.8 vs line 16.3). v6 tests whether
individual ratings, an additive matchup and fitted dispersion fix the compression and whether
that yields positive out-of-sample correlation with the market residual. Fixing compression is
necessary, not sufficient; the report says which.

Output: research scripts and their reports under outputs/ncaaf/, a v6-only history backfill
under data/research/ncaaf_v6/ (gitignored), new v6 feature and model modules, a challenger-only
artifact, micro tests, a dated docs/DEBUG.md entry and the SUMMARY.

Executor note: consult the advisor tool before committing to the design (after reading this plan
and the required inputs) and again before declaring done.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.claude/CLAUDE.md
@docs/AGENTS.md
@.codex/skills/develop-model-prediction/SKILL.md
@src/model_prediction/features/cfb_features.py
@src/model_prediction/models/cfb_structural_v5.py
@src/model_prediction/models/cfb_structural_v4.py
@scripts/cfb_v5_research.py
@scripts/replay_cfb_v4_all_settled.py
@config/models/cfb-structural-v5.json

Absolute inputs (outside the repo, read-only):
- C:/Users/Vince/AppData/Local/Temp/claude/3c4e37e4a6ec2934/f86acfae-6a7a-455a-99d3-5fcdba232520/scratchpad/v5_rows2.py and v5_games.json (scratch replay and per-game dump; ephemeral, copy the dump first)
- C:/Users/Vince/.claude/projects/e--MP/memory/project_ncaaf-v5-no-market-skill-2026-11-14.md
- C:/Users/Vince/.claude/projects/e--MP/memory/project_concurrent-sessions-shared-tree-2026-11-12.md (temp-index commit recipe)

Facts established at planning time (2026-11-15 ~01:50Z, live observation):
- runs.db: latest daily row daily-20261115T013001Z-d9050c is `completed`. Next scheduled daily 05:30Z.
- data/historical/ncaaf_games_all.jsonl and data/processed/ncaaf/games.jsonl: 7,965 rows each, 2016-2026.
  `possessions` is the constant 12.5 on all 7,724 legacy rows and absent on the 241 newest
  (parser espn-scoreboard-1); `home_success_rate` is constant 0.42; `wind_mph`/`temperature_f`
  are placeholders (5 mph / 65 F on 7,542 rows, 0 / 72 on 182); `home_epa_per_play` varies
  (339 distinct values) but has no recorded provenance and is absent on the 241 newest rows;
  `is_neutral_site` and `week` are absent on the 241 newest rows. No drive or play fields exist.
- `_load_cfb_history` drops `is_neutral_site` and `week`; the replay script recovers them with
  `load_schedule_facts()`.
- `ESPNClient.scoreboard` sends no `groups` parameter for NCAAF (likely cause of the missing
  FBS-vs-FCS games); `ESPNClient.completed_games` normalizes a scoreboard payload.
- The v5 artifact's split: train 2016-08-27..2022-11-11, validation 2022-11-12..2024-11-14,
  locked holdout 2024-11-15..2026-11-12. The 575 lined games all fall inside that holdout window.
- Lines on disk: data/odds/ncaaf/ has 34 date folders from 2026-09-04 (same season as the 575);
  data/historical/ holds only an MLB line file; data/market_odds_snapshots.jsonl showed no NCAAF rows.
- The scratch dump v5_games.json has 577 events (575 with a total line, 576 with a spread line).
  The memory figures reproduce from it with v5 calibration (total = -5.6336 + 1.1017 t,
  margin = -0.4143 + 1.3189 m, line margin = minus the home spread) and this side-record rule:
  count a game only when |model - line| >= 1 and the result is not a push.
- No file in config/, src/, tests/ or scripts/ references cfb-structural-v6 today.
- data/research/ is gitignored; outputs/ncaaf/ is tracked (cfb_v5_base_rows.jsonl there is an
  untracked cache and must stay unstaged).
- Shell has MODEL_PREDICTION_RUNTIME_ROOT set; every pytest command below unsets the
  MODEL_PREDICTION_* variables.

Design decisions (Claude's discretion, record each in the SUMMARY):
- D-RES-BUFFER: a game feeds ratings only if kickoff + a result buffer <= decision time (named
  module constant, stated in hours, recorded in the artifact). This is stricter than v5's
  `start < observed`, which can admit a game in progress.
- D-RATINGS: offense and defense points ratings for every team from one weighted ridge solve per
  distinct eligible-game set: per team-game row, points = mu + off(team) + def(opponent) + home
  term (unpenalized `h_home` on the home row and `h_away` on the away row, zero on neutral
  sites), `mu` unpenalized (it is the rolling league scoring baseline, item 4). Window = previous
  season + current season to date, previous-season rows weighted `w_prev`. Ridge strength
  `lambda` and `w_prev` are chosen from a small grid on validation and stored in the artifact.
  No conference or tier strength table enters anywhere; tier is used only to label the
  FBS-vs-FCS dispersion slice. Season 2016 has no previous season and is burn-in: its rows are
  excluded from fitting and scoring (count reported).
- D-NO-HANDSET-TERMS: v4/v5 hand-set altitude, travel, bye, weather and QB point adjustments are
  not carried into v6: none was ever fitted and history has no real weather or point-in-time QB
  data. Their raw inputs are recorded in the v6 feature payload for audit. v6 has no Elo
  channel; opponent strength enters only through the joint solve (item 2's "opponent's own
  rating" requirement).
- D-NO-LINE-WIDENING: v6 SDs come only from fitted per-slice outcome residuals; v4/v5's
  line-dependent widening constants are not used (they were never fitted).
- D-SELECTION: validation chooses the variant by mean of margin and total Gaussian log score,
  moneyline Brier as tiebreak. v5 used Brier first; v6 targets margin/total quality.
- D-FIT-COHORT: fit and holdout cohorts are the events in the existing history sources (v5's
  cohort, burn-in removed). Backfill games are rating inputs only and are scored separately.
- D-SPLIT: `chronological_split` with `train_end_date="2022-11-11"` and
  `validation_end_date="2024-11-14"` so v6's holdout is v5's holdout extended to the pinned as-of.
</context>

<tasks>

<task type="tracer">
  <name>Task 1: Scripts-only v6 research end to end (history audit + v6-only backfill, input audit, joint ratings, walk-forward refit, frozen config, single holdout, 575 market check)</name>
  <files>scripts/cfb_v6_lib.py, scripts/cfb_v6_history_backfill.py, scripts/cfb_v6_input_audit.py, scripts/cfb_v6_market_check.py, scripts/cfb_v6_research.py, outputs/ncaaf/cfb_v5_market_reference_games.json, outputs/ncaaf/cfb_v6_history_audit.json, outputs/ncaaf/cfb_v6_history_audit.md, outputs/ncaaf/cfb_v6_input_audit.json, outputs/ncaaf/cfb_v6_input_audit.md, outputs/ncaaf/cfb_v6_frozen_fit.json, outputs/ncaaf/cfb_v6_research.json, outputs/ncaaf/cfb_v6_research.md, outputs/ncaaf/cfb_v6_market_check.json, outputs/ncaaf/cfb_v6_market_check.md; untracked, never staged: data/research/ncaaf_v6/ncaaf_games_backfill.jsonl, outputs/ncaaf/cfb_v6_base_rows.jsonl</files>
  <read_first>scripts/cfb_v5_research.py (build_cache, score_rows, fit_params, apply_variant, clustered_bootstrap_mean_delta, the incumbent-reproduction gate), scripts/replay_cfb_v4_all_settled.py (load_settled_rows, replay_ledger point-in-time cut, load_schedule_facts, derive_week), src/model_prediction/data_sources/espn.py (scoreboard, completed_games), src/model_prediction/models/college_football.py lines 205-280 and 920-975, src/model_prediction/features/cfb_qb_availability.py, src/model_prediction/features/multi_sport_weather.py::resolve_cfb_game_weather, src/model_prediction/validation.py::chronological_split</read_first>
  <action>
No src/ or tests/ edit in this task. Pin one as-of instant (AS_OF_UTC, the time this task starts)
and use it in every script and output, with the live history files' row counts and sha256 at load.

Tracer slice first, in this order, before any expansion:
(1) Copy the scratch dump v5_games.json verbatim to outputs/ncaaf/cfb_v5_market_reference_games.json
(the scratchpad is ephemeral). (2) In scripts/cfb_v6_market_check.py `reproduce-v5`: load settled
NCAAF rows with the replay module's load_settled_rows (importlib, as cfb_v5_research does), pick
one row per event per market by a rule you pin so that the event set and the total/spread lines
equal the reference file exactly, recompute v5's raw projection point-in-time per event with the
v5 feature extractor (history cut exactly as replay_ledger: min(start, observed), weather None,
QB 1.0; neutral from the row's stored model_input_snapshot features first, then schedule facts;
week from schedule facts, else derive_week; exactly as the scratch v5_rows2.py does). The
reference dump has no event id: match it on start + home + away. Assert the recomputed raw
projections match the reference to 0.1, then apply v5's mean calibration read from config/models/cfb-structural-v5.json (never
typed in). Gate: totals corr -0.033 (+-0.005), side 218-257, model SD 4.13 (+-0.05); spreads
corr +0.009 (+-0.005), side 264-233, MAE 14.64 vs 11.65, SD 11.78 / 16.35 / 22.37 (+-0.05).
Exit non-zero and stop the task if the gate fails. (3) Write scripts/cfb_v6_lib.py as pure,
port-ready functions with no I/O beyond reading the files handed to it, no imports from
scripts/, numpy only for linear algebra: the eligible-game filter (D-RES-BUFFER), the weighted
ridge solve (D-RATINGS) returning mu, h_home, h_away and per-team offense/defense, additive
expected points per side (item 1: mu + off_dev + opp_def_dev + home term; never the half-sum),
and the v6 history loader (existing sources with the same precedence and parse rules as
_load_cfb_history, plus is_neutral_site from the raw rows, then backfill rows; existing sources
win on score; neutral from raw rows else backfill; unknown neutral counted, treated as home).
Team identity: check whether legacy rows, new-parser rows, backfill rows and ledger names all
carry ESPN team ids; pin ONE canonical team key for the solve (the id where every source has it,
else a normalized name via resolve_team) and report every team that appears under two keys.
Opponents outside FBS/FCS (a team that neither resolve_team resolves nor appears in at least
two groups=81 events that season) are pooled into one rated pseudo-team per season; count those
games. Nothing in the lib references the v5 conference tier offsets, the clock-control or fast-tempo
team lists, the preseason decay table, or the fixed conference/non-conference total baselines.
Cache solves by (season, eligible-set index) so each distinct eligible set is solved once.
(4) Add `cfb_v6_research.py selfcheck` on synthetic history (no files): a game with kickoff +
buffer after the cutoff, or kicking off after it, changes no rating or projection; offense +10
vs defence +8 over a league average of 28 projects 46, not the half-sum 37; a history averaging
31 points per team-game gives mu within 0.5 of 31; beating a team the solve rates strong moves
a rating more than the same score against a weak one. `cache` refuses to run until selfcheck
passes. (5) Run one fixed configuration through cache -> train fit -> validation score on a
2-season slice to prove the chain end to end, then expand.
History snapshot: after B's build, write the loaded union (existing sources as of AS_OF_UTC plus
backfill) once to data/research/ncaaf_v6/history_snapshot.jsonl with its sha256 and row count;
cache, holdout, the market step and Task 2's parity all read that snapshot, never the live files
(the 05:30Z daily appends to them).
If Task 2 later finds an arithmetic bug, never re-run the holdout silently: re-freeze under a
new hash, label every later holdout and market result "second look" in the reports, the DEBUG
entry and the SUMMARY.

Expansion. Use parallel agents only on these disjoint files; if you do, run each with
OPENBLAS_NUM_THREADS=1 and at most 2 workers (memory note: BLAS failures under peer load).
- B, scripts/cfb_v6_history_backfill.py (item 6). `audit`: for every 2016-2026 regular and
  post season week, GET the ESPN college-football scoreboard read-only with groups=80 and
  groups=81 (limit 500, its own httpx request with a generic User-Agent carrying no project or
  personal identifiers; do not edit espn.py), paced at least 0.5 s
  apart with backoff on 429/5xx; normalize with ESPNClient.completed_games and add neutralSite,
  week and season type from the event. Compare by event_id with the existing sources. Report
  per season the counts of FBS-FBS / FBS-FCS / FCS-FCS games in existing history vs ESPN, the
  missing count per team for 2026 (confirm or correct "about 2 per team" and "16 of 65 on
  2026-09-26"), and score disagreements on overlapping events. `build`: write every fetched
  completed event to data/research/ncaaf_v6/ncaaf_games_backfill.jsonl with raw_source,
  parser_version cfb-v6-backfill-1, fetched_at_utc, neutral_site, week, season_year,
  season_type; record its sha256 and row count. The script refuses any output path under
  data/historical or data/processed and opens the live files read-only. If older seasons cannot
  be fetched, say so and report which seasons are covered: if only 2026 gains FCS-FCS games the
  rating regime differs between the fit years and the market check, and the report must say it.
- D, scripts/cfb_v6_input_audit.py (audit a and b, read-only; ledger DB opened with mode=ro).
  Weather: trace college_football.py's forecast path through resolve_cfb_game_weather to
  extract_features; state from the code when it returns None and that the extractor then uses
  70 F / 5 mph / 0 in, giving weather_total_adjustment 0.0 outside domes; count, over every
  NCAAF ledger row, rows with non-null wind/temp/precip in decision or feature payloads and rows
  with a non-zero weather adjustment; state that training history weather is placeholder only,
  so the weather term was never fitted. QB: confirm in cfb_qb_availability.py that a starter not
  on the injury report gets starter_prob 1.0 with status not_listed; count QB statuses across the
  ledger payloads; note what passing_leader_from_competitor returns pregame and whether that is a
  point-in-time starter. Also report the possessions / success-rate / EPA field facts above as
  item 3's data finding (no drive or play data; pace is estimated from scoring by the additive
  points model, which carries tempo through offense and defense points ratings).
- C, scripts/cfb_v6_market_check.py `lines-audit` (item 7): inventory every local NCAAF line
  source (data/historical, data/odds/ncaaf, data/odds/historical, data/market_odds_snapshots.jsonl,
  data/archive, the ledger) and count lined events outside the 575 that predate them. Item 7
  proceeds only if such a separable set exists; otherwise record BLOCKED and name the data that
  would unblock it (pregame spread/total quotes with observed_at for 2016-2025 seasons). Do not
  fit anything on the 575. Postgame or reconstructed prices are diagnostic only (docs/AGENTS.md).
- A, scripts/cfb_v6_research.py (items 1, 2, 4, 5). `cache` (after B's build): for every event
  in the existing history sources, compute v6 raw projected margin and total at kickoff for each
  grid point (lambda x w_prev, a few values each, chosen before looking at any metric), plus
  per-side current-season games in window and the FBS-vs-FCS flag; write
  outputs/ncaaf/cfb_v6_base_rows.jsonl. `fit`: split per D-SPLIT; drop burn-in rows; on train
  only fit margin and total mean calibration (OLS intercept/slope) and per-slice residual SD for
  margin AND total (slices established / thin_sample / fbs_vs_fcs, threshold k from a grid);
  choose (lambda, w_prev, k) on validation per D-SELECTION using the analytic Normal scorer from
  cfb_v5_research (import it, do not copy). `freeze`: write outputs/ncaaf/cfb_v6_frozen_fit.json
  with every chosen parameter, the grid, the selection table, AS_OF_UTC, the backfill sha256/row
  count and a sha256 of the canonical params; nothing after this may change it.
  `verify-frozen`: recompute that sha256 and exit non-zero on mismatch. The frozen fit also
  records the history snapshot sha256. `holdout`:
  refuses to run unless the frozen fit verifies and refuses to overwrite an existing holdout
  result. Incumbent check first: v5 on the cache, restricted to the recorded v5 holdout window
  (2024-11-15..2026-11-12), must give n 1,527 and a Brier within 0.002 of the recorded
  holdout.v5_engine.brier (0.18933) in outputs/ncaaf/cfb_v5_research.json (print the
  difference; stop if it fails). Planning-time measurement: analytic v5 on that window gives
  0.19003 over 1,527 rows; the v5 cache ends 2026-11-12, so later games are v6-only and are
  reported in their own block.
  Then it scores v6 and v5 (v5 = cached v4 projections from outputs/ncaaf/cfb_v5_base_rows.jsonl
  with the v5 artifact's calibration and SD table, same analytic scorer) on the common event
  cohort: Brier, log loss, ECE, calibration slope, margin and total log score, residual SD and
  coverage, date-clustered bootstrap CIs for the deltas, by season; backfill-only FBS-involved
  games and the v6-only events reported separately; writes outputs/ncaaf/cfb_v6_research.{json,md}
  with the frozen hash.
- C again, scripts/cfb_v6_market_check.py `v6` (after freeze): on the identical reference event
  sets (575 totals, 576 spreads) compute v6 calibrated projections point-in-time at each pinned
  row's decision time. v6 fails closed on unrated teams, so state v6's covered n per market and
  the reason codes of the uncovered events; compare v5 and v6 on the common covered set, with
  v5's full-set numbers alongside. Report, for v5-calibrated and v6 side by side: corr(model - line,
  actual - line) with a date-clustered bootstrap 95% CI, OLS slope, side record under the
  reference rule, MAE model vs line, SD of model / line / actual, over% by total-line bucket
  (under 45, 45-52, 52-60, 60 and over), side% by spread-line bucket (|line| under 7, 7-14,
  14-21, 21 and over), and on 21+ lines the mean model favourite margin vs line vs actual and the
  count of dog takes. Refuses to run without a verifying frozen fit and writes its hash into
  outputs/ncaaf/cfb_v6_market_check.json and outputs/ncaaf/cfb_v6_market_check.md. Verdict line
  per market: positive out-of-sample correlation yes/no (point estimate) and whether the CI
  excludes zero. No promotion language. Events settled after the reference set are reported in a
  separate block, never mixed in. `v6 --check-only` recomputes nothing: it re-reads the written
  JSON, confirms its frozen hash equals verify-frozen's, that the reference event counts are
  575/576 and that v6 covered n and the common-set n are recorded, and exits non-zero otherwise.

Commit at the end of the task (Task 1 commit, message prefix "feat(quick-261114-srb):"): the
five scripts and the tracked outputs above only, through the temp-index recipe described in
Task 3, trailer exactly
`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never stage
outputs/ncaaf/cfb_v5_base_rows.jsonl, outputs/ncaaf/cfb_v6_base_rows.jsonl or data/.
  </action>
  <verify>
    <automated>.venv/Scripts/python scripts/cfb_v6_research.py selfcheck && .venv/Scripts/python scripts/cfb_v6_market_check.py reproduce-v5 && .venv/Scripts/python scripts/cfb_v6_research.py verify-frozen && .venv/Scripts/python scripts/cfb_v6_market_check.py v6 --check-only && .venv/Scripts/ruff check scripts/cfb_v6_lib.py scripts/cfb_v6_history_backfill.py scripts/cfb_v6_input_audit.py scripts/cfb_v6_market_check.py scripts/cfb_v6_research.py</automated>
  </verify>
  <done>
reproduce-v5 exits 0 with the gate numbers printed; outputs/ncaaf/cfb_v6_frozen_fit.json exists
and verify-frozen confirms its hash; outputs/ncaaf/cfb_v6_research.json and
outputs/ncaaf/cfb_v6_market_check.json both carry that hash; `v6 --check-only` re-reads the
written market report and confirms hash and event counts (575/576) without recomputing.
Numbers on record: per-season FBS-FBS/FBS-FCS/FCS-FCS counts and 2026 missing-game counts;
backfill sha256 and rows; weather and QB counts; lines inventory with item 7 verdict; chosen
lambda, w_prev, k, buffer, calibration and SD table; holdout v6 vs v5 metrics with CIs; market
table for v5 and v6 with the plain-language verdict. Ruff clean on the five scripts.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Port v6 into src (features + model + artifact) with micro tests, bit-exact parity and proven inertness</name>
  <files>src/model_prediction/features/cfb_features_v6.py, src/model_prediction/models/cfb_structural_v6.py, config/models/cfb-structural-v6.json, tests/test_cfb_structural_v6.py, scripts/cfb_v6_research.py, scripts/cfb_v6_market_check.py, scripts/cfb_v6_lib.py (deleted)</files>
  <precondition>Task 1 is committed and outputs/ncaaf/cfb_v6_frozen_fit.json verifies; the latest daily row in runs.db is not 'started'.</precondition>
  <read_first>scripts/cfb_v6_lib.py, outputs/ncaaf/cfb_v6_frozen_fit.json, src/model_prediction/models/cfb_structural_v5.py (artifact loader, from_artifact fail-closed checks, forecast_game with capture_inputs), src/model_prediction/models/cfb_structural_v4.py lines 170-200 and 373-520 (constructor, predict_matchup, GamePrediction blocks), tests/test_cfb_structural_v5.py, tests/test_artifact_hash_verification.py</read_first>
  <behavior>
    - Additive form: offense +10 vs a defence +8 (points over league average 28, neutral) projects 46 for that side; the half-sum form would project 37 (the test is shown to fail when the half-sum is substituted, then restored).
    - Point-in-time: a game whose kickoff + buffer is after the cutoff, or that kicks off after it, leaves ratings and projections byte-identical; the same game moved before the cutoff changes them.
    - Opponent's own rating: beating a team rated strong by the solve moves a team's rating more than the same result against a team rated weak, independent of conference labels.
    - Rolling baseline: history whose team-games average 31 points gives mu within 0.5 of 31 (no fixed 49.5/54 baseline).
    - Raw weather/QB: wind/temp/precip None stays None in to_dict; numeric values pass through verbatim; no weather or QB term moves the projection.
    - Fail-closed: a team with no game in the window and no previous-season game raises with reason code NO_CALL_CFB_V6_UNRATED_TEAM; artifact with wrong model_version, hash mismatch, an unread parameter or a feature the extractor never produces raises ValueError.
    - Loader: tmp_path fixtures for both existing sources plus a backfill file; existing sources win on score, backfill fills missing events and neutral flags; a declared backfill sha256 that does not match raises. No test reads the real gitignored backfill file.
    - Positive artifact check: config/models/cfb-structural-v6.json loads, verifies its hash, builds the model, and a forecast's probabilities sum to 1 per market.
    - Inertness (behavioural, clean subprocess with MODEL_PREDICTION_* removed): importing model_prediction.learned_forward, model_prediction.models.college_football and model_prediction.cli.daily does not load either v6 module; production registry entries, champions/challengers maps and config/model.yaml routing contain no cfb-structural-v6.
    - Module isolation: the v6 feature module's AST imports nothing from the v5 feature module except the record dataclass.
  </behavior>
  <action>
First action, before any src/ or tests/ edit: read the latest daily row
(select run_id, status from runs where worker='daily' order by rowid desc limit 1 against
runs.db under $MODEL_PREDICTION_RUNTIME_ROOT, read-only). If status is 'started', wait and
re-check; do not edit. Re-run this check before every edit batch. If the work is not committed
by 05:25Z, stop editing src/ and tests/ until the 05:30Z run's row is no longer 'started'.

Port the Task 1 lib into src/model_prediction/features/cfb_features_v6.py with function bodies
copied verbatim (same arithmetic, same operation order) plus: CFBV6MatchupFeatures (frozen
dataclass; to_dict carries event id, start, teams, neutral flag, mu, h_home, h_away, each side's
offense/defense rating, projected home/away points, raw margin and total, per-side games in
window, sample_games, is_fbs_vs_fcs, eligible game count, history cutoff, backfill sha256 and
presence, raw_wind_mph / raw_temperature_f / raw_precipitation_in exactly as passed, and
qb_starter_prob_home / qb_starter_prob_away as passed) and a CFBV6FeatureExtractor whose solver
config (lambda, w_prev, buffer hours) comes from the artifact. Reuse the record dataclass from
the v5 feature module; import nothing else from it. Unrated team -> raise with the reason code
above (fail closed, never a guess).

Create src/model_prediction/models/cfb_structural_v6.py: CFB_V6_MODEL_VERSION =
"cfb-structural-v6", load_cfb_structural_v6_artifact (model_version and artifact_hash checks as
v5), CFBStructuralV6Model as a NEW class, not a subclass of v4/v5 (their predict_matchup runs the
v5 extractor). from_artifact rejects unknown features and unread parameters as v5 does.
forecast_game: calibrated means from the artifact, slice margin SD and slice total SD
(D-NO-LINE-WIDENING), CFBJointDistributionEngine with its existing defaults and the model's seed
stream exactly as v5 wires it (do not touch simulate_game or engine defaults), capture_inputs
from cfb_replay, returns CFBStructuralForecastV4 with the QB/altitude/weather fields 0.0
(D-NO-HANDSET-TERMS). predict_matchup mirrors v4's three GamePrediction blocks with v6 version
labels. Nothing registers the model; do not edit learned_forward.py, college_football.py,
cli/, config/production.yaml, config/model.yaml, the v5 feature module or the v5/v4 model modules.

Switch scripts/cfb_v6_research.py and scripts/cfb_v6_market_check.py to import from
model_prediction.features.cfb_features_v6 and delete scripts/cfb_v6_lib.py (one implementation).
Add `cfb_v6_research.py parity`: recompute the frozen configuration's raw projections for every
cached row from the src module, reading data/research/ncaaf_v6/history_snapshot.jsonl (its
sha256 must equal the frozen fit's), and assert exact float equality with outputs/ncaaf/cfb_v6_base_rows.jsonl
(print count and max abs diff, which must be 0.0), and compare the src model's engine moneyline
probability with the analytic one on 50 holdout rows (report max abs diff; informational).
Add `cfb_v6_research.py write-artifact`: build config/models/cfb-structural-v6.json from the
frozen fit (method, model_version, schema_version, sport/league as v5, distribution, mean
calibration, margin and total SD tables, established_min_sample, solver config, features,
training block with split, counts, frozen-fit sha256, AS_OF_UTC, backfill sha256/rows,
holdout and market-check summaries, qualification.status "research", qualified false, a note
that it is a challenger only and not routed), compute artifact_hash with
production_registry.compute_artifact_hash, then load it back through from_artifact. Gates are
not added to the artifact unless the model reads them.

Write tests/test_cfb_structural_v6.py per the behavior block (RED first: run it against the
missing modules, then GREEN). For the additive-form test, prove it catches the defect by
substituting the half-sum, watching it fail, and restoring.

Commit (Task 2 commit, message prefix "feat(quick-261114-srb):") through the temp-index recipe
in Task 3 with only the four new files, the
two edited scripts and the deletion of scripts/cfb_v6_lib.py; trailer exactly
`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
  </action>
  <verify>
    <automated>.venv/Scripts/python scripts/cfb_v6_research.py parity && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python -m pytest tests/test_cfb_structural_v6.py tests/test_artifact_hash_verification.py tests/test_cfb_structural_v5.py -o addopts='' -q && .venv/Scripts/ruff check src/ tests/ scripts/cfb_v6_history_backfill.py scripts/cfb_v6_input_audit.py scripts/cfb_v6_market_check.py scripts/cfb_v6_research.py</automated>
  </verify>
  <done>
parity prints max abs diff 0.0 over every cached row; the three test files pass; ruff is clean on
src/ and tests/; config/models/cfb-structural-v6.json self-verifies; scripts/cfb_v6_lib.py no
longer exists; git show --stat of the Task 2 commit lists only the intended paths.
  </done>
</task>

<task type="auto">
  <name>Task 3: Dated docs/DEBUG.md entry with the real numbers, SUMMARY, and safe commits on the shared tree</name>
  <files>docs/DEBUG.md, .planning/quick/261114-srb-build-cfb-structural-v6-shadow-only-ncaa/261114-srb-SUMMARY.md</files>
  <read_first>outputs/ncaaf/cfb_v6_history_audit.md, outputs/ncaaf/cfb_v6_input_audit.md, outputs/ncaaf/cfb_v6_research.md, outputs/ncaaf/cfb_v6_market_check.md, the concurrent-sessions memory note, docs/DEBUG.md lines 1-10 and 205-215</read_first>
  <action>
Write the entry text only when every number is final (memory note: a peer's commit once swept a
half-written entry). Heading: "## 2026-11-15 — NCAAF: cfb-structural-v6 shadow challenger and
market-relative check (quick-261114-srb)", inserted immediately above the first existing
"## 2026-11-14" heading; the parenthesised task id appears only in that heading. Content, numbers only from the outputs, percentages and points only
(never American odds): v5 reproduction gate; the five defects and what v6 does instead;
backfill coverage per season and the 2026 gap; weather and QB audit findings with counts; item 3
data limitation; chosen solver/calibration/SD parameters; holdout v6 vs v5 with CIs; market check
table for both models with the plain verdict; item 7 BLOCKED (or not) with the unblock data;
frozen-fit and artifact hashes; "No promotion decision. v6 is not routed." Leave docs/ROADMAP.md
untouched; list open items in the entry instead.

docs/DEBUG.md carries another session's uncommitted hunks. Add the entry to the working-tree
file with the Edit tool (foreign hunks preserved). Build the commit blob from HEAD, not from the
working tree: take the HEAD version of docs/DEBUG.md, insert the same entry at the same anchor,
hash-object -w it. Commit with a temporary index for every commit in this plan:
GIT_INDEX_FILE pointing at a scratch file, read-tree HEAD, update-index --cacheinfo for the blob
(or git add for files that are wholly yours), write-tree, commit-tree with parent HEAD,
update-ref HEAD new old. Before update-ref assert the tree diff against HEAD names exactly the
intended paths and that the docs/DEBUG.md diff is only your entry. Afterwards sync the shared
index only for your own paths (git reset -q -- path), except a path a peer had already staged
before you started (record git diff --cached --name-only first). Then confirm git diff HEAD --
docs/DEBUG.md shows only the foreign hunks. Never git add -A, stash, reset --hard or checkout
another session's files.

Write 261114-srb-SUMMARY.md (summary template): commits, every design decision (D-RES-BUFFER,
D-RATINGS, D-NO-HANDSET-TERMS, D-NO-LINE-WIDENING, D-SELECTION, D-FIT-COHORT, D-SPLIT), the
audit findings (a) and (b), the data limitations (no drive/play data, unverified EPA, placeholder
weather, retrospective tier labels), item 7 status, the acceptance tables, and the explicit
statement whether v6 passes the market-relative test. Commit the entry and SUMMARY together
(Task 3 commit, message prefix "docs(quick-261114-srb):") with trailer exactly `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
Then verify every commit of this plan in a detached worktree of HEAD: ruff on src/ and tests/
and the three Task 2 test files with MODEL_PREDICTION_* unset; remove the worktree afterwards.
  </action>
  <verify>
    <automated>DBG="$(git show HEAD:docs/DEBUG.md)" && MSGS="$(git log --format=%B --grep='quick-261114-srb')" && test "$(printf '%s\n' "$DBG" | grep -c '(quick-261114-srb)')" -eq 1 && test "$(printf '%s\n' "$MSGS" | grep -c 'Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>')" -ge 3 && test -f .planning/quick/261114-srb-build-cfb-structural-v6-shadow-only-ncaa/261114-srb-SUMMARY.md</automated>
  </verify>
  <done>
HEAD's docs/DEBUG.md has exactly one quick-261114-srb entry with real numbers and no peer hunks
committed; the SUMMARY exists and states the market-relative verdict and item 7 status; each of
the three commits carries the exact trailer and only its intended paths; the detached-worktree
check passes; the shared working tree still holds the other sessions' uncommitted edits unchanged.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| ESPN public API -> backfill file | Untrusted JSON enters a research data file that feeds ratings |
| ledgers.db -> research harness | Live ledger read for evaluation only |
| research code -> serving path | v6 must not reach learned_forward, champion routes or orders |
| this session -> shared git tree/index | Peer sessions hold uncommitted and staged edits |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-srb-01 | Tampering | scripts/cfb_v6_history_backfill.py | medium | mitigate | Type-check ids, scores (int), timestamps (parse_utc) per event; drop malformed rows and count them; output path refused under data/historical and data/processed; sha256 recorded in the artifact and checked by the v6 loader |
| T-srb-02 | Tampering | ledgers.db | high | mitigate | Open with file:...?mode=ro URI only; harness never writes ledgers, audit logs or the runtime root |
| T-srb-03 | Elevation of Privilege | v6 reaching serving or orders | high | mitigate | Behavioural inertness test in a clean subprocess plus config route check; no edit to learned_forward, college_football, cli, production.yaml, model.yaml; no order endpoint called |
| T-srb-04 | Tampering | shared git index/tree | high | mitigate | Temp-index commits, HEAD-based blob for docs/DEBUG.md, pre-update-ref path and content assertions, detached-worktree verification |
| T-srb-05 | Information Disclosure | evaluation integrity (holdout / 575 peeking) | high | mitigate | Frozen-fit sha256 before holdout and market check; holdout command refuses a second run; the 575 never enter selection |
| T-srb-06 | Denial of Service | ESPN rate limits | low | mitigate | Requests paced >= 0.5 s with backoff on 429/5xx; partial coverage reported, never silently filled |
| T-srb-SC | Tampering | npm/pip/cargo installs | high | mitigate | No package installs in this plan (numpy and httpx are existing dependencies); any install need stops the task |
</threat_model>

<verification>
- Source coverage: items 1, 2, 4, 5 -> Task 1 (research) and Task 2 (src + tests); item 3 -> Task 1 input audit (data finding) and Task 2 (no team lists, scoring-based tempo); item 6 -> Task 1 backfill + loader, Task 2 loader port and tests; item 7 -> Task 1 lines-audit, Task 3 BLOCKED record; audits (a)/(b) -> Task 1 input audit, Task 2 raw weather/QB in payload, Task 3 docs; acceptance -> Task 1 holdout and market check, Task 3 docs. No item is deferred.
- Hard constraints: src/tests edits only in Task 2 behind the runs.db gate; targeted pytest only with MODEL_PREDICTION_* unset; ruff on src/ and tests/ clean; v6 inert; simulate_game and engine defaults untouched; live history files read-only; percentages only; exact Sonnet trailer; no promotion.
- validation.py::build_walk_forward_rows builds generic FeatureStore rows and is not expected to fit the cached-projection cohort (the v5 research harness, whose structure the brief says to reuse, also uses only chronological_split); the executor confirms by reading its signature and records the reason in the SUMMARY, or uses it if it fits.
- Scope note: Tasks 1 and 2 each touch more than five files because the orchestrator asked for 1-3 workstream tasks; Task 1's files split into four disjoint parallel workstreams.
</verification>

<success_criteria>
- v5 market evidence reproduced from code and ledger before any v6 number (gate exit 0).
- v6 frozen with a hash before holdout and market check; both reports carry it.
- Holdout and market-check tables for v5 and v6 in outputs/ncaaf/, docs/DEBUG.md and the SUMMARY, with a plain statement whether v6 shows positive out-of-sample market-relative correlation.
- Bit-exact parity between research cache and src module; tests and ruff green; artifact self-verifies; v6 proven inert.
- Item 7 status and audit (a)/(b) findings recorded with counts.
</success_criteria>

<output>
Create `.planning/quick/261114-srb-build-cfb-structural-v6-shadow-only-ncaa/261114-srb-SUMMARY.md` when done
</output>
