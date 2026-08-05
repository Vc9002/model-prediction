## Clean-slate conclusion

The repository has useful infrastructure, but **none of the existing model structures should be treated as correct by default**. They should be frozen as benchmark controls while a separate system is rebuilt from first principles.

The strongest reusable components are the point-in-time cutoff, immutable hashes, exact market matching, chronological validation framework, settlement process, and audit trail. The weak point is the actual forecasting layer: most active models are small Elo-based or score-history baselines with very few features, while economic qualification still relies heavily on directional accuracy and hypothetical `-110` scoring rather than demonstrated execution at real prices.  

A profitable system should have five independent layers:

1. **Point-in-time data and state estimation**
2. **Sport-specific score or match distribution**
3. **Out-of-fold ensemble and probability calibration**
4. **Separate market-residual model**
5. **Execution, liquidity, cost, and portfolio decision layer**

Adding XGBoost directly to the existing feature matrices would probably produce a more complicated version of the same incomplete models. Calibration must be fitted on predictions that are independent of base-model training, and validation must remain chronological; scikit-learn explicitly warns that calibration on training predictions is biased and provides time-series splitting with a configurable gap. 

## Audit of the current models

| Model | Current construction | Clean-slate verdict |
|---|---|---|
| **MLB moneyline** | Logistic regression using Elo, trend, park, weather, starter ERA gap, and bullpen weakness. The active v8 artifact is explicitly `qualified: false` after a validation-Brier regression.  | Retain only as a benchmark. Rebuild around pitcher, lineup, bullpen, park, and weather state feeding a coherent run distribution. |
| **MLB spreads/totals** | Gamma-Poisson simulation using relative offense, starter, bullpen, park, and weather factors. The latest totals diagnostic deteriorated to approximately 0.041 correlation and 52.9% hit rate.   | Replace the shared relative-only run formula with separate **run-intensity** and **run-differential** heads, reconciled into one joint score distribution. |
| **NBA** | Logistic regression using only Elo, offensive trend, and defensive trend. Its calibration slope is about 1.79, indicating substantial probability-shape error despite strong directional accuracy.  | Rebuild around projected possessions, offense and defense per possession, projected minutes, player impact, availability, and lineup composition. |
| **WNBA** | The same three-feature structure as NBA, with a threshold so close to 0.50 that it effectively calls the complete slate.  | Build an independent WNBA model with stronger shrinkage, projected minutes, player availability, roster continuity, and possession efficiency. |
| **NFL** | Logistic regression using only Elo and score trend. The locked evaluation contains only 122 games and its ECE is roughly 0.10.  | Replace with expected drives and drive-scoring outcomes, quarterback state, early-down efficiency, pace, injuries, protection, pressure, and weather. |
| **Soccer** | Independent Poisson goals with fixed home multiplier `1.15`, fixed Dixon–Coles `rho=-0.10`, EWMA goals, and hardcoded BTTS Platt coefficients.  | Keep as a control. Build dynamic attack/defense effects, league strength, roster availability, xG/event features, and learned time decay. Dixon–Coles remains a valid baseline, not the final model. |
| **Tennis** | Fixed `K=32` Elo, fixed 60% surface/40% overall blend, constant uncertainty, no serve/return input in the actual forecast.  | Replace with dynamically tuned surface ratings, serve/return state, inactivity, fatigue, format, player age, and point-to-match probability conversion. |
| **LoL/CS2/Dota/Valorant/R6** | Per-title neutral Elo with Platt scaling, hand-set recency and tier multipliers, inactivity decay, and thin-history shrinkage. It remains organization/team based rather than roster, player, map, patch, or draft based.   | Replace with independent title-specific models. Model map/game probability first, then derive series probability from format and veto/draft state. |
| **KBO/NPB** | Home Elo plus a flat or Elo-gap tie heuristic; no pitcher, lineup, bullpen, park, or run model.  | Retain as a benchmark until reliable player-level data exists. Rebuild around league-specific run distributions rather than copying MLB coefficients. |

The current soccer model is conceptually related to the Dixon–Coles approach, but the original method dynamically estimates team attack, defense, dependence, and time weighting; fixed global constants and a simple EWMA are a much narrower implementation. 

Surface Elo is a legitimate tennis baseline, but public research finds that standard and surface-adjusted Elo each have value depending on tour and surface. That supports testing multiple rating systems rather than permanently fixing a 60/40 blend. 

## Free and tokenless data stack

Use these as the starting source matrix, subject to prospective archival, licensing review, and schema tests:

- **Market data:** Polymarket US exposes public market, order-book, and BBO data without an API key. The order book includes executable bids, offers, quantities, market state, and transaction time. This is the correct source for economic evaluation, not an indicative midpoint. 
- **MLB:** `pybaseball` retrieves Statcast, Baseball Savant, FanGraphs, and Baseball Reference information, including pitch-level data. Its own documentation warns that historical Statcast data can change, so raw results must be cached and hash-versioned. 
- **Weather:** Open-Meteo requires no API key and provides historical forecasts and individual archived model runs. Archived forecasts are critical because realized weather is a retrospective leak. 
- **NBA/WNBA:** SportsDataverse maintains NBA and WNBA play-by-play, rosters, shots, officials, box scores, and related release datasets. 
- **NFL:** `nflreadpy` is the current Python loader for nflverse data; the older `nfl_data_py` package is deprecated. nflverse provides play-by-play, rosters, injuries, depth charts, and related datasets. 
- **Soccer:** StatsBomb Open Data supplies free match, event, lineup, and selected 360 data for included competitions. Coverage is not universal, so source coverage must remain an explicit feature. 
- **Tennis:** Jeff Sackmann’s ATP data includes identities, rankings, results, and match statistics; use the corresponding WTA data under the same point-in-time discipline. 
- **CS2:** Valve publishes Regional Standings data and methodology that can serve as a roster-level prior rather than relying exclusively on organization Elo. 
- **Dota 2:** OpenDota has a keyless free tier and provides match and player data, but it must be locally cached and rate-limited. 
- **Valorant and Rainbow Six:** I did not find a sufficiently stable, comprehensive, official, tokenless historical feed. Keep these research-only using the existing BO3 source until a reproducible source contract is established.

Do **not** create one universal “clean rate” for every athlete. For MLB pitchers, define scoreless-inning rate, clean-appearance rate, and first-inning clean rate with beta-binomial shrinkage. For other sports, use sport-relevant player state: availability, minutes/snaps, player impact, serve/return strength, map performance, or roster continuity.

# Recompiled three-part rebuild instructions

# PART 1 — CLEAN-SLATE REPOSITORY, DATA, AND FEATURE REBUILD

You are rebuilding `Vc9002/model-prediction` from first principles.

Assume every current model, feature, threshold, architecture decision, metric, and profitability claim may be wrong. Existing code is evidence and a source of regression tests, not the required architecture.

Do not place orders, submit trades, settle production ledgers, overwrite model artifacts, or mutate existing historical data. All new work starts on a separate branch and remains shadow-only.

## Primary objective

Construct a complete point-in-time data platform capable of supporting accurate and economically testable models for:

- MLB
- NBA
- WNBA
- NFL
- soccer
- tennis
- League of Legends
- Counter-Strike 2
- Dota 2
- Valorant
- Rainbow Six
- KBO
- NPB

## A. Audit the repository completely

Read every source, configuration, script, test, model artifact, model card, data manifest, workflow, and current ledger interface.

Determine through import and call tracing:

- what actually runs in `daily`;
- what is dead code;
- what is shadow-only;
- which features are calculated but not consumed;
- which artifact is loaded for every sport and market;
- where train-serving definitions differ;
- where neutral-value imputation disguises missing data;
- where thresholds or model parameters are hardcoded;
- where retrospective information can leak;
- where the same concept has competing implementations;
- where documentation disagrees with code;
- where market prices are mixed with sports probabilities;
- which outputs are hypothetical rather than executable.

Create:

```text
outputs/rebuild/current_system_inventory.json
outputs/rebuild/current_system_audit.md
outputs/rebuild/current_model_baselines.parquet
```

The baseline table must reproduce every current model’s predictions and metrics before any replacement is attempted.

## B. Create an isolated rebuild

Create a branch such as:

```bash
rebuild/clean-slate-v1
```

New outputs go only under:

```text
data/rebuild/
outputs/rebuild/
config/models/challengers/
```

Freeze current model artifacts and never overwrite them.

Record:

- Git SHA;
- dependency versions;
- test results;
- lint results;
- type-check results;
- audit-chain result;
- raw-data hashes.

## C. Build the Python stack

Retain:

- NumPy
- pandas
- SciPy
- scikit-learn
- Pydantic
- httpx
- joblib

Add only after compatibility testing:

- XGBoost
- pybaseball
- Polars
- PyArrow
- DuckDB
- Pandera
- statsmodels
- Optuna only for bounded chronological optimization

Use one locked Python environment. Resolve the repository’s inconsistent Python-version declarations and choose one supported runtime after testing every dependency.

## D. Replace scattered analytical storage

Use a medallion-style layout:

```text
data/rebuild/raw/          # immutable provider responses
data/rebuild/normalized/   # canonical Parquet tables
data/rebuild/features/     # point-in-time feature snapshots
data/rebuild/markets/      # timestamped market books and BBO
data/rebuild/metadata.db   # SQLite metadata and registry
```

Use:

- immutable compressed JSON/JSONL for raw responses;
- Parquet for normalized facts and feature matrices;
- DuckDB for research;
- SQLite for schemas, model metadata, source health, entity mappings, and audit state;
- Excel only as an exported report.

Every observation must include:

```text
source
source_record_id
source_version
observed_at_utc
effective_at_utc
event_start_utc
ingested_at_utc
available
missing_reason
raw_snapshot_hash
schema_version
```

A decision at time `T` may only use information with `observed_at_utc <= T`.

Implement tested as-of joins. Never join simply by event date when a publication timestamp exists.

## E. Build canonical identity

Create stable identities for:

- events;
- teams;
- players;
- rosters;
- venues;
- leagues;
- competitions;
- market contracts.

Persist cross-source IDs and effective dates.

Fuzzy matching may propose a mapping but may not silently authorize one. Low-confidence mappings must fail closed.

Test:

- traded players;
- roster changes;
- duplicate names;
- aliases;
- accented names;
- team relocations;
- organization versus roster identity in esports;
- identical spread or total lines on unrelated games.

## F. Build source collectors

### MLB

Use:

- current ESPN and MLB StatsAPI collectors;
- pybaseball;
- Baseball Savant/Statcast;
- archived Open-Meteo forecasts;
- Polymarket US market data.

Cache raw data in bounded date chunks. Make every collector restartable, idempotent, rate-limited, and schema-tested.

### NBA/WNBA

Use SportsDataverse release data and existing public ESPN/NBA/WNBA sources for:

- play-by-play;
- substitutions and stints;
- rosters;
- game box scores;
- shots;
- officials;
- player availability;
- schedules.

Prospectively archive every injury or availability report.

### NFL

Use nflreadpy and nflverse for:

- play-by-play;
- schedules;
- rosters;
- weekly rosters;
- depth charts;
- injuries;
- snap counts;
- participation;
- officials.

### Soccer

Use:

- StatsBomb Open Data where competition coverage exists;
- current public score and lineup sources;
- prospectively captured roster availability;
- public market books.

Record source coverage by competition. Never substitute a different competition’s feature distribution without an explicit hierarchical model.

### Tennis

Use Sackmann ATP/WTA identities, rankings, matches, and match stats. Build prospective tournament, surface, schedule, withdrawal, and market snapshots.

### Esports

Retain BO3 as a replaceable results source.

Add, only after verifying terms:

- Valve VRS for CS2;
- OpenDota for Dota 2;
- Oracle’s Elixir-style LoL enrichment where licensing permits;
- effective-dated roster archives;
- patch, tournament, map, veto, side, draft, and series format data.

Keep every title independent.

### KBO/NPB

Retain official league schedule/results collectors.

Investigate stable player, pitcher, lineup, bullpen, venue, and weather sources. If reliable point-in-time data cannot be obtained, explicitly restrict the challenger model instead of inventing MLB-equivalent inputs.

## G. Build decision horizons

Create separate feature datasets for:

- `early`: 24–48 hours before start;
- `mid`: 4–8 hours before start;
- `late`: 15–90 minutes before start.

Late information must never be backfilled into early decisions.

Each horizon receives a separate:

- feature schema;
- model;
- calibrator;
- coverage report;
- missingness report;
- economic evaluation.

## H. Build sport-specific feature stores

### MLB

Starting pitcher:

- K%, BB%, K-BB%, CSW%, swinging-strike rate;
- xwOBA, xERA, xFIP and SIERA-style estimate;
- ground-ball, barrel and hard-hit rates;
- pitch mix, velocity, movement and recent changes;
- expected pitch count and innings;
- rest and recent workload;
- times-through-order penalties;
- opener/bulk probability;
- handedness;
- uncertainty and sample size.

Pitcher clean-rate group:

- first-inning clean rate;
- scoreless-inning rate;
- clean-appearance rate;
- rolling 10/20 appearance rates;
- season and multiseason rates;
- opponent adjustment;
- beta-binomial posterior mean and variance;
- sample size.

Lineups:

- projected and confirmed batting order;
- plate-appearance weights;
- xwOBA, K%, BB%, barrel, hard-hit and contact;
- platoon splits;
- pitch-family matchup;
- baserunning;
- projected versus confirmed lineup difference;
- missing regulars and uncertainty.

Bullpen:

- reliever-level quality;
- pitches and appearances over 1/2/3/5/7 days;
- consecutive-use flags;
- leverage role;
- expected availability;
- handedness balance;
- expected available innings.

Environment:

- season-versioned park factors;
- roof;
- temperature;
- humidity;
- dew point;
- pressure;
- precipitation;
- wind vector relative to field orientation;
- forecast age and model disagreement.

### NBA/WNBA

Build:

- possessions and opponent-adjusted pace;
- offensive and defensive Four Factors;
- half-court and transition efficiency;
- projected minutes;
- player availability;
- replacement minutes;
- ridge/elastic-net RAPM-style impact;
- starting and closing-lineup strength;
- lineup continuity;
- shot-location profile;
- rest, travel and altitude;
- officials and foul environment;
- uncertainty from incomplete rotations.

WNBA priors and coefficients must be trained independently and shrunk more strongly.

### NFL

Build:

- quarterback identity and probability of starting;
- early-down pass/rush EPA;
- success rate;
- CPOE;
- pressure, sack and scramble response;
- offensive-line continuity;
- receiver and defensive availability;
- pace and pass rate over expectation;
- expected drives;
- red-zone and explosive-play state;
- fourth-down aggressiveness;
- weather, roof and surface;
- kicker and special-teams state.

### Soccer

Build:

- dynamic attack and defense strength;
- xG for and against where available;
- non-penalty xG;
- shot quality;
- set-piece strength;
- lineup and goalkeeper availability;
- league and competition strength;
- roster continuity;
- schedule congestion;
- home/neutral venue;
- learned recency;
- source-coverage uncertainty.

### Tennis

Build:

- overall and surface rating;
- serve and return points won;
- first- and second-serve state;
- break-point performance with shrinkage;
- opponent-adjusted serve/return;
- surface transition;
- inactivity;
- recent workload;
- travel;
- age;
- format;
- indoor/outdoor;
- retirement and withdrawal risk;
- sample-size uncertainty.

### Esports

Per title, build:

- effective-dated roster;
- player ratings;
- roster tenure;
- region and tournament strength;
- inactivity and schedule;
- patch;
- best-of format;
- LAN/online;
- map pool and veto for CS2;
- side and map conversion;
- draft/champion features for post-draft LoL only;
- Dota draft and patch features only at the appropriate horizon.

### KBO/NPB

Use league-specific:

- starter quality;
- expected starter innings;
- lineup quality;
- bullpen availability;
- park and weather;
- tie-generating process;
- foreign-player and roster rules where measurable.

Do not transfer MLB coefficients.

## I. Missingness is data

For every feature group include:

- observed value;
- availability flag;
- source;
- observation age;
- missing reason;
- conflict count;
- sample size;
- uncertainty.

Never silently fill a required value with zero or league average.

## J. Required tests

Add tests for:

- future leakage;
- same-day leakage;
- historical corrections;
- train-serving parity;
- raw-data hash stability;
- schema drift;
- duplicate events and pitches;
- player and roster identity;
- stale reports;
- conflicting sources;
- failed collectors;
- restartability;
- deterministic features;
- horizon separation;
- market timestamp validity.

Part 1 is complete only when every sport has a documented data contract, reproducible feature build, coverage report, and honest list of unavailable features.

# PART 2 — SPORT-SPECIFIC DISTRIBUTIONS, ML CHALLENGERS, ENSEMBLING, AND CALIBRATION

Continue only after Part 1 passes all point-in-time, identity, and train-serving tests.

Do not promote or execute any challenger.

## A. Modeling principles

Every sport must have:

1. a transparent control model;
2. a sport-specific statistical model;
3. a regularized linear challenger;
4. a nonlinear scikit-learn challenger;
5. an XGBoost challenger;
6. an out-of-fold ensemble;
7. an independently fitted calibrator;
8. an untouched final test;
9. a new prospective test after the final test is consumed.

Accuracy is not the primary objective.

Rank models using:

1. log loss;
2. Brier score;
3. calibration intercept and slope;
4. reliability curves and ECE;
5. distributional likelihood;
6. score and interval accuracy;
7. coverage and stability;
8. directional accuracy as a secondary metric.

## B. Validation architecture

Replace one-shot model selection with nested chronological validation.

Use expanding or rolling folds grouped by complete event dates.

For every fold:

- train on past data only;
- leave a publication-time embargo where needed;
- tune on the next chronological block;
- generate out-of-fold predictions;
- never update ratings with another same-day game before predicting all games on that date;
- preserve one untouched final test;
- mark final tests as consumed after evaluation.

Use date-cluster and team-cluster bootstrap intervals.

Do not use random K-fold cross-validation.

Do not inspect the final test while selecting:

- features;
- model family;
- hyperparameters;
- ensemble weights;
- calibrator;
- confidence threshold.

## C. Common challengers

### Regularized generalized linear models

Use scikit-learn pipelines with:

- explicit missingness indicators;
- transformations learned on training data only;
- L1, L2 and elastic-net regularization;
- interactions selected in advance;
- chronological tuning.

### Histogram gradient boosting

Use conservative:

- leaf counts;
- depth;
- learning rate;
- minimum leaf sample;
- L2 regularization;
- chronological early stopping;
- native missing-value handling.

### XGBoost

Use XGBoost as a challenger, not as automatic production truth.

Use:

- shallow trees;
- low learning rate;
- `hist` tree method;
- strong `min_child_weight`;
- L1 and L2 penalties;
- row and feature subsampling;
- chronological validation;
- early stopping;
- deterministic seeds;
- bounded thread counts;
- interaction constraints where justified;
- monotonic constraints only for features with defensible directional definitions.

Persist and serve the best iteration, not an arbitrary final boosting round.

### Statistical distributions

Use SciPy and statsmodels for:

- Poisson;
- negative binomial;
- beta-binomial;
- multinomial;
- Skellam where appropriate;
- predictive intervals;
- likelihood scoring;
- posterior predictive checks.

## D. MLB architecture

Build two separate latent heads:

### Absolute run-intensity head

Predicts total scoring environment from:

- both lineups;
- both starters;
- expected starter innings;
- bullpen availability;
- park;
- roof;
- weather;
- catcher;
- umpire when available;
- league/season run environment.

### Relative run-strength head

Predicts which team owns the run advantage from:

- lineup differential;
- starter differential;
- bullpen differential;
- defense;
- handedness and pitch-mix matchup;
- home advantage.

Reconcile these heads into away and home expected runs.

Test:

- independent Poisson;
- latent-Gamma Poisson;
- negative binomial;
- correlated or bivariate count simulation.

Derive from the same joint distribution:

- moneyline;
- run line at every supported line;
- total at every supported line;
- push;
- expected score;
- score quantiles;
- prediction intervals.

No disconnected MLB moneyline classifier may contradict the score distribution without being explicitly labeled as a challenger.

## E. NBA and WNBA architecture

Estimate:

```text
expected possessions
×
lineup-adjusted points per possession
```

Build separate home and away efficiency estimates using:

- team offense and defense;
- projected minutes;
- player impact;
- availability;
- Four Factors;
- shot-profile matchup;
- rest and travel;
- uncertainty.

Simulate or model the joint score distribution.

Derive:

- moneyline;
- spread;
- total;
- expected margin;
- expected total.

Train NBA and WNBA independently.

Use partial pooling for WNBA player effects and low-minute lineups.

## F. NFL architecture

Estimate:

- expected number of drives;
- probability distribution over drive results;
- expected score by team.

Drive outcomes should include:

- no score;
- field goal;
- touchdown;
- safety or other rare outcome where data permits.

Condition on:

- quarterback state;
- early-down efficiency;
- protection and pressure;
- pace;
- field position ability;
- injury state;
- coaching regime;
- weather;
- special teams.

Simulate coherent final scores and derive moneyline, spread and total.

Retain Elo as a prior, not the complete model.

## G. Soccer architecture

Use the existing fixed Poisson–Dixon–Coles model as Control A.

Build:

- dynamically estimated attack and defense;
- learned home advantage by league;
- learned time decay;
- learned low-score dependence;
- hierarchical league strength;
- promoted/relegated-team priors;
- lineup and goalkeeper state;
- xG/event enrichment where covered;
- negative-binomial or generalized count challengers where overdispersion exists.

Generate one coherent score matrix for:

- three-way moneyline;
- totals;
- BTTS;
- Asian or European handicap only where exact contract semantics exist.

## H. Tennis architecture

Controls:

- overall Elo;
- surface Elo;
- current fixed-blend model.

Challengers:

- dynamically optimized surface blend;
- Glicko or uncertainty-aware rating;
- Bradley–Terry model;
- serve/return logistic model;
- point-level model converted through tennis scoring rules;
- XGBoost on player-state differences.

Derive match probability from point/game/set state and match format where data quality permits.

Do not mix post-draw or post-match information into pre-match ratings.

## I. Esports architecture

Build independent models for every title.

### LoL

Model game probability using:

- roster;
- player/role strength;
- region;
- tournament;
- patch;
- side;
- form;
- roster tenure.

For a post-draft model, add champion and composition features only after the draft is known.

Convert game probability into best-of-series probability.

### CS2

Model map probability using:

- exact five-player roster;
- VRS or rating prior;
- map-specific strength;
- veto order;
- T/CT side;
- LAN/online;
- event tier;
- travel and schedule;
- patch/map-pool era.

Then simulate the series.

### Dota 2

Use:

- roster;
- player and team strength;
- patch;
- hero pool;
- draft state by horizon;
- side;
- tournament;
- series format.

### Valorant and Rainbow Six

Do not promote until roster, map, patch and tournament data reach reliable point-in-time coverage.

Keep series Elo as the control.

## J. KBO and NPB architecture

Controls:

- current tie-aware Elo;
- league-specific home Elo.

Challengers:

- league-specific starter/lineup/bullpen score model;
- tie probability derived from the score distribution;
- count model calibrated separately by league.

Do not use a manually shaped Elo-gap tie function if a coherent score distribution can estimate ties directly.

## K. Feature ablation

Predeclare feature groups.

Run isolated and cumulative ablations.

Report for every group:

- change in log loss;
- change in Brier;
- calibration;
- coverage;
- fold stability;
- seasonal stability;
- missingness sensitivity;
- bootstrap interval;
- model importance stability;
- live availability.

Reject any feature that:

- helps only after viewing the final test;
- depends on post-event information;
- works in one month only;
- has unstable sign;
- causes major coverage loss;
- acts mainly as a missingness proxy;
- cannot be reproduced in live inference.

## L. Player-rate shrinkage

Every rate must carry:

- numerator;
- denominator;
- raw rate;
- prior;
- posterior mean;
- posterior variance;
- effective sample;
- recency weighting;
- opponent adjustment where justified.

Use beta-binomial shrinkage for binary clean-rate statistics and empirical-Bayes or hierarchical shrinkage for continuous player metrics.

## M. Out-of-fold ensemble

Generate chronological out-of-fold predictions from:

- statistical model;
- regularized linear model;
- histogram gradient boosting;
- XGBoost;
- incumbent baseline.

Test:

- equal-weight average;
- inverse-log-loss weights;
- nonnegative constrained stacking;
- logistic stacking on logits.

The stacker may see only out-of-fold predictions.

Prefer the simplest stable ensemble.

## N. Calibration

Compare:

- identity;
- sigmoid/Platt;
- isotonic when sample size supports it;
- temperature scaling;
- beta calibration if implemented and validated.

Fit calibration on data disjoint from base-model fitting.

Use sport-, market-, and horizon-specific calibrators unless a pooled calibrator demonstrably improves small samples.

Store calibrator and base model as separately hashed, mutually bound artifacts.

## O. Uncertainty

Output:

- raw prediction;
- calibrated probability;
- model-family dispersion;
- bootstrap interval;
- feature-missingness penalty;
- player/lineup uncertainty;
- lower and upper probability;
- data freshness;
- sample size.

Uncertainty must affect the later decision layer.

## P. Deliverables

Create:

```text
outputs/rebuild/model_benchmark.parquet
outputs/rebuild/model_benchmark.md
outputs/rebuild/feature_ablation.parquet
outputs/rebuild/calibration_report.md
outputs/rebuild/test_consumption_registry.json
config/models/challengers/<model>.json
```

No challenger may be called profitable in Part 2. Part 2 establishes probability quality only.

# PART 3 — MARKET RESIDUALS, EXECUTABLE PROFITABILITY, SHADOW DEPLOYMENT, AND PRODUCTION SAFETY

Continue only after Part 2 has frozen its final predictive models and calibrators.

Do not submit real orders.

## A. Separate the three questions

The system must answer independently:

1. What is the sports-only probability?
2. Is the timestamp-valid market price wrong?
3. Is the disagreement large enough to trade after costs and risk?

Do not insert market prices into the sports-only model.

## B. Capture executable evidence

For every event and horizon, prospectively store:

- exact event ID;
- exact market ID;
- exact side;
- exact line;
- contract wording;
- settlement rules;
- observed timestamp;
- event start;
- best bid;
- best offer;
- multiple depth levels;
- available quantity;
- market state;
- order-book hash;
- quote age;
- first observed quote;
- later quotes;
- closing quote;
- settlement.

Use Polymarket US public order-book and BBO data.

Do not use:

- indicative event-list prices;
- midpoint as executable ask;
- postgame prices;
- reconstructed entry prices;
- closing prices as entries;
- generic `-110`;
- a different line;
- another event sharing the same line;
- in-play data for a pregame backtest.

## C. Calculate executable fair value

Store separately:

- model probability;
- conservative model probability;
- best executable ask;
- opposing-side book;
- no-vig market estimate;
- spread;
- expected slippage;
- fees;
- depth-adjusted fill price;
- raw edge;
- cost-adjusted edge;
- expected value.

Walk the actual order book for the proposed quantity.

A positive model-minus-midpoint difference is not a tradeable edge.

## D. Build a separate market-residual model

Inputs may include:

- calibrated sports probability;
- market no-vig probability;
- logit disagreement;
- model uncertainty;
- model-family disagreement;
- spread;
- depth;
- quote age;
- time to start;
- price movement;
- source completeness;
- horizon;
- sport;
- market type.

Possible targets:

- positive cost-adjusted return;
- closing-line improvement;
- expected return;
- probability that the model-market difference is genuine.

Use chronological out-of-fold validation.

Compare against:

- no trade;
- raw edge threshold;
- uncertainty-adjusted threshold;
- market-only baseline.

Do not allow the residual model to rewrite the independent sports probability.

## E. Dual qualification

### Predictive qualification

Require:

- improved or non-inferior log loss and Brier;
- acceptable calibration;
- stable reliability;
- sufficient coverage;
- no point-in-time violations;
- train-serving parity;
- stability across seasons and cohorts.

### Economic qualification

Require:

- real executable quotes;
- modeled spread, fees and slippage;
- sufficient independent events and dates;
- positive cost-adjusted return;
- positive or non-negative CLV;
- bootstrap uncertainty;
- acceptable drawdown;
- stable performance across price and edge buckets;
- no single team, month or market driving results;
- adequate depth;
- successful contract matching.

A model may be predictively strong and economically unusable.

Statuses must include:

```text
REJECTED
RESEARCH_ONLY
PREDICTIVELY_QUALIFIED
ECONOMIC_SAMPLE_INSUFFICIENT
ECONOMICALLY_QUALIFIED_FOR_SHADOW
ELIGIBLE_FOR_SEPARATE_LIVE_REVIEW
```

## F. Threshold selection

Select trade thresholds on an economic validation set only.

Possible threshold dimensions:

- lower-bound edge;
- expected value;
- residual score;
- quote age;
- liquidity;
- model uncertainty;
- missingness;
- horizon.

Apply the frozen threshold once to the untouched economic test.

Do not test dozens of thresholds and report only the best result. Record every trial and apply multiple-testing correction or nested validation.

## G. Conservative probability

Create a validated lower-bound estimate incorporating:

- model bootstrap uncertainty;
- calibration uncertainty;
- player/lineup uncertainty;
- data quality;
- model disagreement.

A trade may proceed in paper simulation only when the conservative probability clears:

- executable ask;
- fees;
- slippage;
- required safety margin.

## H. Position sizing

Zero is the default valid size.

Remove any mandatory minimum of 1 unit.

Compare:

- flat stake;
- fixed fractional Kelly;
- capped fractional Kelly;
- uncertainty-adjusted Kelly;
- no trade.

Use conservative probability, not raw point probability.

Enforce:

- event cap;
- team cap;
- sport cap;
- market-type cap;
- same-game correlation cap;
- daily cap;
- drawdown response;
- minimum depth;
- maximum quote age;
- unit rounding;
- maximum slippage.

Do not choose sizing based only on the largest historical ending bankroll.

## I. Correlation

Identify shared risk among:

- moneyline and spread on the same team;
- total and pitcher/lineup-derived markets;
- multiple positions in one event;
- repeated exposure to one team;
- weather-driven games;
- model-family defects;
- related series markets.

Report nominal and correlation-adjusted exposure.

## J. Economic evaluation

For every model, sport, market and horizon report:

- opportunities;
- accepted trades;
- rejected trades by reason;
- turnover;
- average entry;
- average spread;
- depth;
- raw edge;
- conservative edge;
- expected value;
- ROI;
- P&L;
- CLV;
- drawdown;
- volatility;
- win rate;
- average win/loss;
- bootstrap confidence interval;
- probability ROI exceeds zero;
- month;
- team;
- price bucket;
- edge bucket;
- liquidity bucket;
- missingness cohort;
- horizon.

Clearly distinguish real quoted fills from hypothetical fills.

## K. Stress tests

Re-run with:

- one tick worse;
- two ticks worse;
- delayed execution;
- reduced depth;
- partial fills;
- increased fees;
- probability shrinkage;
- best month removed;
- best team removed;
- largest wins removed;
- doubled correlation;
- stale-data exclusions;
- stricter market matching.

A system that fails under a minor realistic degradation remains research-only.

## L. Prospective deployment

### Stage 1: retrospective predictive research

No ledger writes or orders.

### Stage 2: prospective shadow

Freeze predictions before start, capture books, settle and measure CLV. No orders.

### Stage 3: conservative paper portfolio

Apply frozen thresholds and sizing to simulated fills. No orders.

### Stage 4: live-review eligibility

Outside this task. Requires separate explicit authorization after prospective evidence exists.

## M. Monitoring

Monitor:

- source health;
- schema drift;
- missingness;
- feature drift;
- calibration;
- Brier;
- log loss;
- model disagreement;
- contract-match failures;
- quote latency;
- CLV;
- ROI;
- drawdown;
- rejected opportunities;
- liquidity.

Automatic health states:

```text
HEALTHY_SHADOW
DATA_DEGRADED
CALIBRATION_DRIFT
NEGATIVE_CLV
CONTRACT_MATCH_FAILURE
EXECUTION_SAMPLE_INSUFFICIENT
REVIEW_REQUIRED
ROLLBACK_REQUIRED
```

Do not automatically retrain because of a short losing run.

## N. Storage and migration

Migrate the authoritative ledger to SQLite with ACID transactions.

Tables should include:

- predictions;
- feature snapshots;
- model versions;
- calibration artifacts;
- market snapshots;
- trade decisions;
- paper orders;
- real orders if separately enabled later;
- settlements;
- closing prices;
- reviews;
- audit events.

Original predictions and quotes are immutable.

Generate Excel workbooks from SQLite for review; Excel is not the database.

Integrate through adapters so the existing dashboard, audit chain, settlement, and exact-contract matching continue to work during migration.

## O. Final deliverables

Create:

```text
outputs/rebuild/predictive_report.md
outputs/rebuild/economic_report.md
outputs/rebuild/clv_report.md
outputs/rebuild/stress_report.md
outputs/rebuild/source_coverage.md
outputs/rebuild/model_cards/
outputs/rebuild/promotion_decision.json
outputs/rebuild/rollback_plan.md
outputs/rebuild/reproduction_commands.md
```

For every model provide a blunt verdict, evidence, sample size, predictive metrics, economic metrics, CLV, uncertainty, largest risk, and next action.

Never claim profitability from:

- hit rate alone;
- hypothetical `-110` units;
- midpoint prices;
- shadow P&L without executable quotes;
- an operator override;
- a consumed holdout;
- a small favorable streak.

Profitability may be claimed only after the frozen model and decision policy demonstrate positive prospective cost-adjusted performance with sufficient evidence.

This version makes the existing project a **benchmark and data source**, not the foundation that every rebuilt model must imitate.