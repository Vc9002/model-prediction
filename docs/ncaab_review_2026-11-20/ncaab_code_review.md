# NCAAB serving path: read-only code review (2026-11-20)

Scope: `college_basketball_forward.py`, `models/ncaab_spread_v6.py`, `ncaab_total_v6.py`, `ncaab_v7_serving.py`,
`ncaab_v7_multiseason.py`, `features/ncaab_scoreboard_stats.py`, `features/ncaab_possessions.py`,
`features/trends.py`, `features/elo_ratings.py`, configs, live ledger (`E:\model-prediction-runtime\ledgers\ledgers.db`,
read-only) and the auto-buyer state. No files edited. One targeted test run: `tests/test_ncaab_v7_serving.py`
(9 passed). Reported only what is new or still open relative to `NCAAB_LOSS_AUDIT_2026-11-10.md`,
`NCAAB_V6_REVIEW_FIXES_2026-11-12.md`, the DEBUG NCAAB entries and HANDOFF section 1.

## Exposure map (what the findings can cost)

- **No NCAAB Main row since 2026-11-10T23:43Z.** All 5,371 NCAAB rows since 11-11 are Flat. Main is held by the
  current-season sample gate (>= 8), which teams start reaching in late November.
- **The main paper auto-buyer does not buy NCAAB.** The live `auto_buyer_state.json` lists all three NCAAB
  markets in `disabled_sport_markets`, blacklists every NCAAB model, and has `ncaab_spread_v6_live_bucket.enabled = false`.
  This is the operator's existing state. It is reported, not recommended.
- **The one staked NCAAB path is the spread paper trial.** `auto_buyer_ncaab_spread_paper_trial.json` is
  enabled for model `ncaab-spread-v6`, spread, saved edge 5% to 8%, and buys from Main and Flat
  (`portfolio/ncaab_paper_trial.py:64-86`). It holds 46 fills in `paper_auto_buyer/auto_buyer_ledger.jsonl`;
  15 of them (6W-9L) were placed since 11-16 on v7-priced rows.
- Main exposure starts when samples reach 8. Under v7 that is the narrowest scale bucket (9.97), covered in finding 2.

## Ranked findings (most likely dollar impact first)

### 1. v7 margin engine is live through an uncommitted yaml edit, under the unchanged `ncaab-spread-v6` id, with no artifact hash on any row (NEW)
- `git diff config/model.yaml` has one hunk: `models.NCAAB.margin_engine: v7` plus a comment "Operator wired
  2026-11-15 ... Forecast-quality gain only, no market edge shown". Nothing else in the file differs from HEAD.
  The scheduled tasks run the working tree, so this is live. The ledger's `feature_basis.margin_engine` is `v7`
  on every NCAAB spread and moneyline row from 2026-11-16 on: 1,635 rows, and 1 spread row on 11-16 has no tag.
- No promotion record exists. `production.yaml` still routes moneyline and spread to `ncaab-spread-v6`, the
  artifact hash is unchanged by design (`ncaab_spread_v6.py:264-267`, the "runtime switch, not an artifact parameter"),
  and v7 does not appear in DEBUG, ROADMAP or PROJECT_STATUS. This conflicts with the CLAUDE.md rule that promotion
  goes through `model_promotion promote`, never a hand-edited yaml.
- **NCAAB is the only in-scope sport with no `model_artifact_hash` on any ledger row** (0 of 2,183 since 11-16).
  NBA, NCAAF and NFL rows all carry one. `production.yaml` registers NCAAB as `code_backed_model` with no artifact
  field, so nothing propagates.
- **Measurement consequence.** The pinned HANDOFF forward check is "`ncaab-spread-v6`, spread, edge 5%-8%, picks on
  or after 11-12". The paper-trial cohort (`eligible_trial_pick`, which keys on model_id) now mixes v6 pricing
  (11-12 to 11-15) with v7 pricing (11-16 on) and cannot tell them apart. Calibrators are refit on the same mixed
  population. They are identity today (`champion_method: identity`, n = 219 / 394 / 339).
- Fix candidates (not a revert):
  - Give v7 its own model id/version, or at least stamp `margin_engine` plus a v7 config hash into `model_version`
    or `model_artifact_hash`.
  - Commit the switch with a promotion record.
  - Split the forward check and the paper-trial cohort by `feature_basis.margin_engine`.

### 2. v7 replaces v6's early-season widening with narrower fixed scales; the forward evidence says they under-disperse (NEW; ablation candidate)
- v6 widens the t-scale with thin samples: 10.5 x (1 + 0.5 x (1 - s/8)), i.e. 15.75 at s = 0 and 12.47 at s = 3
  (`ncaab_spread_v6.py:558-564`). v7 swaps in fixed per-bucket scales of 11.27 / 10.87 / 10.71 / 9.97
  (`ncaab_v7_serving.py:54`; applied at `ncaab_spread_v6.py:566-574`). These were fit on 2022-23 and 2023-24.
  Converted to t(df 8) SDs they are 13.0 / 12.6 / 12.4 / 11.5.
- The research report's own 2026-27 partial table (`outputs/ncaab/ncaab_v7_multiseason.md`) shows v7 RMSE of 14.41 at
  0 games played (bias +3.9, n 149) and 12.32 at 1-2 games, against those served SDs.
- Forward evidence: on logged spread rows since 11-16, the v7 margin residual in the 3-7 bucket has SD 13.7 and bias
  -0.6 (n = 133 events), against an implied SD of 12.4. Read this as an ablation candidate, not a proven bug: n is small
  and it counts logged rows only.
- Settled Flat v7 spreads: mean model probability 58.6% against a 48.4% hit rate and a 50.8% mean market price
  (n = 153). Settled v7 moneylines: 62.1% against 53.2% (n = 47). Only 11 and 6 of those rows had executable quotes,
  and the rows are not deduplicated across reruns. Treat both numbers as indicative.
- Why it matters: a sharper distribution inflates every |p - 0.5|, and with it the edges the forward ranks and the
  paper trial buys. The 8+ bucket (9.97) is the one that reaches Main first.
- Ablation to run (fit window only, as the v7 pin rules require):
  - Rescale the v7 buckets on current-season dispersion, or keep v6's `dynamic_volatility` multiplier on top of the
    v7 scale.
  - Judge by cover-probability PIT and Brier on quoted rows, not by RMSE.

### 3. The moneyline is still half Elo-logistic, not "read off the margin distribution"; under v7 it mixes two engines (NEW)
- `ncaab_spread_v6.py:624`: `p_home = elo_w * home_win_elo + (1 - elo_w) * p_margin_home`, with `elo_w` = 0.5
  (artifact `elo_weight` 0.5, ramp off). `coherent_moneyline` only changed the margin half (line 614).
  `git show e32335c7` shows the blend predates the "coherence" fix.
- `tests/test_ncaab_v6_review_fixes.py:55` tests coherence with `elo_weight=0.0`, which hides the blend.
- The docs are therefore wrong:
  - `NCAAB_V6_REVIEW_FIXES` table #10 says "P(home) = 1 - T((0.5 - mu)/scale)".
  - The 261115-v7s SUMMARY says "moneyline and spread cover probabilities are derived from v7's mean/scale".
- Under v7 the Elo half is still v6's Elo (HCA 45, no venue-aware history), while the margin half is v7. Measured on
  the ledger, comparing the logged P(home) with the pure t(8) margin probability:

  | rows | n | mean abs gap | max abs gap |
  |---|---|---|---|
  | v7 moneyline rows | 388 | 3.2 pp | 13.4 pp |
  | v6-era rows (11-13 to 11-15) | 650 | 2.1 pp | 5.7 pp |

- The v7 research scored win probability with the pure t formula (`scripts/ncaab_v7_multiseason.py:391,635`), so the
  **served v7 moneyline was never evaluated**.
- Fix or ablation: score the served blend against pure `1 - T((0.5 - mu)/scale)` on the fit window, then serve one
  rule and record it.

### 4. Team identity: the mascot is dropped whenever a spread market is iterated first, so D1 teams fall to the school-only path (NEW; coverage loss now, collision risk by construction)
- How it happens:
  - `college_basketball_forward.py:477-500` takes away/home school and mascot from the first market whose two sides
    both carry `team`.
  - Spread sides have descriptions like `+12.50`, which the digit filter rejects, so `away_mascot` / `home_mascot`
    stay `""`.
  - `_resolve_ncaab_team` (lines 109-177) then runs school-only.
- Replay of the real 2026-11-21 Polymarket slate (56 events, read-only GET) through that exact code:
  - Five D1-vs-D1 games were declined `NO_CALL_NON_D1` because the school did not resolve: Cornell, UCA, UNCW, NAU
    and Pacific (shown as `src ('spread', '+12.50', '-12.50')`).
  - With the moneyline mascot the same names resolve. My ESPN school+mascot test gave 0 of 365 mismatches.
- Daily logs and the ledger, 11-10 to 11-20: zero rows for games of Alabama, Notre Dame, California, Cornell,
  Delaware, Hawai'i, App State, UNCW, NM State, Grambling, Southeastern Louisiana and IU Indy, although Polymarket
  listed them. Examples: Kennesaw State vs. Alabama, Auburn vs. Notre Dame, UNCW vs. LIU are all `NO_CALL_NON_D1` in
  `data/logs/daily_2026-11-1{7,8,9}.log`.
- Today this fails closed (missed picks, not wrong prices). But school-only token ranking is exactly the path that
  produced the UConn -> Central Connecticut and UMass -> UMass Boston mispricings. The alias patches
  (`0de74638`, `54e0028b`) fix symptoms, not the path.
- Fix candidate:
  - Prefer the moneyline market's descriptions for the mascot, or collect the mascot from any market whose
    description has no digits.
  - Add aliases for Polymarket abbreviations: UCA, UNCW, NAU, NM State, Tenn. State, Ore. State, IU Indy,
    Grambling State (history "Grambling Tigers"), Appalachian State (history "App State Mountaineers").
- No both-sides collision exists in Flat: 0 of 606 event-markets since 11-14 hold both selections live or settled.

### 5. `config/model.yaml` NCAAB model parameters are dead (Q1)
- `spread_sd 10.5`, `total_sd 13.5`, `home_court_points 7.2`, `logistic_scale 9.5` and `max_moneyline_margin 10.0`
  have no reader. I searched all of `src/` for attribute and string-keyed reads, and the dashboard and scripts.
  NCAAB goes through `forecast_ncaab_sport`, which builds the models only from the hash-verified artifacts
  (`ncaab_spread_v6_model()`, `ncaab_total_v6_model()`). The served values are HCA 5.0, Elo HCA 45, margin_sd 10.5
  and total_sd 16.5.
- The policy keys from that block are live:
  - **Live:** `min_edge`, `min_team_games`, `min_moneyline_ask`, `max_spread_line`, `max_margin_divergence`,
    `max_total_divergence`, `margin_engine`.
  - **Read with code defaults; none set in the yaml:** `max_quote_age_seconds`, `max_same_direction_totals`,
    `decline_non_d1`, `main_totals_enabled`.
- `max_moneyline_margin` is read nowhere.
- The artifact's `logistic_scale` 9.5 is also inert, because `coherent_moneyline` is true.
- The NCAAB `LeagueConfig` in `models/basketball_leagues.py:121` (HCA 3.6, total_sd 13.8) serves only the
  rollback tempo models.
- Fix: delete the dead yaml keys, or comment them as not served, so that nobody tunes a no-op.

### 6. The offseason discount is not applied before a team's first game of a new season (NEW; affects next season's opening week, Flat)
- Elo: `build_elo` regresses toward the priors only when it processes the first history game after a league-wide gap
  longer than 150 days (`elo_ratings.py:189-196`). On the first slate of a season, history ends last March, so
  ratings carry 0% of the intended 65% regression.
- Trends: `ewm_level` only discounts gaps *between* history games and seeds `current_season_games = 1`
  (`trends.py:89-108`).
  - Before a team's opener, every prior-season game counts as current-season. There is no 0.35 retention, and the
    full prior season enters `effective_n`.
  - The discount switches on abruptly after the team's first game.
  - The v6 `sample` correctly reads 0 (`_effective_current_season_games`), so only the levels are affected, not the
    volatility.
- Impact: Flat only, and only for each team's opener, which was most of the 11-03 to 11-08 slates. It also shifts the
  ablation's season-opener train-window numbers. `trends.py` is shared with other sports.
- Fix candidate: apply regression and retention relative to the decision time, not the next history game.

### 7. v6 history ignores neutral sites, and the trend points are not venue-adjusted (ablation candidate)
- `GameRecord` has no neutral flag.
  - `EloBook.update` applies the 45 Elo home advantage to every history game, including MTE and neutral games
    (`elo_ratings.py:121-131`).
  - `TrendEngine` uses raw points scored and allowed, with only a multiplicative opponent adjustment.
  - `non_d1_prior_from_history` subtracts the full 5.0 HCA from every D1-home game.
- Early-season schedules for high-majors are mostly home buy games, so their offense levels carry HCA, and HCA is
  then added again in `trend_margin`.
- The v7 ridge does handle venue (`history_neutral`, `ncaab_v7_multiseason.py:325`). Under v7 this defect still
  reaches the non-D1 games, the Elo half of the moneyline (finding 3) and the 0.16 x v6 tail term.
- Ablation: feed `NCAABScoreboardStats.neutral_site` into the Elo update and trend inputs (the data is on disk for
  99.96% of history).

### 8. v7 operational fragility (NEW; low dollar impact now)
- **Silent full fallback next season.** The conference map is frozen in the archive for season_years 2022-2027.
  For season_year 2028, `tier_by_season.get(2028, {})` is empty, every game is non-D1, and v7 serves nothing without
  raising, so no warning is logged (`ncaab_v7_multiseason.py:265-276`, `ncaab_v7_serving.py:222-224`).
  Fail-closed, but invisible.
- **Weak archive integrity check.** It is verified only against the sha in its own meta line
  (`ncaab_v7_serving.py:115`), not against a hash pinned in code. A rebuilt archive passes silently. The tests pin the
  combiner constants, not the archive.
- **Long-lived processes keep the first load.** `default_serving()` caches the archive and the live scoreboard stats
  per process (lines 236-244). The daily is a fresh process, but a long-lived caller keeps the first load's live stats
  (ranks, neutral flags).
- **Runtime.** NCAAB forecast time went from 80-135 s (11-15) to 200-300 s (11-17 to 11-20); the timing coincides with
  v7, I did not profile it. Structural reasons in the code:
  - `components()` re-runs both v6 models over the roughly 31k-game merged history, once per upcoming line.
  - It fits all 48 ridge variants in order to serve one.
- The research report also finds the v6 tail term adds nothing: v7 minus ridge-only RMSE +0.001.

### 9. Totals are still pure v6 (Q2) and have no joint coherence with the spread (still open, minor)
- The total model never reads `margin_engine`. Its overtime bump uses the total model's own
  `home_points - away_points`, without HCA and not the served spread mean (`ncaab_total_v6.py:455-461`).
- The credibility shrink pulls the projection toward the market line: z = s/(s+4), so 0.667 at the Main sample of 8.
- The SD widening (x1.13 at s = 8) remains as HANDOFF already notes.
- The +23.25u on 211 settled Flat totals since 11-13 is indicative only (13 quoted) and not evidence of edge.

## Answers to the seven questions

**Q1. Which parameters serve.**
- The artifact JSONs serve: spread HCA 5.0, Elo HCA 45, margin_sd 10.5, t df 8, rank prior 8 per slot,
  history-derived non-D1 prior (logged at about 445), retention 0.35; total `total_sd` 16.5, attempts tempo,
  sensitivity 1.5.
- The yaml model parameters are dead (finding 5).
- The only uncommitted yaml change is `margin_engine: v7` (finding 1).

**Q2. v7.**
- *What it replaces:* for D1-vs-D1 games (by the 2026-27 as-of conference map) it replaces the margin mean and the
  scale, and it also zeroes rest/density and availability points (`ncaab_spread_v6.py:566-574`).
- *Formula:* mean = 0.9593 x ridge(`rg_c010_l02_k14`: carry 0.1 per season of age up to 3 seasons, lambda 2, tier
  prior x1.4 plus AP rank 8 per slot, ridge-fitted HCA with prior 3.5) + 0.1611 x tail(v6 as-of mean beyond 12).
  Scale is by sample bucket.
- *How it was fit:* constants were fit on 2022-23 and 2023-24 and decided on 2024-25. History is the frozen
  2021-25 archive merged with live history, cut at ET midnight of the earliest slate day.
- *Market evidence:* no edge over the closing line (11.50 against the line's 11.19 RMSE on 2024-25).
- *Fallback to v6:* switch off; any exception (whole slate, with a warning); a non-D1 team on either side; or a
  missing ridge value (per game).
- *What gets recorded:* the engine is recorded as `feature_basis.margin_engine` in both the feature payload and the
  model input snapshot. The model id, model version and artifact hash do not change.
- *Coherence:* the spread is coherent with v7. The moneyline is not (finding 3). The total is pure v6 (finding 9).

**Q3. Point-in-time.** No leak found.
- `FeatureStore.games_before` cuts at ET midnight; the game list is sorted, and dedup keeps the first-ingested row.
- Scoreboard stats and possessions are keyed by event id and served only for history games, and only `completed`
  events load.
- The v7 history cut is ET midnight. Conference membership comes from the season's map, which is public pre-season.
- AP ranks in the archive are per game (Houston 2023-24 moves 7 -> 6 -> 3 -> ... -> 1), so they are pre-game ranks,
  not ranks as of fetch time. That partly resolves the research caveat "not verified as pre-game".
- Same-day early results are excluded, which is conservative.
- The non-D1 prior and the rank prior use only history before the cutoff.
- The remaining timing issue is finding 6 (stale, not leaky).

**Q4. Neutral sites.**
- The only source is the ESPN scoreboard (`groups=50`, `limit=500`, `espn.py:258-266`), matched by the resolved
  team pair (`college_basketball_forward.py:427-443`).
- The Polymarket keys `neutral_site` and `is_neutral` are never populated: the slate code has no neutral field, so
  lines 520-521 are dead.
- It checks out on the ledger: all 44 ESPN-neutral events since 11-10 were flagged neutral, with 0 false neutrals.
  (In 23 of them ESPN listed home and away the other way round, which does not matter on a neutral court.)
- A neutral flag sets HCA, Elo HCA and the altitude bonus to 0, and gives 50/50 tempo control in totals.

**Q5. Team identity.**
- The aliases fixed the known collisions, and no both-sides rows exist.
- The structural risk (school-only resolution) and the current coverage loss are finding 4.

**Q6. Early season.**
- *Priors:* conference-tier Elo priors (1270-1680) plus the AP-rank lift, with the non-D1 prior from history.
- *Regression and retention:* Elo regresses 65% toward the priors after a 150-day gap; trend retention is 0.35
  (spread) and 0.20 (total). Both start one game late (finding 6).
- *How fast current data takes over:* the trend EWM uses half-life 10 shrunk toward the tier strengths; Elo weight
  is a constant 0.5 (the ramp is off). The total shrinks to the line with z = s/(s+4). Main requires s >= 8.
- *Dispersion:* under v6 the scale widened at most 1.5x (15.75), still short of the realised 22-24 (known). Under v7
  the D1 scale is narrower still (finding 2).

**Q7. Signal inventory, as it serves now.**
- **D1-vs-D1 spread (v7):**
  - Main term: the multi-season ridge, built from margins weighted by season, conference-tier prior x1.4 with the
    legacy Pac-12 era override, AP rank, and a venue-aware HCA.
  - Plus 0.16 x the v6 tail.
  - Rest, density, altitude and the 5.0 HCA reach the margin only through that tail.
  - Scale comes from the sample bucket.
- **Non-D1 spread, and the margin half of every moneyline outside v7 (v6):**
  - Elo inputs: tiered conference Elo prior, AP rank prior, non-D1 prior from history, Elo (K 20, margin-of-victory
    multiplier, HCA 45 Elo, offseason regression).
  - Trend inputs: opponent-adjusted trend offense/defense at half-life 10, shrunk to the tier strength, additive matchup.
  - Venue: HCA 5.0, altitude table (8 home teams), neutral zeroing.
  - Schedule: rest (1.2 days or less: penalty; 4 days or more: bonus) and 3-in-4 density.
  - Distribution: t(8) with sample-scaled width.
- **Moneyline:** 0.5 x Elo win probability + 0.5 x margin-distribution probability.
- **Total:** opponent-adjusted trend offense/defense shrunk to conference scoring baselines (a hard-coded table);
  attempts tempo (FGA + 0.44 x FTA), EWM shrunk over 12 games, sensitivity 1.5; home tempo control 0.6; league pace
  68.1; short-rest and 3-in-5 drags; overtime bump; credibility shrink to the market line; SD 16.5 x sqrt(pace) x
  credibility widening.
- **Hooks that exist but are always 0 for NCAAB:** `availability_points_gap`, `availability_total_loss`. The forward
  never sets them.
- **Absent:**
  - Injuries / availability.
  - Roster continuity, returning minutes, transfers, coaching changes.
  - External preseason ratings (KenPom, Torvik, BPI).
  - Per-possession efficiency splits. The box-score OREB/TOV data is on disk and unused for the spread.
  - Shooting regression / 3-point variance and FT rate (3PA/3PM on disk, unused).
  - Team-specific HCA, early-season buy-game HCA, travel distance and time zones.
  - Altitude for visiting teams or for non-listed venues.
  - Venue-adjusted history (finding 7).
  - Conference-game flag (on disk, unused).
  - Referees.
  - Line movement / market priors for the spread or moneyline (only totals use the line).
  - Blowout / garbage-time damping.

## Verified as already fixed (not re-reported)
- UConn / UMass / Rutgers aliases (`0de74638`, `54e0028b`).
- ET-midnight history cut.
- Integer-line push.
- Artifact hash verification in the factories.
- Divergence gate in the away frame.
- Placeholder 50% rows staking 0.
- Same-direction totals cap.
- Main totals behind the sample gate.
- Restore after rerun excludes declined slots: no both-sides rows found.
