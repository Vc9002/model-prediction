# NFL model research audit — 2026-10-27

Scope: all three NFL markets (moneyline, spread, total), current serving code,
local evidence, and primary online sources. This is a research agenda, not a
production change or a claim that a new feature creates betting profit.

## Executive finding

The first priority is the **data and replay contract**, then a calibrated
joint score model. A more elaborate predictor built on silently stale or
missing EPA/QB files will look sophisticated while producing weak calls. The
Oct 26 Philadelphia–Dallas contradiction was the visible symptom of three
heads priced separately; it was not the whole accuracy problem.

## What the repo actually serves

| Market | Current registered model | Actual calculation | Main weakness |
| --- | --- | --- | --- |
| Moneyline | `nfl-elo-trend-lr-v4` | Logistic model of Elo home-win probability and `trend_gap` | Omits current QB/availability and market state; independent of spread |
| Spread | `nfl-spread-structural-v5` | Team EPA, QB adjustment, rest, weather, fixed dispersion and key-number distribution | Missing/stale efficiency can silently become zero; many arithmetic constants are code-owned |
| Total | `nfl-total-structural-v5` | Same team/QB source, rest and weather, separate total distribution | No fitted possession/pace state; fixed weather steps and dispersion |

The current registrations are in `config/production.yaml:699-702`. Source is
`src/model_prediction/cli/forecast.py` and
`src/model_prediction/models/nfl_structural_v5.py`.

The structural engine has fixed code values for home advantage (1.8 points),
rest (0.175 points per day), short week (1 point), weather cutoffs (14 and
20 mph), EPA-to-points (32), and key margins/totals. The artifact supplies
spread and total standard deviations, but fields such as `key_margins`,
`home_field_advantage_points`, and `weather_wind_penalty_threshold_mph` are
not passed into the engine by the live callers. An artifact hash therefore
does **not** prove those displayed fields drive the forecast. The scalar
replay checks the saved mean/SD/line, not the upstream reconstruction of EPA,
QB, rest, or weather that produced the mean. Treat claimed qualification of
those transformations as unverified until exact source-to-inference replay.

### Current data observations (read-only)

- The repo's `data/processed/nfl/team_weekly_efficiency.parquet` (3,006 rows)
  and `qb_weekly_efficiency.parquet` (3,605 rows) both end on **2026-10-12**;
  their modification time is Oct 14. `games.jsonl` includes completed games
  through the Oct 26 Philadelphia–Dallas game. No repo Python/shell producer
  referencing either efficiency output name was found beyond the reader.
- `NFLEfficiencyStore` returns league-average zero EPA and zero QB adjustment
  if files or records are absent. The external runtime root has no copies at
  `processed/nfl/`. Determine the **actual scheduled forecast data root** before
  saying how many live calls used stale versus zero inputs. This is an
  operational-source ambiguity, not yet a measured count of affected calls.
- The forecast uses the last *historical game's* listed starter as its QB
  identity, then applies a penalty if that name appears in the injury feed.
  It does not establish the upcoming game's starter independently. A healthy
  roster change, benching, or backup starting is a plausible failure case.
- `_nfl_situational_features` derives last-game dates from UTC `.date()`;
  callers derive the target game date the same way. Late ET games can cross
  the UTC date boundary. Rest should use one explicit game-date timezone.
- `games.jsonl` has 13 duplicate 2026 event IDs and mixed `regular` /
  `regular-season` labels. `FeatureStore.load_games` deduplicates by first
  event ID, so Elo does **not** count duplicate games twice. The inconsistent
  timestamp/label vintages still need canonicalization and lineage audit.
  For example, event `401872963` appears with both `2026-09-28T20:15Z` and
  `2026-09-29T00:15Z` starts for the same 27–7 score; the first version wins.
- In the selected 2026 Flat calls, saved structural residuals average
  **−4.24 home-margin points** across 41 spread calls and **−0.11 total
  points** across 34 total calls; this is diagnostic, selected data, not
  an unbiased league-wide estimate. The fixed spread SD is 14.5 and total
  SD 14.2 today; neither is shown to match conditional residual variance on
  an untouched cohort.

The existing 934-game `data/processed/nfl/market_relative_validation.json`
shows negative diagnostic ROI for all current heads, but its nflverse-derived
retrospective efficiency and quote vintages do not prove point-in-time
availability. Do not use that report to promote a coefficient or to infer an
executable ROI. The 15-game
[`nfl-joint-market-v2` replay](NFL_JOINT_MARKET_V2_2026-10-27.md)
improved Brier on all three selected markets, but the total gain was only
0.00057 and the market anchor may merely copy public information.

The separate quote-backed walk-forward ablation reinforces the distinction:
the saved moneyline fair price beat the incumbent on 28 later calls (Brier
0.25365 vs 0.26008), and the spread ask-implied probability beat it on 16
(0.25125 vs 0.27665); the total ask-implied probability **lost** to the
incumbent on 16 (0.25155 vs 0.24759). These are selected small samples and
spread/total asks include market margin. Copying the market across all three
heads is not the solution.

## What external research changes in our plan

1. **Capture real vintages instead of trusting a season/week column.**
   nflverse says its cleaned play-by-play and player stats update after games,
   schedules update frequently, and data can be revised for stat corrections.
   Participation data from 2023 onward is delivered only after the season.
   Therefore today's bulk download is unsuitable as a historical pregame
   roster/participation snapshot. Store each observed raw release with capture
   time and SHA-256; train only from what was already available at a forecast
   timestamp. [nflverse availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

2. **Use confirmed QB availability and measure its incremental value.** The
   NFL's practice and game-status reporting calendar provides timed status
   updates; inactive lists are available around the 90-minute pregame
   meeting. nflWAR demonstrates a reproducible play-value framework with
   uncertainty, and NFL Next Gen Stats' passing score separates EPA from
   CPOE and contextual passing quality. These support a QB-state *candidate*;
   they do not prove it beats prices. Capture report/inactive vintages,
   resolve player identity, estimate QB replacement effects on prior seasons,
   and compare with the live last-starter heuristic without double-counting
   QB contribution already embedded in team EPA.
   [NFL reporting calendar](https://operations.nfl.com/calendar-events/nfl-important-dates),
   [pregame inactive timing](https://operations.nfl.com/gameday/pre-game/countdown-to-kickoff),
   [nflWAR paper](https://arxiv.org/abs/1802.00998),
   [NFL Passing Score methodology](https://www.nfl.com/_amp/next-gen-stats-intro-to-passing-score-metric).

3. **Keep a true market baseline and a separate residual model.** nflfastR
   explicitly maintains a win-probability version with the pregame spread and
   describes it as better than its unadjusted version. That is evidence that
   price is a strong *forecast input*, not evidence of a tradable edge. Compare
   each independent head against exact contemporaneous two-sided no-vig
   moneyline/spread/total prices. A price-aware layer should predict only the
   residual and report improvements relative to the price; evaluate trades
   against executable asks, not fair probabilities.
   [nflfastR model documentation](https://github.com/nflverse/nflfastR/blob/master/vignettes/nflfastR.Rmd),
   [nflfastR market-aware model](https://github.com/nflverse/fastrmodels).

4. **Fit one conditional score distribution, especially near 3 and 7.**
   Moneyline, spread and total are functions of the same final scores. The
   current separately fitted heads can assign more than 100% combined
   probability to mutually exclusive outcomes. Joint integer scores solve
   the logical defect; the next step is to fit the conditional margin and
   total distributions, key-number masses, tie/overtime component, and their
   covariance on training dates rather than using fixed code constants.
   Score model changes must beat each incumbent and the market on held-out
   Brier/log loss and margin/total distribution scores. The league changed
   kickoff and regular-season overtime rules in 2025, so test regime effects
   before pooling eras.
   [2026 NFL rulebook](https://operations.nfl.com/rules-officiating/2026-nfl-rulebook),
   [2025 overtime change](https://operations.nfl.com/the-rules/nfl-rulebook).

5. **Build the total from possessions and actual pregame weather forecasts.**
   Prior-play efficiency, drive count/pace, field position and scoring-event
   probabilities offer a testable route to a total mean and variance. nflfastR
   exposes play-by-play and expected-points models. Replace the current 2/4.5
   point wind steps with a fitted, possibly nonlinear weather response; use
   archived *forecast runs* at the decision time, not realized game weather
   or modern reanalysis as if it were known before kickoff. Open-Meteo's
   Single Runs API explicitly preserves issue times; the NWS API provides
   hourly forecasts for live capture. Test wind, precipitation, roof and
   their interactions by feature-group ablation before serving.
   [nflfastR project](https://github.com/nflverse/nflfastR),
   [Open-Meteo forecast-run archive](https://open-meteo.com/en/docs/historical-forecast-api),
   [NWS hourly API](https://www.weather.gov/documentation/services-web-api).

6. **Do not prioritize tracking data yet.** NFL tracking is rich and useful,
   but public participation data arrives too late for a current-season
   pregame backtest; licensing and capture coverage also need review. Begin
   with release-vintaged PBP, team/QB, injury, quote, and weather data.
   [NFL Next Gen Stats](https://operations.nfl.com/gameday/technology/nfl-next-gen-stats),
   [nflverse availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

## Ranked change program

| Priority | Concrete change | First falsifiable test | Failure/rollback |
| --- | --- | --- | --- |
| P0 | Resolve scheduled data root; add freshness, source hash and missingness to each NFL decision; refresh team/QB inputs after games; keep Flat diagnostic visibility | Reproduce Oct 26 input packets from source vintages and prove each metric was known before call | If unavailable, mark the feature unknown and withhold qualified Main; retain incumbent Flat record |
| P0 | Normalize ET game dates and exact event/QB identity; audit first-vintage dedup | Recompute rest and team history across late games, including SNF/MNF; no future result enters input | Preserve v4/v5 and raw rows for rollback |
| P1 | Estimate price-relative QB/team-strength residuals using release-vintaged PBP and official availability | Date-forward ablation vs Elo, market no-vig, and current v5 on identical events | Reject features with no paired gain or uncertain issue time |
| P1 | Train calibrated joint score distribution with data-fitted SD, covariance, key numbers, ties and regime segmentation | Simultaneous held-out moneyline/spread/total proper scores, exact-line push behavior and variance coverage | Keep joint v1/v2 and incumbent artifacts side by side |
| P2 | Drive/pace + weather forecast-run model for totals | Total CRPS, Brier by line, dome/weather strata, and ask-relative EV vs market | Drop any group that fails date-forward ablation |
| P3 | Tracking/O-line pressure features | Only after lawful, pregame-vintaged source capture and P1 success | No current serving dependence |

## Evaluation contract

- Freeze a complete-date train/selection/untouched evaluation split before
  trying features. Previously inspected historical dates are development
  evidence; only new prospective calls can confirm an upgrade now.
- Compare all models on the **same event, decision timestamp, market line and
  side**. Keep non-calls and missing quotes visible to avoid selection bias.
- Report Brier and log loss for each binary market, calibration by probability
  band, score-margin/total CRPS and interval coverage, key-number mass,
  market-relative score, closing-line value and realized executable units.
  Brier/log loss are proper probability scores; ROI is a separate decision
  metric. [Gneiting and Raftery](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf),
  [Walsh and Joshi](https://arxiv.org/abs/2303.06021).
- Analyze early vs late season, current QB vs changed QB, dome vs outdoor,
  spread key lines, quote age and NFL rule era. Cluster uncertainty by game
  date, because three markets on one game are correlated.
- Promote only if the exact versioned artifact, producer hashes, serving
  transforms, quote contract and prospective metrics pass the existing gate.
  Preserve the original champions and Flat CALL policy throughout research.
