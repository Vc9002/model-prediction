# NFL v2/v3 Joint-Market Iteration (2026-10-27 to 2026-10-29)

> Consolidated from: NFL_JOINT_SCORE_V1_2026-10-27.md, NFL_JOINT_MARKET_V2_2026-10-27.md, NFL_REPAIR_2026-10-27.md, NFL_MODEL_RESEARCH_2026-10-27.md, NFL_JOINT_MARKET_V3_2026-10-29.md, NFL_SPREAD_BIAS_EXPERIMENT_2026-10-29.md, NFL_SPREAD_V6_CODE_AUDIT_2026-10-29.md, NFL_FIVE_WEEK_LOSS_REVIEW_2026-10-29.md. Archival merge, ordered by date then topic — all content preserved.

---

## Part 1 — Joint Score Model v1 (source: NFL_JOINT_SCORE_V1_2026-10-27.md)

# NFL joint score v1 research challenger (2026-10-27)

## Problem and change

The incumbent learned NFL moneyline and structural spread/total heads were
priced independently. On the saved Philadelphia–Dallas packet, Philadelphia
moneyline was 52.4444% while Dallas −4.5 was 50.7757%. Those outcomes cannot
both occur, so the sum above 100% was an internal pricing contradiction.

`nfl-joint-score-v1` is a new research version in
`src/model_prediction/models/nfl_joint_score_v1.py`. It derives all three
market probabilities from a single distribution over feasible integer final
scores. The incumbent learned moneyline probability fixes the win marginal;
the existing structural margin and total distributions supply conditional
score shapes. It adds no trained weights and does not change incumbent artifacts,
production registration, or Flat call status.

For the saved pregame Eagles–Cowboys inputs, the joint version gives:

| Market | Incumbent | Joint v1 |
| --- | ---: | ---: |
| Philadelphia moneyline | 52.4444% | 52.4444% |
| Dallas −4.5 | 50.7757% | 37.0566% |
| Over 43.5 | 70.8592% | 71.3730% |

The Dallas spread change is a model probability change, not merely a routing
or display override. The actual final was Dallas 38, Philadelphia 21; this
one game cannot establish which spread probability was better calibrated.

## Retrospective replay

Command:

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_joint_score_v1.py E:\model-prediction-runtime\ledgers\ledgers.db
```

The evaluator reads only settled Flat NFL records, takes the latest saved call
per event and market, requires all three markets, checks that input snapshots
predate kickoff, and verifies replay hashes and incumbent probabilities. On
16 complete verifiable game packets:

| Market | Incumbent Brier | Joint v1 Brier | Mean absolute probability change |
| --- | ---: | ---: | ---: |
| Moneyline | 0.244405 | 0.244405 | 0.000000 |
| Spread | 0.289437 | 0.278788 | 0.076329 |
| Total | 0.228664 | 0.227859 | 0.011051 |

Twenty-two events lacked a complete settled three-market packet. Eighteen
complete packets failed the saved scalar engine source hash check and were
excluded rather than silently replayed with today's source. The cohort is
small and selected from called picks; the score difference is development
evidence, not proof of generalization or profitability. Saved snapshots also
mark historical source observation times as unverified; the replay checks
snapshot timing, not every upstream feature's point-in-time provenance.

## Decision and next gate

Keep v1 as a research challenger. It fixes the logical inconsistency by
construction and improves spread Brier on the verifiable retrospective subset.
It does **not** improve moneyline Brier, and the total improvement is tiny.
Do not promote it as a three-head accuracy upgrade or change live serving on
this evidence. Collect fresh prospective packets with verified source timing,
run the same per-market calibration and profitability comparison, and require
the production promotion gate before replacing the incumbent. Existing Flat
CALL records remain CALL records, with any conflict explicitly annotated.

---

## Part 2 — Joint Market Model v2 (source: NFL_JOINT_MARKET_V2_2026-10-27.md)

# NFL joint market v2 research result — 2026-10-27

## What changed

`nfl-joint-market-v2` is a **price-aware research challenger**, not a new
independent source of betting edge. It starts with the existing learned
moneyline probability, blends toward the exact saved two-sided no-vig
moneyline quote, and derives moneyline, spread, and total from one feasible
integer score distribution. The blend weight is the Brier-optimal value fitted
on earlier complete game dates whose outcomes were settled before the call.
No weight or confidence threshold was chosen from the current game.

The versioned hash-verified research artifact is
`config/models/challengers/nfl-joint-market-v2.json`; incumbent production
artifacts and Flat calls are unchanged. The previous `nfl-joint-score-v1`
challenger anchored on the incumbent moneyline, so it repaired coherence but
did not improve moneyline accuracy. This v2 tests an actual change to that
probability.

## Reproducible comparison

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_joint_market_v2.py E:\model-prediction-runtime\ledgers\ledgers.db
```

The evaluator reads canonical settled NFL Flat records without mutating them.
It requires all three same-event market packets, verifies parent artifact and
replay hashes, checks each input and quote was available by the decision time,
and excludes unavailable or later-settled training outcomes. It compares the
original selected side on each exact historical line; it does not invent a
new contract or retrospective bet.

| Market | Calls | Incumbent Brier | V2 Brier | Incumbent log loss | V2 log loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| Moneyline | 15 | 0.24104 | 0.22354 | 0.67555 | 0.64451 |
| Spread | 15 | 0.29610 | 0.25351 | 0.78717 | 0.69996 |
| Total | 15 | 0.23698 | 0.23641 | 0.66633 | 0.66520 |

For Philadelphia–Dallas, the as-of weight was 1.0 and the saved no-vig market
probability was 32.9177% Philadelphia. V2 returned **32.9177% Philadelphia
moneyline, 52.3135% Dallas −4.5, and 71.5271% over 43.5**. Dallas won 38–21.
These probabilities are coherent. They do not imply that buying Dallas at its
available ask would have had positive expected value.

The single-head market-anchor ablation is in
`scripts/evaluate_nfl_market_anchor_v1.py`. On its own it improved moneyline
and spread but worsened total Brier from 0.24759 to 0.26876 on 16 later
quote-backed total calls. V2 therefore uses the moneyline quote as a score
distribution anchor, not the one-sided total ask as a direct target.

## Limitations and decision

The comparison has only 15 games, all selected from existing called picks. The
total gain is 0.00057 Brier, too small to establish a real improvement.
Twenty-two events lacked a complete triad; 12 complete events had parent
artifact hashes that did not match this challenger; five lacked previously
settled training history; two lacked a valid moneyline quote. The saved
structural feature snapshots explicitly do not verify every upstream
historical source observation time. This is date-walk-forward retrospective
development, not a fresh prospective holdout or production qualification.

**Decision:** keep V2 as a challenger. It delivers an observable probability
change and improves all three metrics on this verifiable subset, but do not
promote it, rewrite settled calls, or claim improved profitability. The next
evidence gate is prospective capture of exact decision-time quotes and source
features across a larger unselected slate, followed by per-market calibration,
market-relative accuracy, and executable EV review under the existing
production promotion rules.

---

## Part 3 — NFL Repair (source: NFL_REPAIR_2026-10-27.md)

# NFL input repair and v6 research routing — 2026-10-27

This change repairs the current NFL input path and starts a new spread/total
research generation. It does **not** establish improved prediction accuracy or
profitability. The incumbent v4 moneyline and v5 spread/total artifacts remain
available for rollback; the new v6 artifacts explicitly say `qualified: false`.

## Theory, evidence, test, failure, rollback

- **Theory:** NFL structural calls were exposed to old or missing EPA/QB rows,
  an unverified injury file, UTC-based rest dates, and a last-game QB heuristic.
  Captured release vintages and a depth-chart QB1 projection make these inputs
  traceable and prevent qualification when they are absent.
- **Evidence:** legacy efficiency Parquets stopped at Oct 12 while completed
  NFL games reached Oct 26. The Oct 27 nflverse capture has
  214 team-game rows and 242 QB-game rows through Oct 26 plus 32 projected
  QB1 entries. The depth chart is a **projection**, not an official game-day
  starter confirmation.
- **Test:** `tests/test_nfl_efficiency_snapshot.py` checks exclusion of future
  results and future depth-chart rows, source hash tampering, last-game
  coverage, and ET date conversion. The seven focused `test_cli.py -k nfl`
  caller tests pass after the ledger mock was brought up to its current API.
- **Failure mode:** nflverse or ESPN can fail or lag, and the projected QB1 can
  differ from the actual starter. The capture leaves an explicit error status;
  no v6 contract is qualified for Main. Flat remains a research diagnostic.
- **Rollback:** v5 artifacts are untouched. Revert the v6 forecast routing and
  daily capture changes; immutable source captures remain available for audit.

## Implementation

`capture_nfl_efficiency` downloads current-season nflverse weekly player
stats, schedules, and dated depth charts before each daily forecast. It keeps
only score-final games before the capture date, derives the exact team/QB
columns consumed by the structural engine, archives raw releases and derived
Parquets, and hashes every file and its manifest. The reader chooses only a
hash-valid snapshot observed by the decision time; legacy Parquets are
diagnostic fallback only. This timing rule follows the source's update and
revision schedule: [nflverse data availability](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
The live downloader rejects caller-supplied past observation timestamps so a
modern release cannot be mislabelled as a historical vintage.

Spread/total v6 uses the latest captured QB1 projection, rather than the
last historical starter, and computes rest from Eastern game dates. It embeds
source metadata in the model input snapshot. Per-contract input qualification
requires verified efficiency coverage of both teams' latest games, projected
  QB names from same-day depth-chart updates, and a same-day hash-valid injury snapshot with neither projected
starter listed Out/IR. The v6 artifact itself is research-only, so these
checks cannot be mistaken for a predictive-performance promotion.

## Remaining research gates

1. **Moneyline and joint scores:** `nfl-joint-market-v2` remains a separate
   challenger. Its 15 selected quote-backed games are insufficient to promote
   moneyline, spread, or total. Capture full unselected prospective slates and
   compare all three heads with contemporaneous no-vig prices and incumbent
   calls on identical event-line-time pairs.
2. **QB availability:** depth-chart QB1 is provisional. Add timed official
   game-status and inactive feeds with player IDs, then fit a replacement
   effect with an explicit team-EPA overlap ablation.
3. **Structural coefficients:** v6 still uses the v5 engine's code-owned home
   advantage, rest, EPA-to-points, key-number, and weather values. Train a
   versioned joint distribution from a date-forward training split and have
   each artifact field drive serving arithmetic. Neither v6 artifact claims
   that this has been done.
4. **Totals and weather:** capture decision-time forecast runs and fit
   pace/possession and weather responses before changing live total pricing.
5. **Promotion:** keep the new versions in Flat research until a prospective
   unselected cohort establishes paired per-market calibration and
   market-relative improvement, followed by executable EV/P&L review.

---

## Part 4 — NFL Model Research (source: NFL_MODEL_RESEARCH_2026-10-27.md)

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

---

## Part 5 — Joint Market Model v3 (source: NFL_JOINT_MARKET_V3_2026-10-29.md)

# NFL joint market v3 research challenger — 2026-10-29

## Theory and evidence

`nfl-joint-market-v2` repaired cross-market coherence and improved proper scores
on 15 selected historical games, but its spread and total parents were v5.
The new v6 spread/total snapshots have observed-time input manifests and
projected-QB/injury checks. V3 binds the existing joint-score and dated
market-anchor calculation to those v6 artifact hashes, requires a complete
three-market decision packet, and records exact executable asks. It is a
**research challenger**, not an established improvement in accuracy or P&L.

The prior spread-mean intercept experiment failed (Brier 0.3004 to 0.3559 on
15 date-forward calls). V3 does not incorporate that correction. Its
moneyline market weight is fitted from earlier complete dates whose outcomes
had settled by the packet timestamp. No current-game result or postgame
price enters the forecast.

## Frozen pregame shadow forecast

The first packet was captured at **2026-10-29 20:18:29 UTC**, before Carolina
at Green Bay's scheduled 2026-10-30 00:15 UTC start. All three model snapshots
and two-sided quote records passed hash, timestamp, and exact-line checks.
The shadow output seals 38 prior settled moneyline training observations,
their fitted weight, parent/source hashes, contract identities, and its own
content hash. Both the packet and forecast are preserved in the repo for later scoring:

- Packet: `outputs/analysis/nfl_joint_market_v3_packets/401873023-98c82fbeec3c6e4ab8c7e7392fe87b39875c567d17ce9f4fbd09e2a8a88eeec8.json`
- Forecast: `outputs/analysis/nfl_joint_market_v3_20261029_401873023_committed.json`
- Versioned artifact: `config/models/challengers/nfl-joint-market-v3.json`

| Exact saved contract | V6 or v4 parent | V3 probability | Selected ask | Raw probability minus ask |
| --- | ---: | ---: | ---: | ---: |
| Green Bay moneyline | 52.51% | 64.09% | 64.25% | -0.16 pp |
| Carolina +3.5 | 52.44% | 48.31% | 50.00% | -1.69 pp |
| Over 47.5 | 52.32% | 52.94% | 48.50% | +4.44 pp |

The as-of market weight was **1.0**, so v3's moneyline probability equals the
saved two-sided no-vig moneyline price. That is no independent moneyline edge.
The total's positive raw gap is **not** an order recommendation: no candidate
selection, stake, fee, or correlation policy has been qualified.

## Settled-game backtest status

The settled NFL Flat ledger currently has 56 games, including 41 v5 spread
calls and 34 v5 total calls. **Zero settled games have v6 spread and total
parent packets**, so an exact v3 backtest has `n = 0`. Replacing old v5 inputs
with today's v6 source capture would leak future data; substituting v5 parents
would no longer evaluate the hash-pinned v3 artifact.

The valid historical analogue is v2, rerun on the same ledger: 15 settled
three-market packets, with moneyline Brier 0.24104 to 0.22354, spread 0.29610
to 0.25351, and total 0.23698 to 0.23641. These are **v2** results on selected
v5-era calls, not evidence that v3 improved accuracy or profitability.

## Reproduction and next evidence gate

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/forecast_nfl_joint_market_v3.py <pregame-packet.json> E:\model-prediction-runtime\ledgers\ledgers.db
.venv\Scripts\python scripts/evaluate_nfl_joint_market_v3.py outputs/analysis/nfl_joint_market_v3_20261029_401873023_committed.json data/processed/nfl/games.jsonl
```

The scorer currently returns `pending`. After a completed scoreboard record
with matching event, teams, start, and score exists, it will compare v3,
the parent model, and the exact two-sided no-vig market on each saved line.
Pushes are excluded from binary proper scores. Do not infer P&L from those
scores; a prespecified executable bet policy and sufficient unselected
prospective games are separate requirements.

Failure modes: v6's projected QB may differ from the actual starter; upstream
historical feature observation times are not fully certified; NFL market
quotes and total/spread score distributions may move after the decision;
38 selected moneyline training calls are a small calibration basis. The model
fails closed on missing or tampered packet, quote archive, parent artifact,
source code, unqualified v6 input, future snapshot, or unavailable prior
settlement. The v2 and v6 artifacts remain rollback versions. No champion,
ledger, or order path was changed.

---

## Part 6 — Spread Bias Experiment (source: NFL_SPREAD_BIAS_EXPERIMENT_2026-10-29.md)

# NFL spread bias correction experiment — 2026-10-29

## Hypothesis and test

The five-week selected Flat cohort showed a -4.24 point average error in home
margin. This experiment tests whether adding a home-margin intercept fitted on
earlier settled complete game dates improves later spread probabilities on the
same exact saved lines. It uses the saved parent artifact and source-engine
hashes and requires exact incumbent probability replay, event/line orientation,
decision timing, and saved score/result consistency. Training outcomes must
settle before the later decision. No threshold, stake, champion, or ledger is
changed.

Run:

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_spread_bias_v1.py E:\model-prediction-runtime\ledgers\ledgers.db
```

## Result

Only 16 of 41 settled spread calls have a replayable current engine hash;
25 older calls fail source-engine hash verification. The first replayable date
has no same-artifact settled training history. Thus 15 calls across four later
game dates are scored.

| Exact paired cohort | Incumbent Brier | Bias-corrected Brier | Gain (positive is better) |
| --- | ---: | ---: | ---: |
| 15 date-forward calls | 0.30037 | 0.35590 | **-0.05552** |
| 14 with saved quote archive present | 0.29583 | 0.34125 | **-0.04542** |

The raw selected-side ask-implied Brier on the 14 quote-backed calls is
0.25288. This is a price reference with margin, not a two-sided no-vig market
probability or executable expected-value result. Date-cluster bootstrap 95%
interval for incumbent-minus-corrected Brier is **[-0.12232, +0.04646]**.
There are too few dates for a stable performance claim. The correction moves
in the wrong direction on the observed paired sample.

## Decision and next test

**Reject this simple bias-intercept challenger.** Do not adjust the serving
spread mean or confidence gate from the five-week selected-call average. The
older artifact's mean residuals are not interchangeable with a current-engine
walk-forward training set. On future unselected full-slate packets, evaluate
margin residuals by game date, team side, QB state, and quote age, then test a
price-relative residual model against exact two-sided no-vig spread prices and
the existing head. Freeze that candidate before scoring prospective games.

The rollback is the unchanged v5/v6 spread artifacts and serving calculation.
No retrospective P&L is attributed to this diagnostic probability adjustment.

---

## Part 7 — Spread v6 Code Audit (source: NFL_SPREAD_V6_CODE_AUDIT_2026-10-29.md)

# NFL spread v6 code audit and feature plan — 2026-10-29

## Scope and result

Traced the serving path from captured nflverse releases and injury snapshots through the v6 spread forecast, archived spread quote selection, scalar replay snapshot, and ledger request. This is a source-block audit, including every numerical transform and decision boundary in the v6 path; it is not a claim that every unrelated line in `forecast.py` was executed. The v6 artifact remains research-only (`qualified: false`), and there are no settled v6 spread calls to score yet. Five-week v5-era Flat spread calls lost 7.4448 paper units on 41 selected contracts; that is evidence of a problem to investigate, not a v6 score.

## Execution trace and findings

| Source lines | Calculation or boundary | Finding |
| --- | --- | --- |
| `nfl_efficiency_snapshot.py:27-74` | Join completed schedules to weekly player stats; aggregate team EPA and QB rows | Capture excludes games with absent scores and current local game date. Captures preserve observed time and raw hashes. Historical bulk files cannot stand in for older decision-time releases. |
| `nfl_efficiency_snapshot.py:77-160` | Select latest QB1 depth row, archive releases and derived parquets | Projected QB is depth-chart QB1, not a confirmed game starter. The manifest holds the depth entry timestamp and capture timestamp; both need coverage checks. |
| `nfl_efficiency_snapshot.py:166-211` | Load latest predecision hash-valid snapshot | Rejects future and changed source files. |
| `nfl_efficiency.py:167-195` | Snapshot load, fallback, last-game coverage | Missing valid snapshot falls back to local parquets and league-average inputs; `input_qualified` later becomes false. Research outputs can still carry numerically plausible but weak projections. |
| `nfl_efficiency.py:197-282` | Six-game offensive and defensive EPA with 120-play prior | Date filter is strict. Defensive EPA is opponent offense against the team. The six-game lookback and 120-play prior are code constants, not fitted v6 artifact fields. |
| `nfl_efficiency.py:284-323` | QB EPA/CPOE shrinkage and point adjustment | The QB adjustment is added to team EPA that already contains the same QB's plays. This creates potential double counting; size and sign need a walk-forward ablation, so no coefficient was changed. Unknown QB and parser failures return zero; low-sample QB returns -1.5 points. |
| `forecast.py:2635-2670` | Choose a balanced full-game spread line | **Verified defect fixed:** prior code compared every archived quote and could choose an old observation of the same contract. It also accepted a missing short ask, which could fail when that side was selected. It now keeps the latest valid two-sided quote per slug before ranking lines. At the 2026-10-29 20:18:29 UTC replay time, the chosen CAR–GB +3.5 quote moved from 12:49:11 to 19:54:26 UTC; both asks were identical (0.500/0.505), so no numerical edge changed in that example. |
| `forecast.py:2806-2907` | Load hash-checked v6 artifact, game history, scoreboard, predecision quotes, select exact team and start | The v6 artifact supplies `margin_sd=14.5`; v6 runs the v5 engine with new vintaged inputs. The selected line is converted to a home line based on anchor team. No result data enter the forecast. |
| `forecast.py:2909-2968` | Neutral venue, weather, rest, projected QB, injuries, efficiency | Rest uses Eastern local game dates. Input qualification checks last-game coverage, two projected QBs, same-UTC-date updates, and injury snapshot, but the artifact itself is research-only. Weather uses ESPN event weather or fixed defaults, with no separate forecast-run vintage recorded in the model snapshot. Division flag is captured but has no numerical consumer. |
| `nfl_structural_v5.py:49-134` | Normal CDF, key-number mass, mean-conserving tilt, spread cover/push | Distribution is discrete over margins -55..55. Key masses, boosts and tilt settings are hardcoded. Mean conservation is approximate if the tilt hits its iteration limit or support edge; ordinary NFL means are within support. |
| `nfl_structural_v5.py:230-290` | EPA-to-points, QB points, rest, short-week, weather, home advantage, cover probability | EPA is multiplied by 32; rest by 0.175/day; home advantage is 1.8; wind/temperature use hard thresholds. Those are code-owned, not v6 artifact-fitted. Spread cover sides are normalized conditional on no push; therefore `1 - cover_prob` in the caller is arithmetically correct. |
| `forecast.py:2970-3078` | Pick higher-probability side, attach exact ask, snapshot mean/SD/line | Long and short sides use the matching anchor line sign. The scalar snapshot proves probability replay given the saved mean, SD and line; it does **not** independently reproduce upstream EPA, QB, injury or weather values. `model_uncertainty=0.08` is fixed and not empirically calibrated here. |
| `forecast.py:3103-3233` | Build PickRequest, validate, gate, append Flat/Main | V6 `qualified: false` is downgraded from serving eligibility. Flat is a diagnostic ledger, not an executed order. No promotion or order was made in this audit. |

## Falsifiable feature priorities

1. **QB state and substitution, first.** Capture projected starter, official game status and inactives with timestamps, then estimate a QB *change relative to the team EPA window* rather than adding full QB quality again. Compare against v6 and contemporaneous two-sided no-vig spread prices by complete game date. The NFL's pregame process reviews inactives at the 90-minute meeting; nflverse depth charts update daily and are projections, not confirmation. [NFL pregame process](https://operations.nfl.com/gameday/pre-game/countdown-to-kickoff); [nflverse release schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
2. **Opponent-adjusted, decayed efficiency.** Replace raw six-game EPA with an opponent-adjusted pass/rush split, recency decay, and uncertainty based on prior play count. Fit weights only on training dates. Ablate each part against the current six-game shrinkage and the market. nflverse player/team statistics arrive after games and can change, so use captured releases. [nflverse release schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
3. **Conditional margin distribution.** Estimate residual SD and key-number mass by spread size, projected total, and era; score exact half-point and integer lines, including pushes. Compare Brier, log loss, and proper distribution scores on later complete dates. Today's fixed 14.5 SD and hand-set key masses are an unvalidated confidence model, especially around 3 and 7.
4. **Decision-time market residual.** Freeze a separate layer that learns the spread residual relative to exact two-sided no-vig contemporaneous prices. Report independent-model accuracy and market-relative accuracy separately. The market is a strong baseline, not proof of profitable bets; nflfastR itself provides win probability models with and without pregame spread. [nflfastR project](https://github.com/nflverse/nflfastR).
5. **Weather and trench availability, after timestamp coverage.** Archive forecast *runs* (issue time plus retrieval time), roof state, wind direction, precipitation, and offensive-line/edge-rusher status; test interactions with pass-heavy offenses. Do not substitute realized weather or postseason participation data for pregame observations. [Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api); [nflverse release schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

Avoid a home-margin intercept from the selected losses: a prior date-forward test worsened spread Brier from 0.30037 to 0.35590 on 15 replayable calls (`docs/NFL_SPREAD_BIAS_EXPERIMENT_2026-10-29.md`). Also avoid fitting new weights from the current 41 selected calls; v6 needs complete-slate, predecision feature packets and later results. The 2026-10-29 CAR–GB packet is the first prospective v6 observation.

## Verification and rollback

Theory: quote selection should use the latest two-sided observation of each exact contract. Evidence: 16 vintages existed for the selected CAR–GB contract at the saved cutoff; old selector returned the early one. Test: focused selector tests cover newer-vs-older observations and missing short asks; live archive replay now returns the 19:54 UTC quote. Failure mode: a missing or malformed two-sided quote now makes that contract ineligible and may result in ESPN fallback. Rollback: revert only the NFL selector edit; the v5/v6 artifacts and model engine are unchanged.

`ruff check src/model_prediction/cli/forecast.py tests/test_cli.py` passed. `pytest tests/test_cli.py -o addopts='' -q -k select_nfl_spread_market` passed (3 tests). `pytest tests/test_nfl_efficiency_snapshot.py -o addopts='' -q` passed (4 tests). No broad suite was run.

---

## Part 8 — Five Week Loss Review (source: NFL_FIVE_WEEK_LOSS_REVIEW_2026-10-29.md)

# NFL five-week loss review and prospective replay capture — 2026-10-29

## Cohort and arithmetic

Read-only audit of canonical settled NFL Flat calls in
`E:/model-prediction-runtime/ledgers/ledgers.db`, covering the five event weeks
beginning September 28 through October 26, 2026. There are 56 unique games,
131 selected contracts, 166.59 staked units, and **-11.2472 units (-6.75% ROI)**.
The final week currently has only one settled game. Flat is a paper comparison
ledger; these are recorded model-call units, not exchange fills or net cash.

| Market | Calls | Wins | Staked | P&L | ROI | Brier on selected sides |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Moneyline | 56 | 31 | 63.50u | -1.8791u | -2.96% | 0.2386 |
| Spread | 41 | 20 | 56.12u | -7.4448u | -13.27% | 0.2891 |
| Total | 34 | 16 | 46.97u | -1.9233u | -4.09% | 0.2938 |

The spread is the largest observed loss. For spread calls with model probability
0.55–0.65, 7 of 16 won; for 0.65–0.75, 4 of 9 won; both calls above 0.75
lost. For total calls with probability 0.65–0.75, 4 of 10 won, and the sole
call above 0.75 lost. These selected small buckets suggest overconfidence but
do not establish calibration of the unselected slate or a stable threshold.
Changing confidence gates based on these outcomes would be retrospective
selection.

The saved final-inference means give a complementary diagnostic: across 41
spread calls, actual home margin minus projected home margin averaged **-4.24
points**; error standard deviation was 12.31 versus mean configured spread SD
14.07. Across 34 total calls, mean error was -0.11 points; error SD was 13.13
versus configured total SD 13.83. The spread's selected-cohort mean bias is a
stronger lead than simply inflating its variance. These are not full-slate
estimates, and old upstream feature vintages are not fully certified.

## Why the joint challenger covers only 15 games

Fifteen of 56 games have no saved Flat spread and total contract; seven lack
total. Of the 34 complete triads, 18 carry an older spread/total engine source
SHA-256 that does not match the current or recorded recoverable Git versions.
The v2 evaluator further excludes missing decision-time moneyline quotes and
dates without previously settled training history. Its 15-game probability
comparison and the incumbent's full five-week P&L therefore answer different
questions. The joint challenger has no prespecified bet selection or sizing
policy; the original calls' P&L cannot be attributed to it.

## Testable improvement priorities

1. **Capture all scheduled games:** the new append-only NFL joint packet path
   records moneyline, spread, and total model snapshots and exact two-sided
   archived quotes when available, plus explicit gaps for no-calls and missing
   contracts. It does not rewrite old calls. Keep every source observation time,
   engine/artifact hash, line orientation, and quote archive binding.
2. **Variance and probability calibration:** on subsequent complete dates,
   compare forecast margin and total residual variance with realized variance,
   key-number mass, Brier/log loss, and reliability by probability range. Fit
   any correction on prior dates only and compare the corrected joint model
   with the current heads and decision-time market on paired events.
3. **QB/availability and market residual ablations:** use timed official
   starter/inactive releases and hash-valid efficiency captures. Test their
   paired incremental value over the market and over the current v6 inputs;
   retain an explicit missingness branch. A modern release cannot retroactively
   certify September or early October feature availability.
4. **Economic test:** freeze a challenger selection, stake, and executable-ask
   rule before scoring prospective games. Report skipped contracts, CLV,
   realized paper units, and uncertainty separately from probability scores.

No champion, historical data, existing model artifact, or order path changed.
