# Claude Code Takeover — Three-Part Clean-Slate Rebuild

## Mission

Take over `Vc9002/model-prediction` and finish the clean-slate prediction-system rebuild from data collection through production-ready shadow operation.

Treat all incumbent models, thresholds, feature definitions, calibration claims, profitability claims, and architecture decisions as potentially incorrect. Existing models must remain frozen benchmark controls—not assumptions the rebuilt system must imitate.

The complete system must have five independent layers:

1. Point-in-time data and state estimation
2. Sport-specific score or match distribution
3. Chronological out-of-fold ensemble and independent calibration
4. Separate market-residual model
5. Execution, liquidity, cost, sizing, and portfolio decision layer

Do not submit real orders. Do not mutate production ledgers. Do not overwrite incumbent artifacts. All rebuilt work remains shadow-only until it accumulates sufficient prospective economic evidence and receives separate authorization.

The immediate operational priority is:

```text
MLB production-ready shadow pipeline
```

"Production-ready shadow" means the MLB system can reliably execute the complete workflow:

```text
collect
→ normalize
→ build point-in-time features
→ generate calibrated sports probabilities
→ derive coherent moneyline/spread/total probabilities
→ capture exact executable market books
→ freeze predicted winner or totals side
→ evaluate conservative cost-adjusted value
→ output BET or NO_BET
→ store paper decision and evidence
→ support later settlement and CLV measurement
```

It does not mean:

- guaranteed profitability;
- live-money execution;
- operator-overridden promotion;
- qualification based only on hit rate;
- qualification based on synthetic `-110` returns;
- qualification based on a small favorable sample.

---

# Repository and Safety Rules

Work on:

```text
rebuild/clean-slate-v1
```

Do not merge into `main`.

Do not:

- place orders;
- call real execution adapters;
- settle or alter existing production ledgers;
- overwrite incumbent models or calibration artifacts;
- rewrite historical source data;
- use post-event information in pregame features;
- silently substitute missing inputs with zero or league averages;
- use midpoint prices as executable entries;
- use reconstructed historical prices;
- claim profitability without prospective cost-adjusted evidence.

All new work must remain under:

```text
data/rebuild/
outputs/rebuild/
config/models/challengers/
```

Read before changing code:

```text
ARCHITECTURE.md
AGENTS.md
MASTER.md
README.md
docs/REBUILD_PLAN.md
docs/AI_REBUILD_GUIDE.md
docs/PROJECT_STATUS.md
outputs/rebuild/current_system_inventory.json
outputs/rebuild/current_system_audit.md
outputs/rebuild/source_coverage.md
```

Documentation is not evidence that a feature works. Verify every claim by tracing imports and call sites, running the actual pipeline, inspecting generated artifacts, and executing tests.

At the beginning of the takeover, record:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline -15
python --version
```

Create and maintain:

```text
outputs/rebuild/takeover_status.md
```

It must include:

- current branch;
- current Git SHA;
- dirty working-tree state;
- dependency versions;
- exact commands run;
- test results;
- lint results;
- type-check results;
- current checkpoint;
- known blockers;
- next executable command.

Commit and push small verified checkpoints. Do not leave the only working implementation unpushed.

---

# Non-Negotiable Betting Strategy

## Primary product strategy: winner-first value betting

The system must not operate as an unrestricted "bet whichever side has the largest apparent edge" engine.

The primary strategy is:

1. Use the sports-only model to determine the most likely winner.
2. Freeze that predicted winner before inspecting market prices.
3. Evaluate only team markets aligned with the frozen predicted winner.
4. Bet only when an aligned market is undervalued after uncertainty, fees, slippage, liquidity, and a safety margin.
5. Otherwise return `NO_BET` with exactly `0.0` units.
6. Never recommend the less-likely opponent solely because the opponent appears underpriced.

Example:

```text
Sports model:
Team A: 60%
Team B: 40%

Executable market:
Team A: 70¢
Team B: 30¢
```

Required result:

```text
Predicted winner: Team A
Team A raw edge: 60% - 70% = -10%
Decision: NO_BET
Units: 0.0
```

Do not produce:

```text
Bet Team B because Team B is priced at 30¢
```

That may exist only as a separately labeled unrestricted-EV research benchmark. It may never replace the primary operator strategy.

Positive example:

```text
Calibrated Team A probability: 60%
Conservative Team A probability: 57%
Depth-adjusted executable price: 52¢
Fees, slippage, and safety margin: 2%
Cost-adjusted edge: 57% - 52% - 2% = +3%
```

This market may qualify, subject to freshness, depth, exact contract matching, expected value, and portfolio limits.

## Moneyline policy

- The candidate team must equal `predicted_winner`.
- Market price must not influence which team is labeled the predicted winner.
- An overpriced winner produces `NO_BET`.
- The opposing moneyline may not become the primary recommendation.

## Spread policy

- The selected spread must belong to `predicted_winner`.
- Exact team, line, market ID, event ID, and contract semantics must match.
- The system may choose a winner-aligned spread over the winner moneyline when the spread has stronger qualified value.
- It may not select the opposing team's spread merely because that side has a larger apparent edge.

## Totals policy

Totals are independent of game-winner alignment.

For each exact total line:

1. Use the sports-only joint score distribution to determine whether `OVER`, `UNDER`, or neither is more likely.
2. Freeze the more probable totals side before inspecting market price.
3. Bet only if that frozen side clears the conservative executable-value gate.
4. Otherwise return `NO_BET` with zero units.

## Required separation

Represent the workflow using separate immutable objects:

```python
@dataclass(frozen=True)
class SportsForecast:
    event_id: str
    predicted_winner: str
    raw_probabilities: dict[str, float]
    calibrated_probabilities: dict[str, float]
    probability_lower: dict[str, float]
    probability_upper: dict[str, float]
    expected_home_score: float
    expected_away_score: float
    model_artifact_hash: str
    calibration_artifact_hash: str


@dataclass(frozen=True)
class MarketEvaluation:
    market_id: str
    market_type: str
    team_or_side: str
    line: float | None
    executable_ask: float
    depth_adjusted_price: float
    conservative_probability: float
    cost_adjusted_edge: float
    expected_value: float
    quote_age_seconds: float
    available_depth: float


@dataclass(frozen=True)
class BetDecision:
    event_id: str
    action: Literal["BET", "NO_BET"]
    predicted_winner: str
    selected_market: MarketEvaluation | None
    units: float
    reason_code: str
```

The sports probability must not be rewritten by the market layer.

## Conservative probability

The paper decision layer must use a validated lower-bound estimate incorporating:

- bootstrap uncertainty;
- calibration uncertainty;
- model-family disagreement;
- lineup and player uncertainty;
- missing-data penalty;
- data freshness;
- source conflicts.

Conceptually:

```python
conservative_probability = lower_probability_bound(
    calibrated_probability=calibrated_probability,
    bootstrap_uncertainty=bootstrap_uncertainty,
    calibration_uncertainty=calibration_uncertainty,
    lineup_uncertainty=lineup_uncertainty,
    missingness_penalty=missingness_penalty,
    model_disagreement=model_disagreement,
)
```

## Executable value

The system must calculate:

```python
cost_adjusted_edge = (
    conservative_probability
    - depth_adjusted_fill_price
    - fees
    - expected_slippage
    - required_safety_margin
)
```

A paper bet may proceed only when:

```text
candidate is aligned with the frozen predicted side
cost_adjusted_edge >= configured minimum
expected value > 0
quote is fresh
market is open
contract matching succeeds
depth is sufficient
portfolio limits pass
correlation limits pass
```

Zero is the default valid position size. There must be no mandatory one-unit floor.

---

# PART 1 — CLEAN-SLATE REPOSITORY, DATA, AND FEATURE REBUILD

Part 1 creates a reproducible, point-in-time data system. Do not begin serious model promotion work until Part 1 passes its correctness gates.

## 1. Audit the current repository completely

Read and trace every:

- source module;
- configuration file;
- script;
- test;
- model artifact;
- calibration artifact;
- model card;
- data manifest;
- workflow;
- ledger interface;
- daily job;
- dashboard write path.

Determine through import and call tracing:

- what actually runs during `daily`;
- what is dead code;
- what is shadow-only;
- which models write to which ledgers;
- which artifact is loaded for every sport and market;
- which calculated features are actually consumed;
- where train-serving definitions differ;
- where neutral imputation disguises missing data;
- where thresholds are hardcoded;
- where model parameters are hardcoded;
- where future or retrospective information can leak;
- where competing implementations exist for the same concept;
- where documentation disagrees with code;
- where market information is mixed into sports probability;
- which performance outputs use real executable prices;
- which performance outputs are hypothetical.

Generate:

```text
outputs/rebuild/current_system_inventory.json
outputs/rebuild/current_system_audit.md
outputs/rebuild/current_model_baselines.parquet
outputs/rebuild/current_model_baselines_summary.json
```

The baseline table must reproduce every incumbent model's actual historical predictions and metrics before replacement.

Required baseline fields:

```text
event_id
decision_time_utc
sport
market_type
selection
line
raw_probability
calibrated_probability
artifact_path
artifact_hash
calibration_hash
feature_schema_version
code_revision
prediction_status
actual_result
log_loss_component
brier_component
legacy_decision
legacy_units
```

Do not manually edit Git SHA, test counts, or dependency versions into audit reports. Generate them through an executable audit script.

Missing required artifacts must cause the audit command to fail.

## 2. Preserve an isolated rebuild

All rebuilt data and artifacts remain separate:

```text
data/rebuild/raw/
data/rebuild/normalized/
data/rebuild/features/
data/rebuild/markets/
data/rebuild/metadata.db
data/rebuild/shadow.db
outputs/rebuild/
config/models/challengers/
```

Freeze incumbent artifacts byte-for-byte. Record their hashes before making changes.

Do not overwrite:

```text
config/models/<incumbent>.json
```

New artifacts must be written to:

```text
config/models/challengers/<model>.json
```

Every model artifact must record:

```text
model_name
model_version
sport
market
horizon
training_start
training_end
feature_schema_version
dataset_hash
split_manifest_hash
code_revision
dependency_lock_hash
artifact_hash
```

## 3. Establish one reproducible Python environment

Retain:

- NumPy
- pandas
- SciPy
- scikit-learn
- Pydantic
- httpx
- joblib

Add after compatibility testing:

- XGBoost
- PyBaseball
- Polars
- PyArrow
- DuckDB
- Pandera
- statsmodels
- Optuna only for bounded chronological optimization

Resolve all Python-version inconsistencies.

Test supported versions, select one runtime, and make these agree:

```text
pyproject.toml requires-python
Ruff target-version
mypy python_version
CI runtime
lockfile
README instructions
reproduction commands
```

A fresh clone must successfully run:

```bash
pip install -e ".[dev]"
python -c "import model_prediction.rebuild"
pytest -q
ruff check src tests
mypy src/model_prediction
```

Commit a dependency lockfile.

Add CI for the rebuild branch. CI must install from the declared environment, not rely on a cached developer virtual environment.

## 4. Build immutable medallion storage

Use:

```text
data/rebuild/raw/
data/rebuild/normalized/
data/rebuild/features/
data/rebuild/markets/
data/rebuild/metadata.db
```

### Raw storage

Store provider responses as immutable compressed snapshots:

```text
data/rebuild/raw/{source}/{date}/{record_id}/{observed_at}_{sha256}.json.gz
```

A refreshed response is a new snapshot.

Do not allow:

```python
allow_refresh=True
```

to overwrite an existing snapshot.

Required properties:

- atomic writes;
- canonical JSON serialization;
- SHA-256 content hash;
- idempotent identical writes;
- changed payload creates new snapshot;
- partial writes are invalid;
- earlier snapshots remain unchanged.

### Normalized storage

Use canonical Parquet tables with explicit primary keys.

Examples:

```text
MLB scoreboard:
(event_id, observed_at_utc, source, source_record_id)

Statcast pitch:
(game_pk, at_bat_number, pitch_number, source_version)

Market book:
(market_id, side, line, observed_at_utc)

Roster:
(team_id, player_id, effective_at_utc, observed_at_utc)
```

Repeated collection must not duplicate rows.

Conflicting rows sharing an immutable primary key but carrying different source hashes must fail closed and create an audit event.

### Metadata storage

Use SQLite for:

- schemas;
- source registry;
- source-health state;
- entity mappings;
- dataset manifests;
- model metadata;
- calibration metadata;
- test-consumption state;
- audit events.

Excel may be generated only as a report or compatibility export.

## 5. Standard provenance

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

Add:

```text
publication_time_utc
supersedes_record_id
conflict_count
```

where relevant.

A decision at time `T` may only use observations with:

```text
observed_at_utc <= T
```

## 6. Build one strict as-of join utility

Create one shared implementation used by all rebuilt feature builders.

Example interface:

```python
def point_in_time_join(
    decisions: pl.DataFrame,
    observations: pl.DataFrame,
    *,
    entity_keys: list[str],
    decision_time_col: str,
    observation_time_col: str = "observed_at_utc",
    max_age: timedelta | None = None,
) -> pl.DataFrame:
    ...
```

Required invariants:

```text
selected observation occurred on or before decision timestamp
newest valid prior observation is selected
stale observations become explicit missingness
future observations fail the test
historical corrections appear only after publication
```

Tests must cover:

- same-day games;
- doubleheaders;
- timezone boundaries;
- traded players;
- injury-report updates;
- probable-pitcher changes;
- late lineup confirmation;
- weather-forecast revisions;
- market-book updates;
- retroactive source corrections.

Never join only by event date when a publication timestamp exists.

## 7. Build canonical identity

Create stable identities for:

- events;
- teams;
- players;
- rosters;
- venues;
- leagues;
- competitions;
- tournaments;
- market contracts.

Persist cross-source IDs and effective dates.

Fuzzy matching may suggest a mapping but may not authorize one automatically below a configured confidence threshold.

Low-confidence identity must fail closed.

Tests must cover:

- traded players;
- duplicate player names;
- aliases;
- accented names;
- team relocations;
- renamed teams;
- organization versus roster identity in esports;
- identical spread or total lines on unrelated games;
- doubleheaders;
- neutral-site games.

## 8. Build source collectors

Every collector must be:

- restartable;
- idempotent;
- rate-limited;
- schema-tested;
- provenance-complete;
- explicit about partial failure;
- free of broad exception swallowing;
- capable of resuming from a manifest.

### MLB

Use:

- ESPN;
- MLB StatsAPI;
- PyBaseball;
- Baseball Savant and Statcast;
- archived Open-Meteo forecast data;
- Polymarket US public market books.

Split collectors where useful:

```text
rebuild/collectors/mlb/scoreboard.py
rebuild/collectors/mlb/statcast.py
rebuild/collectors/mlb/rosters.py
rebuild/collectors/mlb/probables.py
rebuild/collectors/mlb/weather.py
rebuild/collectors/mlb/markets.py
rebuild/collectors/mlb/orchestrator.py
```

### NBA and WNBA

Use SportsDataverse and existing public ESPN/NBA/WNBA sources for:

- play-by-play;
- substitutions;
- stints;
- rosters;
- box scores;
- shots;
- officials;
- player availability;
- schedules.

Prospectively archive every injury and availability report.

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
- prospective roster availability;
- public market books.

Record source coverage by competition.

Never substitute another competition's distribution without an explicit hierarchical model.

### Tennis

Use Jeff Sackmann ATP and WTA identities, rankings, match results, and match statistics.

Prospectively capture:

- tournament;
- surface;
- schedule;
- withdrawal;
- market snapshots.

### Esports

Retain BO3 as a replaceable results source.

Add after terms and stability review:

- Valve VRS for CS2;
- OpenDota for Dota 2;
- Oracle's Elixir-style LoL enrichment where licensing permits;
- effective-dated roster archives;
- patch;
- tournament;
- map;
- veto;
- side;
- draft;
- best-of format.

Each title remains independent.

### KBO and NPB

Retain official schedule and result collectors.

Investigate stable point-in-time sources for:

- starting pitchers;
- lineups;
- bullpen;
- park;
- weather;
- roster rules.

When reliable inputs are unavailable, restrict the model rather than inventing MLB-equivalent features.

## 9. Build separate decision horizons

Create separate datasets for:

```text
early: 24–48 hours before event
mid:   4–8 hours before event
late:  15–90 minutes before event
```

For the first deterministic MLB implementation, use:

```text
early: start minus 36 hours
mid:   start minus 6 hours
late:  start minus 60 minutes
```

Each horizon receives a separate:

- feature schema;
- dataset;
- model;
- calibrator;
- coverage report;
- missingness report;
- predictive evaluation;
- economic evaluation.

Late information must never be backfilled into early decisions.

## 10. Build sport-specific feature stores

### MLB

#### Starting pitcher

- K%;
- BB%;
- K-BB%;
- CSW%;
- swinging-strike rate;
- xwOBA;
- xERA;
- xFIP;
- SIERA-style state where reproducible;
- ground-ball rate;
- barrel rate;
- hard-hit rate;
- pitch mix;
- velocity;
- movement;
- recent changes;
- expected pitch count;
- expected innings;
- rest;
- recent workload;
- times-through-order penalty;
- opener or bulk probability;
- handedness;
- sample size;
- uncertainty.

#### Pitcher clean-rate group

Do not use one universal athlete clean rate.

For MLB pitchers calculate:

- first-inning clean rate;
- scoreless-inning rate;
- clean-appearance rate;
- rolling 10-appearance rate;
- rolling 20-appearance rate;
- season rate;
- multiseason rate;
- opponent adjustment;
- beta-binomial posterior mean;
- posterior variance;
- numerator;
- denominator;
- effective sample.

#### Lineup

- projected batting order;
- confirmed batting order;
- plate-appearance weights;
- xwOBA;
- K%;
- BB%;
- barrel rate;
- hard-hit rate;
- contact rate;
- platoon splits;
- pitch-family matchup;
- baserunning;
- missing regulars;
- projected-versus-confirmed difference;
- uncertainty.

#### Bullpen

- reliever-level quality;
- pitches over 1/2/3/5/7 days;
- appearances over 1/2/3/5/7 days;
- consecutive-use flags;
- leverage role;
- expected availability;
- handedness balance;
- expected available innings.

#### Environment

- season-versioned park factors;
- roof state;
- temperature;
- humidity;
- dew point;
- pressure;
- precipitation;
- field-relative wind vector;
- forecast generation time;
- forecast valid time;
- forecast age;
- provider-model disagreement.

#### Schedule and availability

- travel;
- rest;
- prior-game workload;
- doubleheader;
- player availability;
- source conflict;
- freshness.

### NBA and WNBA

Build:

- expected possessions;
- opponent-adjusted pace;
- offensive and defensive Four Factors;
- half-court efficiency;
- transition efficiency;
- projected minutes;
- player availability;
- replacement minutes;
- ridge or elastic-net RAPM-style impact;
- starting-lineup strength;
- closing-lineup strength;
- lineup continuity;
- shot-location profile;
- rest;
- travel;
- altitude;
- officials;
- foul environment;
- rotation uncertainty.

Train NBA and WNBA independently.

Use stronger shrinkage for WNBA player and low-minute lineup effects.

### NFL

Build:

- quarterback identity;
- probability quarterback starts;
- early-down pass EPA;
- early-down rush EPA;
- success rate;
- CPOE;
- pressure response;
- sack response;
- scramble response;
- offensive-line continuity;
- receiver availability;
- defensive availability;
- pace;
- pass rate over expectation;
- expected drives;
- red-zone state;
- explosive-play state;
- fourth-down aggressiveness;
- weather;
- roof;
- surface;
- kicker;
- special teams.

### Soccer

Build:

- dynamic attack strength;
- dynamic defense strength;
- xG for and against where covered;
- non-penalty xG;
- shot quality;
- set-piece strength;
- lineup availability;
- goalkeeper availability;
- league strength;
- competition strength;
- roster continuity;
- schedule congestion;
- home or neutral venue;
- learned recency;
- source-coverage uncertainty.

### Tennis

Build:

- overall rating;
- surface rating;
- serve points won;
- return points won;
- first-serve state;
- second-serve state;
- break-point performance with shrinkage;
- opponent-adjusted serve and return;
- surface transition;
- inactivity;
- recent workload;
- travel;
- age;
- format;
- indoor or outdoor;
- retirement risk;
- withdrawal risk;
- sample-size uncertainty.

### Esports

Per title:

- effective-dated roster;
- player ratings;
- roster tenure;
- region strength;
- tournament strength;
- inactivity;
- schedule;
- patch;
- best-of format;
- LAN or online.

CS2:

- map pool;
- map strength;
- veto order;
- T/CT side;
- exact five-player roster.

LoL:

- role and player strength;
- side;
- patch;
- post-draft composition only after draft is known.

Dota 2:

- roster;
- player strength;
- patch;
- hero pool;
- draft state by horizon;
- side.

### KBO and NPB

Use league-specific:

- starter quality;
- expected starter innings;
- lineup quality;
- bullpen availability;
- park;
- weather;
- tie-generating process;
- league roster rules where measurable.

Do not transfer MLB coefficients.

## 11. Missingness is data

Every feature group must include:

```text
observed value
availability flag
source
observed timestamp
observation age
missing reason
conflict count
sample size
uncertainty
raw snapshot hashes
```

Never silently fill required values with zero or league average.

Models may use explicit missingness indicators, but reports must show when missingness itself becomes the dominant predictive signal.

## 12. Part 1 tests

Add tests for:

- future leakage;
- same-day leakage;
- historical corrections;
- train-serving parity;
- raw hash stability;
- schema drift;
- duplicate events;
- duplicate pitches;
- player identity;
- roster identity;
- stale reports;
- source conflicts;
- collector failure;
- restartability;
- deterministic features;
- horizon separation;
- market timestamp validity;
- normalized idempotency;
- immutable raw snapshots;
- as-of join correctness.

Part 1 is complete only when every sport has:

```text
documented data contract
reproducible feature build
coverage report
missingness report
honest unavailable-feature list
```

For tonight's operational target, MLB must fully satisfy this gate before expansion to other sports.

---

# PART 2 — SPORT-SPECIFIC DISTRIBUTIONS, CHALLENGERS, ENSEMBLING, AND CALIBRATION

Continue only after Part 1's point-in-time, identity, storage, and train-serving tests pass.

No challenger may be promoted or executed with real money.

## 1. Modeling principles

Every sport must eventually have:

1. transparent control model;
2. sport-specific statistical distribution;
3. regularized linear challenger;
4. nonlinear scikit-learn challenger;
5. XGBoost challenger;
6. chronological out-of-fold ensemble;
7. independently fitted calibrator;
8. untouched final test;
9. prospective replacement test after final-test consumption.

Rank primarily using:

1. log loss;
2. Brier score;
3. calibration intercept;
4. calibration slope;
5. reliability curves;
6. ECE;
7. distributional likelihood;
8. score and interval accuracy;
9. coverage and stability.

Directional accuracy is secondary.

## 2. Nested chronological validation

Use expanding or rolling folds grouped by complete event dates.

For every fold:

- train on past data only;
- use an embargo when publication timing requires it;
- tune on the next chronological block;
- generate validation-only predictions;
- never update ratings with another same-day event before predicting all events on that date;
- preserve one untouched final test;
- mark final tests consumed after evaluation.

Do not use random K-fold cross-validation.

Do not inspect the final test while selecting:

- features;
- model family;
- hyperparameters;
- ensemble weights;
- calibrator;
- confidence threshold;
- decision threshold.

Persist a split manifest:

```json
{
  "sport": "mlb",
  "horizon": "late",
  "dataset_hash": "...",
  "folds": [
    {
      "train_start": "...",
      "train_end": "...",
      "embargo_start": "...",
      "embargo_end": "...",
      "validation_start": "...",
      "validation_end": "..."
    }
  ],
  "final_test_start": "...",
  "final_test_end": "...",
  "final_test_consumed": false
}
```

Use date-cluster and team-cluster bootstrap intervals.

## 3. Common challengers

### Regularized generalized linear models

Use scikit-learn pipelines with:

- transformations fitted only on training rows;
- explicit missingness indicators;
- L1;
- L2;
- elastic net;
- predeclared interactions;
- chronological tuning.

### Histogram gradient boosting

Use conservative:

- leaf count;
- depth;
- learning rate;
- minimum leaf sample;
- L2 regularization;
- chronological early stopping;
- native missing-value handling.

### XGBoost

Use XGBoost as a challenger—not automatic production truth.

Use:

- shallow trees;
- low learning rate;
- `hist` tree method;
- strong `min_child_weight`;
- L1 and L2 penalties;
- row subsampling;
- feature subsampling;
- chronological validation;
- deterministic seed;
- bounded threads;
- interaction constraints where justified;
- monotonic constraints only where defensible;
- early stopping.

Persist the best iteration.

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

## 4. MLB architecture

Build two separate latent heads.

### Absolute run-intensity head

Predict total scoring environment using:

- both lineups;
- both starters;
- expected starter innings;
- bullpen availability;
- park;
- roof;
- weather;
- catcher where available;
- umpire where available;
- league and season run environment.

### Relative run-strength head

Predict which team owns the scoring advantage using:

- lineup differential;
- starter differential;
- bullpen differential;
- defense;
- handedness;
- pitch-mix matchup;
- home advantage.

Reconcile into:

```text
away expected runs
home expected runs
```

Test:

- independent Poisson;
- latent-Gamma Poisson;
- negative binomial;
- correlated or bivariate count simulation.

Derive from the same joint distribution:

- home moneyline;
- away moneyline;
- run line at supported exact lines;
- total at supported exact lines;
- push probabilities;
- expected score;
- score quantiles;
- prediction intervals.

No disconnected MLB moneyline classifier may silently contradict the joint score distribution. It may remain only as a clearly labeled challenger.

## 5. NBA and WNBA architecture

Estimate:

```text
expected possessions
×
lineup-adjusted points per possession
```

Build separate home and away efficiency estimates using:

- team offense;
- team defense;
- projected minutes;
- player impact;
- availability;
- Four Factors;
- shot-profile matchup;
- rest;
- travel;
- uncertainty.

Generate a coherent joint score distribution and derive:

- moneyline;
- spread;
- total;
- expected margin;
- expected total.

Train NBA and WNBA separately.

## 6. NFL architecture

Estimate:

- expected drives;
- distribution of drive outcomes;
- expected score by team.

Drive outcomes should include:

- no score;
- field goal;
- touchdown;
- safety or rare outcome where data permits.

Condition on:

- quarterback;
- early-down efficiency;
- protection;
- pressure;
- pace;
- field-position ability;
- injuries;
- coaching regime;
- weather;
- special teams.

Simulate coherent final scores and derive moneyline, spread, and total.

Retain Elo only as a prior or benchmark.

## 7. Soccer architecture

Keep the existing fixed Poisson–Dixon–Coles model as Control A.

Build:

- learned attack;
- learned defense;
- learned home advantage by league;
- learned time decay;
- learned low-score dependence;
- hierarchical league strength;
- promoted and relegated priors;
- lineup state;
- goalkeeper state;
- xG enrichment where covered;
- negative-binomial challengers where overdispersion exists.

Generate one coherent score matrix for:

- three-way moneyline;
- totals;
- BTTS;
- exact handicap contracts where semantics are known.

## 8. Tennis architecture

Controls:

- overall Elo;
- surface Elo;
- incumbent fixed blend.

Challengers:

- optimized surface blend;
- Glicko or uncertainty-aware ratings;
- Bradley–Terry;
- serve and return logistic model;
- point-level probability converted through tennis scoring;
- XGBoost on player-state differences.

Derive match probability from point, game, set, and match format where data permits.

## 9. Esports architecture

Build independent title-specific models.

### LoL

Model game probability using:

- roster;
- player and role strength;
- region;
- tournament;
- patch;
- side;
- form;
- roster tenure.

Add draft features only after the draft is known.

Convert game probability to series probability.

### CS2

Model map probability using:

- exact roster;
- VRS or rating prior;
- map strength;
- veto;
- side;
- LAN or online;
- event tier;
- travel;
- patch and map-pool era.

Simulate the series.

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

Keep research-only until stable point-in-time roster, map, patch, and tournament coverage exists.

## 10. KBO and NPB architecture

Controls:

- tie-aware Elo;
- league-specific home Elo.

Challengers:

- league-specific starter, lineup, bullpen, park, and weather score distribution;
- tie probability derived from that score distribution.

Do not use manually shaped Elo-gap tie probability when a coherent count model is available.

## 11. Feature ablation

Predeclare feature groups.

Run:

- isolated ablation;
- cumulative ablation;
- leave-one-group-out ablation.

Report:

- log-loss change;
- Brier change;
- calibration change;
- coverage change;
- fold stability;
- seasonal stability;
- missingness sensitivity;
- bootstrap interval;
- importance stability;
- live availability.

Reject features that:

- help only after viewing final test;
- use post-event data;
- work in one short period;
- have unstable signs;
- destroy coverage;
- act mostly as missingness proxies;
- cannot be reproduced during live inference.

## 12. Player-rate shrinkage

Every rate must include:

```text
numerator
denominator
raw rate
prior
posterior mean
posterior variance
effective sample
recency weighting
opponent adjustment where justified
```

Use:

- beta-binomial for binary clean-rate statistics;
- empirical-Bayes or hierarchical shrinkage for continuous player metrics.

## 13. Out-of-fold ensemble

Generate chronological OOF predictions from:

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

The stacker may see only OOF predictions.

Prefer the simplest stable ensemble.

## 14. Independent calibration

Compare:

- identity;
- sigmoid or Platt;
- isotonic when sample size supports it;
- temperature scaling;
- beta calibration if implemented and validated.

Fit calibration only on predictions independent of base-model fitting.

Store base model and calibrator as separately hashed, mutually bound artifacts.

Use sport-, market-, and horizon-specific calibrators unless pooled calibration demonstrably improves small-sample performance.

## 15. Uncertainty output

Every prediction must report:

- raw probability;
- calibrated probability;
- model-family dispersion;
- bootstrap interval;
- feature-missingness penalty;
- player and lineup uncertainty;
- lower and upper probability;
- data freshness;
- sample size.

Uncertainty must later affect the decision layer.

## 16. Part 2 deliverables

Create:

```text
outputs/rebuild/model_benchmark.parquet
outputs/rebuild/model_benchmark.md
outputs/rebuild/feature_ablation.parquet
outputs/rebuild/calibration_report.md
outputs/rebuild/test_consumption_registry.json
config/models/challengers/<model>.json
```

No challenger may be described as profitable in Part 2.

Part 2 establishes probability quality only.

---

# PART 3 — MARKET RESIDUALS, EXECUTABLE PROFITABILITY, SHADOW DEPLOYMENT, AND PRODUCTION SAFETY

Continue only after Part 2 freezes its predictive models and calibrators.

Do not submit real orders.

## 1. Separate the three questions

The system must answer independently:

1. What is the sports-only probability?
2. Is the timestamp-valid market price wrong?
3. Is the disagreement large enough to trade after costs and risk?

Do not insert market prices into the sports-only probability model.

## 2. Capture executable evidence

For every event and horizon, prospectively store:

```text
exact event ID
exact market ID
exact contract ID
exact side
exact line
contract wording
settlement rules
observed timestamp
event start
best bid
best offer
multiple bid levels
multiple ask levels
available quantities
market state
quote age
order-book hash
first observed quote
later quotes
closing quote
settlement
```

Use Polymarket US public BBO and order-book data.

Do not use:

- indicative event-list prices;
- midpoint as executable ask;
- postgame prices;
- reconstructed entries;
- closing prices as entries;
- generic `-110`;
- another line;
- another event with the same line;
- in-play data for a pregame test.

## 3. Walk the order book

Implement:

```python
def walk_asks(
    levels: list[BookLevel],
    requested_contracts: int,
) -> FillEstimate:
    ...
```

Return:

```text
contracts requested
contracts filled
average fill price
worst fill price
unfilled contracts
slippage
depth sufficient
```

Use average fill price for the proposed quantity.

## 4. Calculate executable fair value

Store separately:

```text
model probability
calibrated probability
conservative probability
best ask
depth-adjusted fill price
opposing-side book
no-vig market estimate
spread
fees
expected slippage
raw edge
cost-adjusted edge
expected value
```

A positive model-minus-midpoint difference is not tradeable evidence.

## 5. Build a separate market-residual model

Possible inputs:

- calibrated sports probability;
- conservative probability;
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
- positive CLV;
- expected return;
- probability disagreement is genuine.

Use chronological OOF validation.

Compare against:

- no trade;
- raw-edge threshold;
- uncertainty-adjusted threshold;
- market-only baseline.

The residual model may not rewrite the sports probability or change the frozen predicted winner.

## 6. Dual qualification

### Predictive qualification

Require:

- improved or non-inferior log loss;
- improved or non-inferior Brier;
- acceptable calibration;
- stable reliability;
- sufficient coverage;
- zero point-in-time violations;
- train-serving parity;
- stability across seasons and cohorts.

### Economic qualification

Require:

- real executable quotes;
- modeled spread, fees, and slippage;
- sufficient independent events and dates;
- positive cost-adjusted return;
- positive or non-negative CLV;
- bootstrap uncertainty;
- acceptable drawdown;
- stable price and edge buckets;
- no single team, month, or market driving performance;
- adequate depth;
- successful exact contract matching.

Allowed statuses:

```text
REJECTED
RESEARCH_ONLY
PREDICTIVELY_QUALIFIED
ECONOMIC_SAMPLE_INSUFFICIENT
ECONOMICALLY_QUALIFIED_FOR_SHADOW
ELIGIBLE_FOR_SEPARATE_LIVE_REVIEW
```

A predictively strong model may remain economically unusable.

## 7. Threshold selection

Select trade thresholds only on an economic validation set.

Possible threshold dimensions:

- lower-bound edge;
- expected value;
- residual score;
- quote age;
- liquidity;
- model uncertainty;
- missingness;
- horizon.

Freeze the selected threshold and apply it once to the untouched economic test.

Record every threshold trial.

Do not report only the best threshold after testing many alternatives.

## 8. Position sizing

Zero is the default valid size.

Remove any mandatory minimum stake.

Compare:

- flat stake;
- fixed fractional Kelly;
- capped fractional Kelly;
- uncertainty-adjusted Kelly;
- no trade.

Use conservative probability.

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

## 9. Correlation

Track shared risk among:

- moneyline and spread for the same team;
- total and pitcher or lineup-derived positions;
- multiple positions in one event;
- repeated exposure to one team;
- weather-driven games;
- model-family defects;
- related series markets.

Report:

```text
nominal exposure
correlation-adjusted exposure
```

## 10. Economic evaluation

For every sport, model, market, and horizon report:

```text
opportunities
accepted trades
rejected trades by reason
turnover
average entry
average spread
depth
raw edge
conservative edge
expected value
ROI
P&L
CLV
drawdown
volatility
win rate
average win
average loss
bootstrap interval
probability ROI exceeds zero
month
team
price bucket
edge bucket
liquidity bucket
missingness cohort
horizon
```

Clearly distinguish:

```text
real quoted fill
depth-adjusted simulated fill
top-of-book hypothetical fill
unfillable opportunity
```

## 11. Stress testing

Rerun the frozen paper portfolio with:

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

Reprice each trade rather than merely multiplying historical P&L by an arbitrary percentage.

A system that fails after minor realistic degradation remains research-only.

## 12. Prospective deployment stages

### Stage 1 — Retrospective predictive research

No ledger writes and no orders.

### Stage 2 — Prospective shadow

Freeze predictions before start, capture books, settle, and measure CLV.

No orders.

### Stage 3 — Conservative paper portfolio

Apply frozen thresholds and sizing to simulated fills.

No orders.

### Stage 4 — Live-review eligibility

Outside this task.

Requires separate explicit authorization after sufficient prospective evidence.

## 13. Monitoring

Monitor:

- source health;
- schema drift;
- missingness;
- feature drift;
- calibration;
- Brier;
- log loss;
- model disagreement;
- contract-match failure;
- quote latency;
- CLV;
- ROI;
- drawdown;
- rejected opportunities;
- liquidity.

Health states:

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

Do not retrain automatically because of a short losing run.

## 14. SQLite shadow ledger

Create append-only SQLite tables for:

```text
raw_snapshots
normalized_observations
feature_snapshots
dataset_manifests
model_versions
calibration_artifacts
predictions
market_snapshots
trade_decisions
paper_orders
settlements
closing_prices
reviews
audit_events
```

Original predictions, quotes, and decisions are immutable.

Corrections create superseding records.

Generate Excel from SQLite for operator review. Excel is not the authoritative database.

Use adapters so the existing dashboard, settlement workflow, exact-contract matching, and audit interfaces continue to work during migration.

## 15. One-command production-ready shadow operation

Expose a safe command such as:

```bash
model-prediction rebuild-mlb-shadow \
  --date YYYY-MM-DD \
  --horizon late
```

The command must execute:

```text
collect
normalize
build features
load frozen model
load calibrator
predict score distribution
freeze predicted winner
freeze totals side
capture exact markets
walk order-book depth
apply winner-first value policy
persist prediction
persist BET or NO_BET
produce operator summary
```

Rerunning the same timestamped job must be idempotent.

Every event must persist a decision, including no-bets.

Operator output must include:

```text
event
predicted winner
raw probability
calibrated probability
conservative probability
expected score
exact market
exact line
best ask
depth-adjusted fill price
cost-adjusted edge
decision
units
reason
missingness warnings
model version
quote timestamp
```

## 16. Part 3 deliverables

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
outputs/rebuild/takeover_status.md
```

Each model card must include:

- blunt verdict;
- model and calibrator hashes;
- sample size;
- predictive metrics;
- calibration;
- economic metrics;
- CLV;
- uncertainty;
- largest risk;
- qualification status;
- next action.

Never claim profitability from:

- hit rate alone;
- hypothetical `-110` units;
- midpoint prices;
- operator override;
- consumed holdout;
- small favorable streak;
- shadow P&L without executable quotes.

Profitability may be claimed only after a frozen model and frozen decision policy demonstrate positive prospective cost-adjusted performance with sufficient independent evidence.

---

# IMMEDIATE MLB PRODUCTION-READY SHADOW PLAN

Complete these checkpoints in order.

## Checkpoint 0 — Preflight

- Verify branch and SHA.
- Record dirty state.
- Run baseline tests.
- Run Ruff.
- Run mypy.
- Run audit-chain verification.
- Record incumbent artifact hashes.
- Confirm no production writers are being invoked.
- Update `takeover_status.md`.

## Checkpoint 1 — Environment

- Fix direct dependencies.
- Choose one Python version.
- Commit lockfile.
- Add CI.
- Prove fresh installation.

## Checkpoint 2 — Storage correctness

- Remove raw overwrite behavior.
- Add immutable versioned snapshots.
- Add normalized primary keys.
- Add conflict detection.
- Add atomic writes.
- Add strict as-of joins.
- Add schema validation.

## Checkpoint 3 — Frozen baseline

- Generate incumbent baseline Parquet.
- Regenerate audit at current head.
- Bind predictions to artifact hashes.
- Fail audit when required output is absent.

## Checkpoint 4 — MLB data

- Backfill real MLB data.
- Capture scoreboard, Statcast, roster, probable pitcher, weather, and market books.
- Make all collection restartable and idempotent.
- Produce source coverage report.

## Checkpoint 5 — MLB features

Build:

```text
early.parquet
mid.parquet
late.parquet
```

with real:

- starter;
- lineup;
- bullpen;
- park;
- weather;
- schedule;
- availability;
- clean-rate features.

Produce coverage and missingness artifacts.

## Checkpoint 6 — MLB modeling

Replace random-feature and invalid demonstration training.

Generate valid chronological OOF predictions from:

- control;
- statistical model;
- regularized linear model;
- HistGradientBoosting;
- XGBoost.

Fit ensemble and calibration independently.

Create and consume untouched final test only after freezing all choices.

## Checkpoint 7 — Decision engine

Implement central:

```text
sports forecast
market evaluation
BET or NO_BET
```

Enforce:

```text
predicted winner frozen before market
opponent cannot replace predicted winner
negative winner edge produces NO_BET
NO_BET has zero units
winner-aligned spread allowed
totals handled independently
```

## Checkpoint 8 — Market and shadow persistence

- Capture exact order books.
- Walk depth.
- Calculate realistic fill.
- Persist append-only predictions and decisions.
- Add SQLite shadow ledger.
- Prove no real order adapter was called.

## Checkpoint 9 — End-to-end run

Run one real current or next MLB slate.

Verify:

- every game receives a prediction;
- every game receives `BET` or `NO_BET`;
- overpriced winners become `NO_BET`;
- exact contract matching works;
- rerun is idempotent;
- operator report is complete.

## Checkpoint 10 — Evidence

Update every required report with executed evidence.

Do not describe unfinished architecture as completed operation.

---

# Required Focused Tests

Create:

```text
tests/rebuild/test_storage_immutability.py
tests/rebuild/test_normalized_idempotency.py
tests/rebuild/test_asof_joins.py
tests/rebuild/test_identity.py
tests/rebuild/test_mlb_collectors.py
tests/rebuild/test_mlb_feature_point_in_time.py
tests/rebuild/test_mlb_horizon_separation.py
tests/rebuild/test_mlb_train_serving_parity.py
tests/rebuild/test_mlb_chronological_oof.py
tests/rebuild/test_mlb_calibration_independence.py
tests/rebuild/test_winner_first_decision.py
tests/rebuild/test_order_book_walk.py
tests/rebuild/test_sqlite_shadow_persistence.py
tests/rebuild/test_mlb_shadow_e2e.py
```

Each corrected bug must include a regression test that fails against the pre-fix implementation.

Critical decision tests:

```text
market price cannot change predicted winner
60% winner at 70¢ returns NO_BET
NO_BET returns 0 units
60% winner at qualified price may return BET
opponent cannot be selected for larger apparent edge
winner-aligned spread may qualify
stale quote fails closed
insufficient depth fails closed
contract mismatch fails closed
totals freeze sports-only side before market evaluation
```

---

# Commit Sequence

Use small, auditable commits:

```text
1. build(rebuild): lock runtime and complete dependency declarations
2. fix(rebuild): enforce immutable raw and idempotent normalized storage
3. feat(rebuild): generate incumbent baseline and executable audit
4. feat(rebuild): complete MLB point-in-time collectors
5. feat(rebuild): build MLB early mid late feature stores
6. feat(rebuild): add valid chronological OOF training and calibration
7. feat(rebuild): enforce winner-first value-only decision policy
8. feat(rebuild): add order-book walking and SQLite shadow persistence
9. feat(rebuild): add one-command MLB shadow pipeline
10. docs(rebuild): publish verified evidence and reproduction commands
```

After every checkpoint run:

```bash
git status --short
pytest -q
ruff check src tests
mypy src/model_prediction
```

Record exact output in:

```text
outputs/rebuild/takeover_status.md
```

Push every verified checkpoint.

---

# Minimum Definition of Done for MLB Production-Ready Shadow

MLB is not complete until:

```text
[ ] Fresh clone installs from declared locked dependencies
[ ] Full tests pass
[ ] CI is attached to pushed head
[ ] Raw responses are immutable
[ ] Normalized collection is idempotent
[ ] Strict as-of joins are used
[ ] Frozen incumbent baseline exists
[ ] MLB early, mid, and late datasets exist
[ ] Coverage reports exist
[ ] Missingness reports exist
[ ] Real pregame features replace random or global-score demos
[ ] No point-in-time violations exist
[ ] Train-serving parity passes
[ ] Control and challengers generate genuine chronological OOF predictions
[ ] Ensemble uses OOF predictions only
[ ] Calibration uses independent predictions only
[ ] Final-test registry contains real date ranges
[ ] Final test is consumed once
[ ] Joint score distribution derives moneyline, spread, and total
[ ] Predicted winner is frozen before market inspection
[ ] Team markets are winner-aligned
[ ] 60% winner at 70¢ returns NO_BET and zero units
[ ] Zero is the default valid position size
[ ] Exact market and line matching works
[ ] Real order-book depth is captured
[ ] Proposed quantity walks the order book
[ ] SQLite stores append-only forecasts and paper decisions
[ ] One safe command runs the MLB shadow slate end to end
[ ] Rerun is idempotent
[ ] No real order is submitted
[ ] Reports distinguish operational, predictive, and economic status
[ ] Branch is committed and pushed
```

An operational pipeline may still remain:

```text
RESEARCH_ONLY
```

or:

```text
ECONOMIC_SAMPLE_INSUFFICIENT
```

That is acceptable.

Do not fabricate qualification to satisfy the deadline.

---

# Expansion Order After MLB

After MLB passes all three parts:

1. NBA
2. WNBA
3. NFL
4. Tennis
5. Soccer
6. CS2
7. LoL
8. Dota 2
9. KBO
10. NPB
11. Valorant
12. Rainbow Six

Valorant and Rainbow Six must remain research-only until stable roster, map, patch, and tournament coverage exists.

For every sport, repeat:

```text
data contract
point-in-time feature build
coverage and missingness report
control model
sport-specific distribution
linear challenger
nonlinear challenger
XGBoost challenger
OOF ensemble
independent calibration
untouched final test
prospective market capture
paper economic evaluation
```

---

# Final Claude Handoff Report

At completion, report:

1. exact branch and Git SHA;
2. commits created;
3. materially changed files;
4. exact commands run;
5. test results;
6. Ruff results;
7. mypy results;
8. CI result;
9. audit-chain result;
10. MLB data coverage by horizon;
11. feature coverage and missingness;
12. model benchmark;
13. calibration metrics;
14. final-test dates and consumption state;
15. current qualification status;
16. one real shadow-slate example;
17. one overpriced predicted winner correctly returning `NO_BET`;
18. proof that no real order was submitted;
19. remaining blockers for economic qualification;
20. one direct reproduction command.

Be blunt.

Separate:

```text
architecture implemented
pipeline executed
model trained
predictively qualified
economically qualified
live-review eligible
```

Do not treat these as equivalent.
