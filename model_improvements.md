# Model Improvement and Feature Research Roadmap

This document is the research and promotion contract for the NBA, WNBA, MLB,
and NFL models. Its purpose is not to maximize the number of features. Its
purpose is to add information that remains available point-in-time, survives a
fresh out-of-sample test, improves probability quality, and can create positive
expected value at an executable price after costs.

No feature is production-worthy because it sounds predictive. Every candidate
must have an identical historical and forward definition, explicit observation
timestamps, a versioned source, a missingness policy, and an ablation result.

---

## 1. Current-state audit: fix this before trusting new lift

The current active artifacts are narrow models:

| League | Active v3 moneyline inputs |
|---|---|
| NBA | Elo probability, offensive trend gap, defensive trend gap |
| WNBA | Elo probability, offensive trend gap, defensive trend gap |
| MLB | Elo probability, trend gap, park factor, weather factor, `pitcher_era_gap` |
| NFL | Elo probability, trend gap |

The narrowness is not the main problem. The immediate problem is that some
reported evidence is not yet safe to interpret as forward model lift.

### P0 blockers

1. **MLB training-serving skew.** In historical validation,
   `pitcher_era_gap` is actually the difference in each team's runs allowed over
   its last five games. In forward prediction, the same feature name can contain
   probable-pitcher ERA. Those are different variables and must never share one
   coefficient.
2. **MLB weather mismatch.** Historical training uses a cached historical
   weather database. The live function currently takes the first returned
   forecast hour rather than the game's scheduled hour, and treats wind speed
   without ballpark-relative direction. A model trained on realized or
   near-realized weather cannot be validated as if that were the forecast known
   at decision time.
3. **Static park leakage/regime drift.** A single `2025-three-year` park-factor
   table is applied across earlier seasons. Park geometry, venue, roof behavior,
   humidor use, and league run environments can change. Historical rows need the
   park version that was knowable for that season/date.
4. **The locked holdout is no longer untouched.** Multiple variants and
   thresholds have already been compared on the same dated holdout in several
   `learned-model-validation-v*.json` reports. That cohort is now development
   evidence. It cannot honestly certify the next promoted version.
5. **Profitability is not established.** Flat one-unit `-110` P&L is a
   diagnostic, not moneyline or Polymarket profitability. Current qualification
   does not require positive executable-price EV or venue costs. Hit rate alone
   can be inflated by selecting favorites.
6. **Documentation and artifacts disagree.** README summary figures, active
   artifact qualifications, and the latest validation report do not consistently
   report the same calls, hit rates, or units. Establish one versioned report as
   the source of truth before the next experiment.

### Required response

- Treat the existing MLB park/weather/pitcher result as **research-only** until
  train/serve parity is demonstrated.
- Freeze the previously opened holdout as descriptive evidence. Select features
  with inner chronological folds, then use a new prospective cohort or a newly
  reserved final period exactly once.
- Keep every new output at zero actual units until economic validation passes.

---

## 2. What “better” means

Accuracy and profitability are different objectives. A feature can improve one
and damage the other.

### Predictive gate

A candidate should be compared with the declared incumbent and with a simple
baseline using:

- Brier score and log loss on all forecasts;
- calibration intercept, calibration slope, ECE, and reliability plots;
- discrimination and selectivity only as secondary diagnostics;
- score-distribution accuracy for spreads/totals, not only binary hit rate;
- uncertainty intervals around every delta, not only point estimates;
- coverage and results by missing-feature cohort.

Brier and log loss are proper scoring rules: unlike raw hit rate, they reward
honest probability estimates. Calibration is especially important when a model
is used to estimate expected value and size exposure.

### Economic gate

Economic activation is a separate test. It requires:

- a timestamp-valid executable ask for the chosen side;
- the opposing side's executable quote when calculating a no-vig market
  baseline;
- exact venue fees, spread/slippage assumptions, and order-size constraints;
- expected value at the decision timestamp;
- realized ROI, CLV, maximum drawdown, and bootstrap confidence intervals;
- comparison with the no-vig market forecast on the same events;
- enough independent dates and teams to avoid one streak or one franchise
  driving the result.

No reconstructed postgame price, midpoint-only quote, generic `-110` payout, or
indicative event-list price can pass the economic gate.

### Promotion rule

A feature may enter the independent probability model only if it improves
proper-score performance on a fresh test without damaging calibration or
causing unacceptable coverage loss. A feature may enter a market-aware residual
or decision layer only if it also improves net performance at executable prices.

If a feature improves prediction but lacks economic evidence, its correct status
is `PREDICTIVE_RESEARCH_ONLY`. If it needs data available only shortly before
the event, it should power a late-horizon model or a no-call gate, not be filled
with a neutral value and described as observed.

---

## 3. Point-in-time feature contract

Every source observation should carry at least:

| Field | Meaning |
|---|---|
| `event_id` | Canonical event identifier |
| `entity_id` | Canonical team, player, venue, official, or market identifier |
| `feature_name` | Stable semantic name; never reuse for a different variable |
| `value` | Raw value before model transformation |
| `effective_at_utc` | When the fact became applicable |
| `observed_at_utc` | When this system actually obtained it |
| `source` | Provider and endpoint/report type |
| `source_version` | Dataset, formula, or model version |
| `available` | Whether the value was genuinely observed |
| `missing_reason` | Unknown, not published, stale, source failure, or not applicable |
| `snapshot_hash` | Immutable content hash |

The model input for a decision at time `T` may use only observations with
`observed_at_utc <= T`. Corrections published after `T` remain excluded even if
their `effective_at_utc` is earlier.

### Separate decision horizons

Do not force one model to behave as if all information exists all day. Maintain
distinct, calibrated horizons such as:

- **early:** 24-48 hours before start;
- **mid:** 4-8 hours before start;
- **late:** 15-90 minutes before start, after lineups/inactives when available.

Each horizon needs its own coverage report, calibration, and economic result.
The early model should widen uncertainty or no-call when late information is a
primary driver.

---

## 4. Free, no-signup data-source policy

The default feature stack must not require a new account, API key, paid
subscription, or browser login. Paid or authenticated feeds may be evaluated
later as optional replacements, but no NBA, WNBA, MLB, or NFL model should fail
because a paid credential is absent.

### Source preference order

1. Existing cached repository data and immutable snapshots.
2. Official public league reports or public JSON/CSV endpoints requiring no
   authentication.
3. Public, versioned GitHub release files with clear provenance and licensing.
4. Keyless open-data APIs with documented rate limits.
5. Unofficial public endpoints or scrapers, only with caching, throttling,
   schema tests, and a fail-closed fallback.
6. Paid, keyed, or login-only sources as optional upgrades only.

### Default source stack

| Source | Account/key | Free coverage to use | Main limitation |
|---|---|---|---|
| Existing ESPN public client | None | Schedules, final scores, event IDs, box scores, rosters, and some probable-player/game context | Undocumented and unsupported; schemas can change |
| Polymarket US public gateway | None | Market discovery, order books, BBO, and settlement prices | Public read data only; trading and private portfolio endpoints require authentication |
| Open-Meteo free API | None for non-commercial use | Current forecasts, historical forecasts, and previous model runs for venue weather | Free endpoint is non-commercial, rate-limited, and has no uptime guarantee |
| SportsDataverse GitHub releases | None | NBA/WNBA play-by-play, box scores, shots, rosters, officials, and WNBA Stats-derived tables | Derived pipeline; release lag and upstream schema changes must be monitored |
| Official NBA/WNBA injury-report pages and PDFs | None | Timestamped player participation status and report updates | Prospective archive must be built locally; PDFs and layouts can change |
| Baseball Savant through `pybaseball` or direct CSV | None | MLB pitch-level Statcast, batted-ball quality, pitcher/batter, fielding, and catcher inputs | Scraping endpoint is not a contractual API; cache and throttle it |
| nflverse GitHub releases | None | NFL play-by-play from 1999, EPA/success fields, rosters, depth charts, injuries, officials, and aggregated NGS tables where available | Dataset-specific licences/attribution and in-season release lag |
| Local schedule-derived features | None | Rest, back-to-backs, short weeks, travel distance, time zones, local start time | Requires a versioned venue/coordinate table and correct relocations |

### League assignment

| League | Default no-signup sources |
|---|---|
| NBA | Existing ESPN client + SportsDataverse `hoopR` releases + official NBA injury reports + local schedule/venue features + Polymarket US gateway |
| WNBA | Existing ESPN client + SportsDataverse `wehoop` releases + official WNBA injury reports + local schedule/venue features + Polymarket US gateway |
| MLB | Existing ESPN/MLB StatsAPI path + Baseball Savant via cached `pybaseball` pulls + Open-Meteo + local park/venue table + Polymarket US gateway |
| NFL | Existing ESPN client + nflverse release files + official public injury status where available + Open-Meteo + local venue table + Polymarket US gateway |

### Deliberately excluded from the default build

- SportsDataIO, Sportradar, Stats Perform, PFF, Second Spectrum, and other paid or
  login-only feeds;
- The Odds API for these four leagues; it requires a key and is not needed when
  prospective Polymarket US BBO is the economic evidence source;
- full NBA/WNBA/NFL optical tracking unless a reproducible free export becomes
  available;
- social-media, beat-reporter, or account-gated injury data as deterministic
  features.

If a high-priority feature cannot be built from this stack, mark it
`OPTIONAL_PAID_SOURCE_BLOCKED` or use it only as a qualitative risk flag. Do not
silently replace it with a differently defined free proxy.

### Reliability rules for free sources

- Cache every raw response before transformation and retain its retrieval time.
- Use conditional requests or scheduled bulk downloads instead of repeatedly
  scraping the same endpoint.
- Pin release URLs and file hashes for GitHub-derived historical data.
- Record source schema/version and fail closed when required fields disappear.
- Keep the last valid local snapshot only when policy permits; never label stale
  data as current.
- Respect attribution, rate limits, robots/terms, and non-commercial restrictions.
- Maintain a provider-independent normalized schema so a source can be replaced
  without changing feature semantics.

---

## 5. Shared feature groups across all four leagues

These groups should be built once as reusable infrastructure, while coefficients
and transformations remain league-specific.

| Feature group | Candidate variables | Primary use | Priority |
|---|---|---|---|
| Availability state | projected active probability, confirmed/inactive flag, minutes/snaps/innings at risk, time since last update | Probability and uncertainty | P0 |
| Schedule load | rest days, back-to-back/short week, games in 7/14 days, travel distance, time-zone shift, east/west direction, local body-clock start | Probability and totals | P1 |
| Venue/environment | home/neutral, altitude, surface, roof state, game-time weather forecast | Margin and total | P1 |
| Regime and continuity | season phase, roster continuity, coach/manager change, expansion/relocation, rule era | Priors and uncertainty | P1 |
| Missingness and freshness | feature age, confirmed vs projected, number of key inputs missing, source disagreement | Uncertainty/no-call gate | P0 |
| Market residual | executable no-vig probability, spread/total context, bid-ask width, liquidity, price movement since first snapshot | Separate market-aware layer | P0 for profitability |

The market residual layer must remain separate from the independent sports
model. That preserves an honest answer to two different questions:

1. How likely is the outcome based on sports information?
2. Is the currently executable market price wrong enough to trade after costs?

---

## 6. NBA feature roadmap

The NBA market is fast and efficient. The largest plausible edge is not another
team-level rolling average; it is faster and more accurate player-availability
and minutes information.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Player availability and projected minutes | For every rotation player: probability active, projected minutes, restriction flag, starter/bench role, and replacement minutes; aggregate with a versioned player-impact prior | Converts injuries and rest into expected lineup strength instead of a binary team flag | Official reports update repeatedly; high stale-data risk |
| 2 | Shrunk player/lineup impact | Offensive and defensive RAPM-style player effects with multiseason priors; projected five-man lineup strength and uncertainty | Raw on/off and tiny lineup samples are noisy; regularization is essential | Requires possession/stint reconstruction and entity stability |
| 3 | Possession and efficiency decomposition | Projected pace plus opponent-adjusted eFG%, turnover rate, offensive-rebound rate, and free-throw rate; separate offense and defense | Maps directly to expected possessions and points per possession | Available from box/play-by-play history; must be walk-forward |
| 4 | Shot-profile matchup | Rim, short-mid, long-mid, corner-three and above-break-three frequency/efficiency; opponent allowed profile; transition and half-court rates | Captures stylistic interactions hidden by one net-rating number | Tracking data may be licensed; use public zone data only if stable |
| 5 | Lineup continuity and role change | Returning-minute share, games with current starting five, new-starter indicator, usage redistribution after a key absence | Helps early-season and post-trade adaptation | Requires transaction-effective timestamps |
| 6 | Travel, rest, and altitude | Back-to-back, 3-in-4, 5-in-7, distance, time-zone direction, Denver altitude, local start time | Plausible fatigue/context effect; should affect pace and shooting differently | Evidence is mixed; test, do not hard-code folklore |
| 7 | Referee crew | Crew foul rate, home/away differential, free-throw effect, interaction with drive rate | May affect totals and foul-dependent matchups | Assignment is late and effects are noisy; P3 only |

### NBA model form

Build a joint pregame score model:

`expected possessions x lineup-adjusted home/away points per possession`

Derive margin, total, spread cover probability, and moneyline probability from
one coherent score distribution. Do not train disconnected binary classifiers
that can imply contradictory moneyline, spread, and total forecasts.

### NBA first ablations

1. Elo/trend control.
2. `+ opponent-adjusted Four Factors + pace`.
3. `+ projected minutes x player impact`.
4. Combined basketball model.
5. Combined model plus schedule/travel.
6. Separate market-residual layer using timestamp-valid executable prices.

---

## 7. WNBA feature roadmap

Do not copy NBA coefficients. The WNBA has fewer games, more roster churn,
different game length and schedule structure, and historically thinner public
data. The solution is stronger shrinkage and better availability capture, not
pretending the sample is NBA-sized.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Official availability and minutes | Snapshot each WNBA injury report; projected minutes, restriction, starter/bench role, replacement player, and report freshness | Player availability has outsized impact in a short rotation | The official report is now available, but history must be prospectively archived |
| 2 | Hierarchical player/lineup impact | WNBA-only RAPM with player and lineup priors; partial pooling by role/position; uncertainty grows for low-minute players | Handles sparse lineups without using raw plus-minus | Must not transfer NBA coefficients; priors may share structure only |
| 3 | Pace and Four Factors | Opponent-adjusted pace, eFG%, TOV%, OREB%, FTA rate on 5/10/season horizons with reliability shrinkage | Directly models possessions and efficiency | Public WNBA advanced data exists; verify use and archival terms |
| 4 | Roster and role discontinuity | Returning-minute share, transactions, hardship contracts, expansion/new-franchise flag, coach change | Stabilizes early-season and expansion-era priors | Entity mapping is a kill gate |
| 5 | Travel/circadian load | Distance, time zones, eastward/westward travel, recovery days, local start time, cumulative road miles | Recent WNBA-specific research finds travel and directional jet-lag relationships worth testing | Do not assume a universal penalty; estimate with team/season controls |
| 6 | Overseas/offseason workload | Days since overseas season, games in prior 30/60 days, late arrival to camp | Could explain early-season fatigue and role uncertainty | Data collection is difficult and licensing-sensitive; P2 research |
| 7 | Shot-profile and matchup | Rim/three frequency, assisted-shot rate, transition share, paint touches, opponent allowed profile | Adds matchup context to team efficiency | Public tracking coverage may be incomplete; report coverage explicitly |

### WNBA first ablations

1. Elo/trend control.
2. `+ pace + Four Factors` with heavy shrinkage.
3. `+ projected minutes x WNBA player impact`.
4. `+ roster continuity`.
5. `+ directional travel/circadian features`.
6. Combined model and a separately calibrated market-residual layer.

Because the WNBA sample is small, report season-by-season results and use a
fresh prospective cohort. A model chosen after repeatedly viewing a 100-game
holdout is not validated merely because the file calls that cohort “locked.”

---

## 8. MLB feature roadmap

MLB is the clearest case where the current team-score proxy is too lossy.
Starting pitcher, lineup, bullpen, park, roof, and weather are not optional
details; they are major components of the run-generating process.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | True starting-pitcher quality | Confirmed starter ID; handedness; opponent-adjusted K-BB%, xwOBA allowed, ground-ball rate, pitch-count/expected innings, days rest; velocity/spin/movement change; uncertainty | Separates the starter from generic team runs allowed and models how long the bullpen is exposed | Starter must be confirmed and every rolling stat cut off before game time |
| 2 | Projected/confirmed lineup quality | Batting-order-weighted xwOBA, K%, BB%, barrel/hard-hit rate and baserunning; platoon splits versus starter hand; difference between projected and confirmed lineup | Captures who will actually take the plate and in what order | Official lineup is late; use separate early and late horizons |
| 3 | Bullpen availability and quality | Reliever-level pitches in prior 1/2/3 days, consecutive-use flags, leverage role, handedness mix, recent velocity, expected available innings, closer/setup absence | Full-game outcomes often depend on which relievers are available, not season bullpen ERA | Must use pregame workload only and handle opener/bulk roles |
| 4 | Park, roof, and game-time forecast | Season-versioned handedness-specific park factors; roof/open state; temperature, dew point, pressure, precipitation, and wind vector projected onto home-plate-to-center-field orientation | Models the physical run environment instead of a crude magnitude-only factor | Train on archived forecasts issued at the same lead time, not realized weather |
| 5 | Pitch-mix/platoon matchup | Expected lineup performance against starter pitch families, velocity bands and movement; team weakness/strength by pitch type with shrinkage | Captures nonlinear batter-pitcher style matchups without relying on tiny head-to-head samples | Avoid batter-vs-pitcher history; it is usually too sparse |
| 6 | Defense and catching | Projected-fielders OAA/fielding run value, catcher framing/blocking/throwing, lineup-position interaction | Converts balls in play and borderline pitches into expected runs | Lower priority than starter/lineup/bullpen; keep season-versioned |
| 7 | Travel/circadian context | Eastward/westward shift, days to acclimate, local start time, getaway day, doubleheader game number | MLB research finds detectable eastward-travel effects | Estimate interactions; do not use one blanket penalty |
| 8 | Umpire assignment | Called-strike tendency, zone width, walk/strikeout effect, interaction with catcher framing and pitcher command | May affect totals and strikeout/run environment | Late assignment and substantial noise; P3 only |

### Correct MLB model form

Use two reconciled components:

1. **Relative-strength head:** which team has the stronger run differential.
2. **Absolute-intensity head:** the total run environment.

Reconcile both into one away/home run distribution, such as a hierarchical
Poisson/negative-binomial or simulation model with correlated scoring where
supported. Moneyline, run line, and total probabilities must be derived from
that same distribution.

### MLB first ablations

1. Repair and rename current inputs so training and serving match exactly.
2. True starter quality only.
3. Confirmed/projected lineup quality only.
4. Bullpen availability only.
5. Starter + lineup + bullpen.
6. Add season-versioned park and archived game-time forecast.
7. Add pitch-mix/platoon interaction.
8. Evaluate defense/catcher and travel as later challengers.

The first three groups should be collected prospectively even before the model
is ready. Coefficients cannot recover information that was never timestamped.

---

## 9. NFL feature roadmap

NFL samples are small, games are path-dependent, and quarterback/injury news can
move the fair price materially. Team final scores alone throw away most of the
available signal.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Quarterback identity and uncertainty | Expected starter; backup probability; opponent-adjusted early-down EPA/dropback, CPOE, sack/pressure response, scramble value, designed-run share; injury/practice status | QB is the highest-impact identity feature and a primary no-call gate | Status changes through the week; model each possible starter rather than imputing team average |
| 2 | Stable unit efficiency | Offense/defense early-down pass EPA, rush EPA, success rate, explosive-play rate, sack rate, neutral-situation pace; opponent and game-state adjusted | More stable and predictive than raw points, wins, or late-game garbage-time stats | Exclude kneels and heavily downweight extreme win-probability states |
| 3 | Injury and lineup value | Snap-weighted availability by QB, OL, receiver, pass rusher, coverage and interior defense; unit continuity; replacement quality | Multiple medium injuries can matter more than one headline skill-player tag | Official practice/game reports need observed timestamps and active/inactive confirmation |
| 4 | Protection-pressure matchup | OL continuity and pass-block performance versus opponent pressure/pass-rush profile; pressure without blitz, time to throw, QB under-pressure efficiency | Captures interaction between line health, pass rush, and QB style | Public tracking is partly aggregated/proprietary; use only reproducible data |
| 5 | Drives, pace, and pass tendency | Expected drives, seconds per play, pass rate over expectation, early-down pass rate, no-huddle, fourth-down aggressiveness | Critical for totals and possession count | Coaching changes create regime breaks |
| 6 | Receiver-coverage matchup | Target share/air-yards share, separation/cushion where available, man/zone tendencies, coverage shell, personnel grouping rates | Adds matchup detail beyond aggregate passing efficiency | Tracking access and sample size are limiting; P2 unless licensed |
| 7 | Weather, roof, and surface | Wind speed/direction, precipitation, temperature, roof state, grass/turf, kicker range and QB hand-size/cold interactions only if predeclared | Wind can alter passing, kicking, pace, and totals | Avoid fishing for arbitrary temperature thresholds |
| 8 | Special teams and field position | Opponent-adjusted kicking, punting, return value, starting field position, kicker availability | Close games turn on hidden yards and field-goal conversion | Shrink heavily because attempts are sparse |
| 9 | Rest and travel | Short week, mini-bye, post-bye, international travel, time-zone shift, altitude, consecutive road games | Meaningful context with few games per season | Team and season confounding; use partial pooling |
| 10 | Coaching/regime | Coordinator/head-coach continuity, play-caller change, fourth-down policy, neutral pace/PROE shift | Helps detect structural changes that Elo updates slowly | Must be effective-dated, not assigned retrospectively |

### NFL model form

Model expected drives and discrete scoring events, then derive the joint score
distribution. Apply Bayesian or empirical-Bayes shrinkage toward preseason and
prior-season priors, with roster continuity controlling how much prior strength
is retained. Widen uncertainty sharply for unresolved quarterback or offensive
line states.

### NFL first ablations

1. Elo/trend control.
2. `+ opponent-adjusted early-down unit efficiency`.
3. `+ starting-QB identity and uncertainty`.
4. `+ snap-weighted injury/line continuity`.
5. `+ pace/PROE/expected drives`.
6. `+ weather/roof` for totals and kicking cohorts.
7. Add protection-pressure matchup only when data coverage is reproducible.

---

## 10. Experiment design

### Development and final test

Use nested chronology:

1. **Training folds:** fit coefficients and player/team states using only prior
   observations.
2. **Inner validation folds:** select features, regularization, transformations,
   calibration method, and decision thresholds.
3. **Final test:** evaluate the fully specified candidate once.
4. **Prospective shadow:** require timestamp-valid features and prices before
   economic activation.

Every feature and hyperparameter tried counts as a research trial. Record failed
variants. If the final test has already influenced a decision, relabel it as
development evidence and reserve a new final cohort.

### Ablation table

For each candidate report:

| Variant | Feature coverage | Brier | Log loss | ECE | Market Brier | Calls at executable +EV | Net ROI | CLV | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| incumbent | | | | | | | | | |
| + feature A | | | | | | | | | |
| + feature B | | | | | | | | | |
| + A+B | | | | | | | | | |

Also report results by season/month, favorite/underdog, home/away, probability
bucket, decision horizon, missingness cohort, and the largest team/player
concentrations. Do not order the table only by P&L; that encourages selection on
the noisiest metric.

### Uncertainty

- Bootstrap by event date or week, not by treating correlated games/features as
  fully independent.
- Report confidence intervals for score differences, calibration, CLV, and ROI.
- Track how many variants were evaluated; repeated search raises the bar for a
  claimed win.
- Predeclare the primary metric and minimum meaningful improvement.
- Prefer a simpler model when the interval includes no gain.

---

## 11. Ruthless implementation order

The highest-value work is data provenance, not a more complex algorithm.

1. **Repair current parity and truth labels.** Split `team_recent_runs_allowed`
   from true starter quality; make weather game-time and direction-aware; use
   season-versioned park inputs; reconcile README/report/artifact metrics.
2. **Build prospective snapshot infrastructure from the no-signup stack.**
   Archive official injury, starter, lineup, inactive, roof/weather-forecast,
   and executable-price observations with `observed_at_utc`. A missing paid feed
   is not a blocker.
3. **Build NBA/WNBA possession and availability models.** Four Factors/pace plus
   projected minutes times shrunk player impact.
4. **Build NFL QB/unit-efficiency and injury states.** Do this before tracking
   exotica such as coverage shells.
5. **Build MLB starter/lineup/bullpen states.** This is higher value than more
   tuning of team score trends.
6. **Add environment and interaction features.** Travel, weather, park, shot
   profile, pitch mix, protection-pressure.
7. **Build the market-residual/economic layer.** Train only on timestamp-valid
   BBO history; keep it separate from independent probabilities.
8. **Promote only after a fresh test and user confirmation.** Until then, zero
   actual units.

What should not be prioritized now:

- deep neural networks on a few hundred WNBA/NFL games;
- raw head-to-head records or tiny batter-vs-pitcher samples;
- social-media sentiment;
- referee/umpire micro-effects before core availability data works;
- optimizing confidence thresholds against the already-opened holdout;
- calling generic `-110` units “profit.”

---

## 12. Verification checklist

Before presenting a feature recommendation:

- [ ] Historical and forward feature code produce the same semantic variable.
- [ ] The default path requires no new account, API key, or paid subscription.
- [ ] Every value has `observed_at_utc`, source, version, and missing reason.
- [ ] No postgame, closing, correction, or future-season information enters the
      independent feature.
- [ ] Train, validation, and final dates are complete and non-overlapping.
- [ ] The final test was not used to select the feature or threshold.
- [ ] Ablations include the incumbent and simple baseline.
- [ ] Proper scores, calibration, coverage, and uncertainty are reported.
- [ ] Economic claims use executable prices and costs from the same timestamp.
- [ ] Market-aware inputs are isolated from the independent model.
- [ ] Daily forward construction was tested with missing/stale providers.
- [ ] Artifact hashes and model/feature versions are reproducible.
- [ ] All research outputs remain zero-unit until explicit promotion.

Run code-quality checks after implementation, not during the research-only
stage:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Do not regenerate production artifacts, change thresholds, enable filters, or
commit a promotion until Vincent explicitly approves the named candidate.

---

## 13. Research basis and data-source notes

These sources establish feature meaning or empirical motivation. They do not
guarantee that a feature will improve this repository's model.

### Forecast evaluation and profitability

- Gneiting and Raftery, [Strictly Proper Scoring Rules, Prediction, and
  Estimation](https://doi.org/10.1198/016214506000001437): basis for using proper
  probability scores rather than hit rate alone.
- Hubacek, Sourek, and Zelezny, [Machine learning for sports betting: Should
  model selection be based on accuracy or
  calibration?](https://www.sciencedirect.com/science/article/pii/S266682702400015X):
  NBA betting experiments found calibration-based selection more useful for
  profitability than accuracy-based selection.
- Lopez de Prado, [A Data Science Solution to the Multiple-Testing Crisis in
  Financial Research](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3177057):
  motivation for recording trials and discounting repeatedly selected backtests.

### NBA and WNBA

- NBA, [Stats glossary](https://www.nba.com/stats/help/glossary?hidenav=true) and
  [Stats FAQ](https://www.nba.com/stats/help/faq): definitions for possessions,
  pace, ratings, tracking categories, and Four Factors.
- NBA, [official injury report](https://official.nba.com/nba-injury-report-2025-26-season/):
  reporting schedule and continually updated participation states.
- WNBA, [Stats FAQ](https://stats.wnba.com/help/faq/) and
  [lineups tool](https://stats.wnba.com/lineups/lineups-tool/): WNBA-specific
  pace, Four Factors, advanced, and lineup data.
- WNBA, [official injury report](https://www.wnba.com/webview/wnba-injury-report):
  official availability reports and update cadence.
- Petridis and Pelechrinis, [Lineup Regularized Adjusted Plus-Minus
  (L-RAPM)](https://arxiv.org/abs/2601.15000): lineup sparsity and informed-prior
  regularization.
- Leota et al., [Home-Court Advantage and the Associations of Travel and Jet Lag
  with Team Performance in the WNBA](https://pubmed.ncbi.nlm.nih.gov/42426359/):
  direct WNBA evidence for testing directional travel and recovery variables.

NBA/WNBA Stats data may be visible without being licensed for unrestricted bulk
reuse. Confirm access, archival, and commercial terms before making it a
production dependency.

- SportsDataverse publishes public [NBA `hoopR` and WNBA `wehoop` data
  repositories](https://github.com/orgs/sportsdataverse/repositories) and
  versioned season-level loaders/releases. Prefer release assets over repeated
  live requests, while retaining upstream source and generation timestamps.

### MLB

- MLB, [Statcast glossary](https://www.mlb.com/glossary/statcast): definitions
  and coverage for expected offense, pitch tracking, batted-ball quality,
  defense, running, and catching.
- MLB, [park factor](https://www.mlb.com/glossary/advanced-stats/park-factor),
  [Outs Above Average](https://www.mlb.com/glossary/statcast/outs-above-average),
  and [catcher framing](https://www.mlb.com/glossary/statcast/catcher-framing):
  official definitions for venue, defense, and receiving features.
- Song et al., [How jet lag impairs Major League Baseball
  performance](https://pmc.ncbi.nlm.nih.gov/articles/PMC5307448/): 20 seasons
  and 46,535 games, with stronger detectable effects after eastward travel.
- Koch and Panorska, [The Impact of Temperature on Major League
  Baseball](https://journals.ametsoc.org/view/journals/wcas/5/4/wcas-d-13-00002_1.xml):
  empirical motivation for a physically specified run-environment feature.
- Open-Meteo, [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
  and [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api):
  archived forecasts and fixed lead-time forecasts. These are preferable to
  realized historical weather when reproducing what was knowable before a game.
- `pybaseball`, [Statcast data interface](https://github.com/jldbc/pybaseball):
  keyless access to Baseball Savant pitch-level data. It is a scraper, so cache
  date partitions and treat endpoint/schema failures as unavailable data.

### NFL

- NFL Next Gen Stats, [glossary](https://nextgenstats.nfl.com/glossary): official
  definitions for tracking, completion probability, CPOE, time to throw,
  separation, cushion, and rushing metrics.
- NFL Football Operations, [Next Gen Stats
  overview](https://operations.nfl.com/gameday/technology/nfl-next-gen-stats):
  tracking-data provenance and advanced-metric categories.
- NFL Football Operations, [Personnel (Injury) Report
  Policy](https://operations.nfl.com/media/2683/2017-nfl-injury-report-policy.pdf):
  practice participation and game-status semantics.
- nflverse, [play-by-play build and data releases](https://github.com/nflverse/nflverse-pbp):
  reproducible public play-by-play infrastructure for EPA, success, pace, and
  game-state features. Verify each release's field definitions and licensing.

Public Next Gen Stats pages do not imply that complete play-level tracking data
is freely downloadable. Treat tracking-dependent features as licensed/P2 until
the actual reproducible source is secured.

### Keyless weather and market data

- Open-Meteo states that its free non-commercial API requires [no API
  key](https://open-meteo.com/en/about). Its free endpoint is rate-limited and
  has no uptime guarantee, so retain raw forecast snapshots and a missing-source
  path.
- The [Polymarket US public API](https://docs.polymarket.us/api-reference/introduction)
  exposes markets, events, order books, and BBO without an API key. Authenticated
  trading and private portfolio endpoints remain outside this research-source
  policy.
