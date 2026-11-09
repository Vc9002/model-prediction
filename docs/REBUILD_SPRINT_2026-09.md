# Rebuild Sprint (2026-09-09 to 2026-09-14)

> Consolidated from: FORWARD_REBUILD_2026-09-09.md, GENERATION_2026-09-09.md, MODEL_ITERATION_2026-09-09.md, INCUMBENT_COMPARISON_2026-09-09.md, PAIRED_REBUILD_2026-09-10.md, RECENCY_AND_QUOTE_ITERATION_2026-09-11.md, CFB_JOINT_ITERATION_2026-09-11.md, NRFI_AND_SETTLED_REBUILD_2026-09-12.md, REBUILD_STATUS_2026-09-14.md, CONTROL_PLANE_CONSOLIDATION_2026-09-14.md. Archival merge, ordered chronologically by date — all content preserved.

---

## Part 1 — Forward Rebuild (source: FORWARD_REBUILD_2026-09-09.md)

# All-model forward inference and incumbent replay — updated 2026-09-10

Later checkpoint: all 41 forecasts now have outcomes, and exact paired
evaluation/preparation/capture are implemented. The earlier awaiting-outcome
description below is historical. See
[September 10 paired rebuild evidence](PAIRED_REBUILD_2026-09-10.md).

The 21 first-generation candidates now have a common fixture inference and
source-capture path. This is an engineering milestone, not evidence that all
21 outperform their incumbents. The earlier MLB comparisons remain negative
or inconclusive; no profitability or promotion claim changes.

## Shared features and archived forecast inputs

Theory: historical training and upcoming-event inference must use the same
target-free feature calculation. Previously only labeled `Game` rows could
reach the generic feature builder. `Fixture`, `FeatureState`, and
`fixture_features` now calculate inputs without fabricated scores or outcomes.
The existing two-calendar-day result embargo remains, with a separate cutoff
at the actual observation date for upcoming fixtures.

Evidence and verification:

- Thirteen sports exactly reproduce the generation's saved feature rows.
- CS2's refreshed source removed `bo3:128641`, reducing 34,356 rows to 34,355
  and changing ten subsequent common rows. The archived original feature code
  and the refactored code produce identical outputs on that same refreshed
  source. This is source drift, not a refactor discrepancy. Evidence lives in
  `outputs/research/feature_refactor_cs2_drift_20260909.json`.
- All 21 artifacts pass synthetic contract inference and exact replay tests:
  `outputs/research/forward_all21_smoke_20260909_v2/report.json`. These are
  explicitly synthetic fixtures, not predictive evaluation or real games.
- Forty-one upcoming contracts from existing local MLB/NFL scoreboards were
  captured before their listed starts: ten MLB games across four markets and
  one NFL moneyline. Every prediction rebuilds exactly from the archived
  source bytes. Evidence: `outputs/research/forward_capture_20260909_v1/`,
  particularly `predictions.jsonl` and `independent_source_rebuild.json`.

The common command is `scripts/forecast_research_generation.py`. It accepts
an explicit fixture JSONL and a generation directory, and creates a new output
directory containing the source files, fitted artifacts, recipes, full named
features, prediction snapshots, and per-market coverage. Sources changing
during capture are rejected. Existing output directories are not overwritten.
Partial failures remain visible and return exit code 2; an empty request fails.

Fixtures require timezone-qualified future starts and the historical store's
exact participant IDs. Tennis uses sorted player IDs within the declared tour;
esports preserves source team1/team2 orientation. Cold starts are unavailable.
Totals/spreads require an explicit full-game period and exact numeric line;
spreads require a signed home line. NRFI requires named teams, starters, venue,
the frozen generation's training priors, and the archived first-inning source.
Its existing live feature builder uses a conservative results embargo.

The local schedules were captured, not re-fetched. Their spread/total lines
are ESPN's saved full-game lines, not newly verified executable quotes. No
execution profitability is calculated. Capturing source bytes now proves
what was used for this calculation, not when older historical rows first
became observable. `rebuild_forecast` verifies the archived source hash,
rebuilds features, checks the complete prediction snapshot, and independently
replays the artifact. Recipe metadata must also match the recipe embedded in
the hash-verified joblib file.

Failure modes addressed: target leakage from dummy completed fixtures,
orientation errors, stale source substitution, missing inputs being silently
defaulted for generic models, contract-line ambiguity, and mismatched recipes.
Rollback consists of using the preserved generation code and prior research
artifacts; no production champion configuration or existing model artifact was
changed by this work.

## NCAAF incumbent state capture

The NCAAF incumbent uses a stateful simulation engine. A seed and projected
scores cannot reproduce later games in a slate: the random-number generator
has already advanced. Each new prediction now retains its full-precision score
means, exact distribution parameters, actual pre-simulation RNG state, and the
distribution engine's source hash.

`cfb_replay.py` reconstructs all three markets, including integer-line pushes,
from these inputs. Decision validation checks model/event identity, selected
probability, pregame timestamp, and the signed line for the selected side.
These snapshots pass through the forecast logger, canonical SQLite decision
and feature payloads, XLSX export, and settlement. The incumbent replay helper
recognizes them. Historical NCAAF rows without this evidence remain
unreproducible; no RNG states or probabilities were backfilled.

This capture does not change the simulator, reset its RNG, replace coefficients,
or certify upstream historical feature availability. Existing legacy model IDs
remain as recorded, while the snapshot separately binds the actual engine
source. A changed engine source blocks replay rather than silently substituting
new code. This adds three incumbent market paths to the four learned-moneyline
paths instrumented earlier.

## Complete incumbent capture coverage — 2026-09-10

All 21 incumbent market paths now have explicit capture/replay implementations:

| Family | Markets | Retained serving state |
|---|---:|---|
| MLB/WNBA/NBA/NFL learned moneyline | 4 | Full artifact, exact features, availability transform |
| NCAAF | 3 | Means, parameters, pre-call RNG state, engine hash |
| Five esports titles | 5 | Matchup Elo state, decay/calibration settings, reference time |
| WNBA spread/total and MLB NRFI | 3 | Full artifact and exact normal/logistic inputs |
| KBO/NPB | 2 | Elo state and tie policy; win/draw probabilities distinct from settlement values |
| Soccer/tennis moneyline | 2 | Full goal rates or overall/surface Elo ratings and sample counts |
| MLB spread | 1 | Full features, formula, calibration artifact and deterministic seed inputs |
| MLB structural v10 total | 1 | Full structural artifact/features, exact line, empirical residual mapper |

The v10 capture is wired into `scripts/mlb_v10_prospective_shadow.py`, its
separate prospective runner. The main forecast CLI still contains the older
Measured Edge totals path; this work does not claim to have activated a v10
production-CLI route. V10 now refuses capture before its T-30 window or after
event start. NRFI similarly skips started fixtures instead of backdating them.

Soccer retains draw probabilities. Tennis reproduces the actual six-decimal
model rounding followed by four-decimal contract rounding. MLB spread retains
raw push probabilities and separately calibrated side values: those calibrated
sides must not be silently normalized into a joint distribution. KBO/NPB draws
pay half value, so binary win/loss calibration is inappropriate for their
stored expected settlement values.

Snapshots survive canonical SQLite storage, XLSX export, and settlement in
focused tests. They prove reproducibility of captured serving state, not
upstream historical observation provenance. Historical rows without these
snapshots remain unreproducible. No inputs, RNG states or outcomes were
backfilled. Existing artifact JSON files and champion configuration remain
preserved. Serving source files were instrumented; probability formulas remain
unchanged, apart from rejecting falsely backdated prospective captures.

The legacy ready-challenger command now reads canonical SQLite, preserves zero
probabilities, pairs exact contracts/timestamps, rejects inconsistent outcomes
and context conflicts, and requires incumbent replay before metrics. Its real
battery run returned blocked comparisons, not invented gains. Its binary
comparison branch explicitly rejects KBO/NPB settlement-value metrics. The
older `model_lifecycle.load_challenger_evidence` function is not repaired by
this change; the command bypasses it.

## Outcome scoring and fresh audit

`scripts/score_research_forward.py` now rebuilds candidate predictions from
archived feature-source bytes and artifacts before joining completed outcomes.
It checks exact participants, starts, signed lines, tennis surface orientation,
and unique NRFI team/start identity across StatsAPI and scoreboard IDs.
Incomplete events, conflicting completed scores, altered inputs, and mismatched
artifacts never receive scores. Draws and integer-line pushes remain distinct
classes. The reported vector-sum Brier convention is twice scalar binary Brier
on binary markets; reports must not mix these scales.

The verified run is `outputs/research/forward_outcome_scoring_20260910_v3/`:
all 41 prior forecasts replay and remain `AWAITING_OUTCOME` because current
local source files contain no completed matching result. The command archives
current outcome stores and code, preserves existing output directories, and
returns 2 when there is no scored evidence or an integrity failure. This is
not a claim that all games are still in progress. No outcome-source refresh,
new trades, candidate retraining or promotion occurs in this command.

The refreshed canonical-ledger audit is
`outputs/research/incumbent_comparison_20260910_capture_adapters/`. It includes
newly settled legacy contexts but no settled decision with the new complete
state snapshots yet. All 21 adapters are implemented; their absence from old
records is a data limitation. The same 371 MLB comparison events reproduce
the earlier negative/inconclusive results exactly. Non-learned captured state
can pass the replay gate without being sent into the binary moneyline economics
branch; it receives an explicit awaiting-market-pairs status instead.

Verification evidence is in `outputs/research/rebuild_verification_20260910/`.
The extracted v10 empirical mapper exactly matches the prior HEAD method on
486 line/delta cases using the unchanged artifact. Lint passes on all 57
changed/new Python files, and explicit-package-base type checking passes on
20 rebuild modules. The final full suite passes: 2,821 passed, 3 skipped, no failures (293.77
seconds). Its log, code snapshot, and hashes are recorded with `validation.json`.
Rollback can restore the instrumented source while retaining appended snapshots;
existing artifact JSON files and champion configuration remain byte-identical.

## Remaining work

Candidate inference/capture and incumbent state adapters cover all 21 markets.
End-to-end market-aware paired evaluation and executable economic comparison
remain to be completed beyond learned moneyline. Historical WNBA gaps remain;
source identities, full contract archives, fees, depth and independent closing
quotes need complete linkage. Generic generation v1 and MLB residual v2 remain
research candidates with no demonstrated reliable superiority or profitability.
Sport-specific iterations and fresh settled confirmation are still required;
the rebuild is not complete merely because capture coverage reaches 21/21.

---

## Part 2 — Generation (source: GENERATION_2026-09-09.md)

# All-model research generation — 2026-09-09

**Follow-up completed:** [exact-incumbent comparison and quote economics](INCUMBENT_COMPARISON_2026-09-09.md).
MLB replay passes but the candidate does not improve prediction reliably;
both common-policy simulations lose money. WNBA replay fails; 19 other markets
need decision evidence or replay support. The initial-run descriptions below
are historical and are superseded by that follow-up where indicated.

21 fitted research candidates now exist across all 14 registered sports. This is an initial fitted benchmark generation, not 21 proven improvements. None has passed exact incumbent comparison, prospective confirmation, or executable profitability evaluation.

Vincent requested improved prediction accuracy and profitability, and explicitly declared the previous frozen protocols obsolete. New research is authorized across every model, including MLB totals. Earlier artifacts and results remain comparison/rollback evidence.

## What was built

- Training runner: `scripts/train_research_generation.py`.
- Feature replay, fitted estimators, calibration, inference: `src/model_prediction/research_generation.py`.
- Verified artifacts and reports: `outputs/research/generation_20260909_v1_verified/`.
- One `model.joblib`, hash-bound `recipe.json`, `report.json`, and per-event evaluation predictions file per market. The top-level manifest pins source/code hashes and full split identities.
- Existing production registrations and model artifacts were hash-checked unchanged. No trading, daily-run, live-ledger, or promotion operation was invoked.

## Method and evidence boundaries

Each market independently selects among three regularized linear configurations and two small gradient-boosted tree configurations. Learned preprocessing is fit only on training data. Complete dates are split 60/10/10/20 into training, selection, calibration, and retrospective evaluation. Classification calibration fits one temperature on the calibration period. Continuous markets use a separate calibration residual distribution; probabilities require an explicit actual line and retain pushes. A signed home spread is converted to the opposite home-margin threshold.

The generic sports features use lagged ratings, short/recent form, scoring/defense, volatility, available-result age, and tennis surface information. All results are embargoed for two calendar days before entering those features. Tennis participants are ordered by stable identity, never by winner/loser. Soccer/KBO/NPB retain three outcome classes. MLB NRFI reuses its dedicated first-inning feature builder with training-period-only league priors. These are intentionally initial fitted benchmarks; most do not yet incorporate the full sport-specific player, roster, starter, lineup, venue, or patch information contemplated by the structural prototypes.

The raw scoreboards lack historical observation timestamps. Chronological construction therefore supports retrospective development, not a claim that every source field was historically observable. NRFI also needs a separate legacy feature-lineage audit. Previously examined data remains previously examined; the final historical split is not new prospective evidence.

Proper scoring rules reward honest probability forecasts; classification uses log loss and Brier alongside accuracy/calibration diagnostics. See [Gneiting and Raftery, Strictly Proper Scoring Rules, Prediction, and Estimation](https://stat.uw.edu/research/tech-reports/strictly-proper-scoring-rules-prediction-and-estimation).

## Fitted candidates

All 21 selected a regularized linear model on selection-period loss. The tree alternatives were evaluated but did not win selection. The table is descriptive. It must not be read as an incumbent leaderboard.

| Sport | Market | Current incumbent | Selected candidate | Evaluation n | Diagnostic |
|---|---|---|---|---:|---|
| WNBA | moneyline | `wnba-elo-trend-lr-v4` | `logistic_C0.1` | 275 | LL 0.6441; accuracy 63.6% |
| WNBA | spread | `wnba-spread-margin-v2` | `ridge_100.0` | 275 | MAE 10.513; bias -0.484 |
| WNBA | total | `wnba-total-margin-v2` | `ridge_1.0` | 275 | MAE 16.778; bias -6.208 |
| MLB | moneyline | `mlb-elo-trend-lr-v8` | `logistic_C0.1` | 1492 | LL 0.6888; accuracy 54.2% |
| MLB | spread | `measured-edge-margin-v3` | `ridge_1.0` | 1492 | MAE 3.534; bias +0.483 |
| MLB | total | `mlb-structural-v10-frozen` | `ridge_100.0` | 1492 | MAE 3.556; bias -0.642 |
| MLB | nrfi | `mlb-nrfi-v2` | `logistic_C0.1` | 1375 | LL 0.6923; accuracy 51.4% |
| NBA | moneyline | `nba-elo-trend-lr-v4` | `logistic_C0.1` | 587 | LL 0.6142; accuracy 68.5% |
| NFL | moneyline | `nfl-elo-trend-lr-v4` | `logistic_C0.1` | 165 | LL 0.6362; accuracy 66.1% |
| NCAAF | moneyline | `college-football-v1` | `logistic_C1.0` | 878 | LL 0.5690; accuracy 68.6% |
| NCAAF | spread | `cfb-spread-v1` | `ridge_1.0` | 878 | MAE 13.501; bias -0.785 |
| NCAAF | total | `cfb-total-v1` | `ridge_1.0` | 878 | MAE 12.847; bias -0.453 |
| SOCCER | moneyline | `soccer-poisson-dc-v1` | `logistic_C0.1` | 1483 | LL 1.0206; accuracy 50.8% |
| TENNIS | moneyline | `tennis-surface-elo-v1` | `logistic_C10.0` | 5527 | LL 0.6348; accuracy 63.4% |
| CS2 | moneyline | `cs2-tiered-elo-v6` | `logistic_C0.1` | 6688 | LL 0.6349; accuracy 64.1% |
| DOTA2 | moneyline | `dota2-tiered-elo-v6` | `logistic_C0.1` | 1698 | LL 0.6314; accuracy 65.4% |
| LOL | moneyline | `lol-tiered-elo-v6` | `logistic_C0.1` | 2399 | LL 0.6134; accuracy 67.4% |
| VALORANT | moneyline | `valorant-tiered-elo-v6` | `logistic_C10.0` | 2293 | LL 0.6485; accuracy 61.7% |
| RAINBOW_SIX | moneyline | `rainbow_six-tiered-elo-v6` | `logistic_C1.0` | 495 | LL 0.6038; accuracy 68.3% |
| KBO | moneyline | `kbo-tie-aware-elo-v2` | `logistic_C0.1` | 1665 | LL 0.7886; accuracy 52.9% |
| NPB | moneyline | `npb-tie-aware-elo-v2` | `logistic_C0.1` | 809 | LL 0.7711; accuracy 53.3% |

## What the first run says

- KBO does not beat even its constant training-prior reference on evaluation log loss. NPB and MLB spread/total have date-bootstrap improvement intervals that include zero against their respective simple references. These candidates do not establish a useful upgrade.
- WNBA totals has evaluation bias around -6.21 points and only about 67.3% coverage for its nominal 80% interval. That is a material predictive weakness despite beating a constant median on MAE.
- The remaining reference comparisons are useful sanity checks, not evidence of superiority to production. The exact incumbent reproduction gate remains UNVERIFIED for all 21.

## Profitability coverage audit

The canonical runtime `market_quotes.db` contains MLB moneyline, spread, and total rows only (112,603 / 446,487 / 644,713 quote rows at audit time). Its event IDs have zero direct overlap with this generation's evaluated scoreboard event IDs. This is an identity reconciliation problem to investigate, not proof that the underlying MLB games have no quotes. Other odds archives may contain additional data; this was not an exhaustive archive audit.

The warehouse schema has no contract-ID or fee column. Therefore the run does not pretend its records already provide complete executable economic inputs. `economic_data_coverage.json` records all 21 market counts and these limitations. ROI and CLV are explicitly null/UNVERIFIED. No -110 fallback, invented line, or requested-size-as-filled assumption is used.

Before profitability can be measured: reconcile event identity with unique game/time/team evidence (including doubleheaders), validate each actual contract and side/line, bind timely quotes and historical fees, model feasible fills/size, and evaluate a policy fixed before the evaluation period. Compare at the same risk budget and report uncertainty/drawdown plus no-bet coverage. Lower log loss alone does not establish profitability.

## Existing evaluation defects identified

- `scripts/evaluate_ready_challengers.py`: event-only joins drop decision horizon/contract/side distinctions; pushes map to outcome zero; missing/zero probabilities can become 0.5. Not changed by this isolated research implementation.
- `scripts/evaluate_cfb_v2.py`: reference is raw Elo, not the shipped CFB score-distribution model; absent lines become -3.5/52.5; the ML-only verdict is reused by the registry across markets. Its existing VALIDATED_OFFLINE label is not accepted as proof for this generation.
- `qualification_registry.py`: specific IDs receive CAPTURING_PROSPECTIVE without a live capture-health check; module importability can yield MECHANICS_VALIDATED. The live registry query returned zero linked historical/replay/prospective/synthetic evaluation rows for each market, although separate offline artifacts exist.
- MLB v10 operational audit returned NO_DATA, zero predictions; no confirmation-performance metrics were inspected.

## Verification and reproducibility

- 24 focused tests passed across the new module and existing first-inning/audit tests. Ruff and focused mypy passed. No full-suite claim.
- The first run trained 20 markets; NRFI stopped before fitting on a timezone-free prior-cutoff timestamp. The timestamp was corrected and NRFI trained separately. A clean second run completed all 21, preserving the first run.
- All 21 second-run predictive metrics exactly match their first successful runs. Each fitted artifact reproduces its own evaluation predictions after serialization. This is research-run reproducibility, not an incumbent-reproduction pass.
- `verification.json` records file-hash checks and unchanged production artifacts/configuration.

Run a new isolated experiment (output must not already exist):

```bash
env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. \
  .venv/bin/python scripts/train_research_generation.py \
  --output outputs/research/<new-unique-experiment>
```

Do not select new hyperparameters by optimizing this report. Further experiments can use these historical results for development, but need future evidence for an independent confirmation claim. Active follow-up tasks live in `docs/ROADMAP.md`.

---

## Part 3 — Model Iteration (source: MODEL_ITERATION_2026-09-09.md)

# Second research iteration — 2026-09-09

Built a second MLB candidate that preserves the current model's information,
and repaired two causes of missing WNBA replay evidence. The candidate is
usable for research inference, but does not establish a reliable predictive
or profitable upgrade. No champion registration or existing model artifact
was changed, and this work submitted no orders.

## WNBA investigation and recording fixes

All 22 incomplete contexts are missing `defensive_trend_gap`, dated July 28
through August 3. This is the older explicit-field serialization gap. The
current learned serving path computes the feature, but fixed lists of ledger
columns make new features easy to omit again.

The two probability mismatches are a separate issue. The serving path can
apply a WNBA player-availability probit transform after the fitted artifact.
It previously recorded neither the full transform nor its historical margin
dispersion. The retained player-prior files for August 23 and 24 have observation
times August 24 03:21 UTC and August 25 03:26 UTC respectively, later than the
decisions being replayed. The as-of loader correctly refuses both. This is a
plausible explanation for the mismatches, not independent proof of their exact
original adjustments. Their original probabilities were not overwritten or
relabeled as reproduced.

New learned moneyline decisions now carry a self-contained snapshot of:

- The entire hash-verified incumbent artifact and every input it requires,
  preserving full numeric precision and feature names without a fixed whitelist.
- The base home probability and final served home probability.
- The applied or skipped serving transform, including points adjustment,
  historical margin sigma, threshold, availability evidence, and fallback reason.
- Event identity, start time, observation time, and a snapshot hash.

The snapshot survives `PickRequest`, the decision audit, SQLite feature payload,
XLSX export, and settlement. The evaluator supports this new evidence format.
Validation rejects artifact/input corruption, inconsistent transformations,
wrong decision identity/probability, and future observation timestamps. Legacy
rows retain their old schema; unavailable inputs are never fabricated.

Player-prior refreshes now archive both the previous daily file and every new
version under `data/player_priors/wnba/snapshots/{date}/{content_hash}.json`.
The daily file remains an atomic compatibility view. The as-of loader searches
the immutable versions and rejects conflicting priors with identical observation
times. Later refreshes therefore cannot erase earlier priors on this write path.
Existing overwritten versions cannot be restored merely by changing this code.

This instrumentation covers the common MLB/WNBA/NBA/NFL learned moneyline
serving path. The fourteen other model families still require their own exact
input/state capture and replay adapters. Capturing a calculation does not by
itself certify the historical observability of every underlying source.

Evidence: `outputs/research/wnba_replay_20260909/report.json`, with code snapshots.
Pre-change source copies: `outputs/research/wnba_replay_20260909_prechange/`.
No live ledger backfill or historical prior mutation was performed.

## MLB v2 candidate

Artifact: `outputs/research/mlb_incumbent_residual_20260909_v2_verified/model.json`.
Runner: `scripts/train_incumbent_residual.py`.
Inference: `model_prediction.incumbent_residual.predict_artifact(path, features)`;
the input columns must follow the artifact's `feature_names`.

The candidate starts with the exact v8 logit and fits a regularized correction
using its six original inputs plus the 21 generic history features from the
first generation. Standardization is fitted on training rows only. Four
predeclared selection options are the unchanged incumbent and three correction
penalties, 0.1, 1, and 10. Selection-period log loss chose penalty 1. The JSON
artifact contains its fitted parameters and the complete v8 control artifact.
No parameter was selected from the final evaluation period.

All 371 source decisions replay the exact incumbent before training. Complete
UTC dates split into 211 training games (August 10–27), 83 selection games
(August 28–September 2), and 77 evaluation games (September 3–8). These dates
were examined during the previous experiment: this is reused historical
development data, not fresh confirmation. Six evaluation dates are also too
few to make strong uncertainty claims.

| Same 77 evaluation games | Incumbent v8 | New residual v2 |
|---|---:|---:|
| Accuracy | 61.04% | 57.14% |
| Brier score, lower is better | 0.242385 | 0.239842 |
| Log loss, lower is better | 0.677901 | 0.672683 |

Brier improvement is +0.002543, with a date-cluster bootstrap 95% interval of
[-0.003008, +0.008742]. The interval does not clear the required 0.002 minimum
effect. The verdict is **NO_RELIABLE_GAIN**.

Of those 77 games, 68 have the previously verified archived quotes. The same
$5-per-event, fee-aware taker simulation from the first comparison produces:

| Same 68 quote opportunities | Incumbent v8 | New residual v2 |
|---|---:|---:|
| Hypothetical trades | 44 | 55 |
| Net P&L | -$14.335 | -$16.065 |
| ROI on deployed cost | -6.82% | -6.13% |
| Maximum settled-P&L drawdown | $25.865 | $40.365 |

The candidate's less-negative ROI does not mean it earned more money: it
deployed more capital and lost $1.73 more. Its paired P&L gain per opportunity
is -$0.0254, with 95% interval [-$0.3950, +$0.4498]. Both lose, and the candidate
has larger drawdown. These are hypothetical fills, not realized trading
results; CLV and actual-fill metrics remain unavailable. The fee and execution
assumptions are documented in `INCUMBENT_COMPARISON_2026-09-09.md`.

## Verification and next work

203 focused tests passed across the new replay/archival logic, residual model,
learned serving, WNBA availability, CLI, canonical ledger, settlement, original
research generation, and incumbent evaluation. Scoped Ruff and mypy pass.
The verified training run reproduces the first v2 model bytes and metrics.
Existing production model/config hashes remain unchanged. This is not a new
full-suite verification or a deployment of the new model.

The next bottleneck is the amount and coverage of decision-time evidence.
Extend exact state capture to the other fourteen families, obtain current-model
cohorts for the five empty markets, and collect a fresh forward evaluation for
the new candidate. Do not keep tuning thresholds or penalties on these same
six evaluation dates until a positive number appears.

---

## Part 4 — Incumbent Comparison (source: INCUMBENT_COMPARISON_2026-09-09.md)

# All-model incumbent comparison — 2026-09-09

Follow-up: [recording fixes and the second MLB candidate](MODEL_ITERATION_2026-09-09.md).
The results below remain the preserved first comparison.

The first generation does **not** establish an improved, profitable replacement.
MLB moneyline is the only market whose exact current artifact can be replayed
and compared end to end from the available decision records. Its new candidate
has worse point estimates for accuracy, Brier score, and log loss. WNBA replay
fails. The other 19 markets lack either settled current-model decisions or
reproducible stored inputs/model state. No model was promoted and no order was
placed by this evaluation.

The operator's research reset remains in force. These are evidence limitations,
not reinstated frozen-protocol restrictions.

## Verified output

`outputs/research/incumbent_comparison_20260909_verified/` contains the policy,
canonical ledger snapshot, code snapshot, full report, and one report per
market. Each market has per-context stored/reproduced probabilities and replay
status. MLB additionally has exact prediction pairs and the complete archived
quote records used for hypothetical economics.

Run `scripts/evaluate_research_incumbents.py` with the existing generation,
canonical runtime database, and a new output directory:

```bash
env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. \
  .venv/bin/python scripts/evaluate_research_incumbents.py \
  --generation outputs/research/generation_20260909_v1_verified \
  --database /Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db \
  --output outputs/research/incumbent_comparison_NEW_RUN
```

The runner opens SQLite read-only and saves the selected records from one read
transaction. It checks the current champion registry and hashes production
artifacts before and after. It never imports an execution or promotion path.
Earlier failed attempts remain under `incumbent_comparison_20260909_v1`–`v3`;
`v4` is the first complete run. The verified run reproduces its results.

## MLB result

663 settled ledger rows become 445 unique decision contexts after removing
218 mirror duplicates. Four contexts differ only in tier-specific feature
availability annotations; their actual feature values, hashes, predictions,
outcomes, and quote references agree. All 378 contexts with the exact current
artifact hash reproduce within 0.000002 probability. Another 67 contexts carry
an earlier artifact hash and are outside the current-artifact comparison.

Choose the earliest decision per event before checking quote eligibility.
Five events are absent from the candidate's exact saved holdout and one has a
source start-time mismatch; one later decision is removed. This leaves 371
games across 30 UTC dates. Every pair checks participant orientation, start
time, pregame decision time, score, outcome, and candidate target. All 1,492
saved candidate holdout predictions also reproduce from the fitted artifact.

| Metric, same 371 games | Current MLB v8 | New research candidate |
|---|---:|---:|
| Accuracy | 56.33% | 52.56% |
| Brier score, lower is better | 0.243103 | 0.247241 |
| Log loss, lower is better | 0.679154 | 0.687610 |

Brier improvement is -0.004138; its date-cluster bootstrap 95% interval is
[-0.009810, +0.001758]. The candidate fails the required lower-bound improvement
of 0.002. This is no reliable gain, not proof of a statistically significant
degradation. The generic candidate also omits the starter, bullpen, park, and
weather inputs used by v8; the experiment does not isolate which inputs explain
the difference.

The historical source file changed after training, but rebuilding from the
current source exactly reproduces **all 6,855 saved feature-and-target rows**.
Both source hashes and this verification are recorded. The original fitted
candidate and saved evaluation predictions were not changed.

## Hypothetical economics, not realized fills

194 of the 371 games have a uniquely linked, pregame full-game binary contract
with a fresh, valid two-sided book. Exclusions: 169 missing archived references,
one contract start-time mismatch, and seven quotes older than 300 seconds.
All 194 links use the known legacy reduced-quote hash, uniquely reconciled to
an archived full record. This is explicitly distinguished from a full-record
hash created at decision time. The evaluation saves each recovered raw record
and its full hash; historical ledger records are not rewritten.

The common policy permits up to $5 per event including fees, buys whole
contracts within displayed ask depth, chooses the side with the largest
positive expected net return per dollar, and otherwise abstains. Prices are
asks, never midpoint or a -110 fallback. It assumes an immediate single taker
fill and holds to binary settlement. It does not reproduce the incumbent's
actual serving/trading policy or assert that a historical order would fill.

Fees use the [official Polymarket US fee schedule](https://docs.polymarket.us/fees):
the taker coefficient is 0.06 in `coefficient × contracts × price × (1-price)`,
rounded to cents with half-even rounding. The published effective time is July
1, 2026 at midnight ET; earlier quotes are excluded. Rebates are not assumed.

| Same 194 opportunities | Current v8 probabilities | Candidate probabilities |
|---|---:|---:|
| Hypothetical trades | 134 | 152 |
| Cost including entry fees | $640.395 | $727.570 |
| Net P&L | -$91.395 | -$69.570 |
| ROI on deployed cost | -14.27% | -9.56% |
| Entry fees | $20.25 | $24.49 |
| Maximum drawdown of settled P&L | $103.445 | $90.785 |

Candidate-minus-incumbent P&L is +$0.1125 per available event, with a date-cluster
95% interval of [-$0.4030, +$0.6451]. Both policies lose money and the incremental
advantage is uncertain. ROI denominators differ because the models abstain on
different events under the same per-event budget. CLV and actual fills remain
null. The sample is selected by incumbent ledger coverage, and historical
feature observation times remain unverified. The policy was written before
this evaluator consumed outcomes, but it is retrospective development, not
prospective preregistration or fresh confirmation.

## Other markets

- **WNBA moneyline:** 169 rows reduce to 94 contexts. 70 replay, 22 lack required
  valid inputs, and two mismatch. Event `401857168`, August 23: stored 0.547062
  vs reproduced 0.634997 for away. Event `401857171`, August 24: stored 0.552593
  vs reproduced 0.669877 for home. These are unexplained differences, not
  calibration evidence. Comparison and economics are blocked for this market.
- **No settled current-model decisions:** WNBA spread v2, WNBA total v2, MLB
  structural total v10, NBA moneyline v4, NFL moneyline v4. Older/rollback
  decisions are not relabeled as evidence for these incumbents.
- **Fourteen other markets:** MLB spread and NRFI; all three NCAAF markets;
  soccer; tennis; five esports; KBO; NPB. The canonical records contain no stored
  model feature inputs for these cohorts. This evaluator has no replay adapter
  for these families. Recover versioned decision state and inputs and implement
  their exact inference paths before interpreting a candidate delta. This is
  not a claim that replay is impossible using other archives.

## Fix and verification

`match_executable_quote` previously returned a reduced object. The moneyline
logger hashed that object while pointing to an archive containing the full
market record. The matcher now preserves the exact raw record privately, and
the lineage function hashes that record. The regression test checks the hash
against the actual archived JSON, rather than merely checking a non-null
lineage object. No retrospective ledger mutation is performed.

126 focused tests passed across research generation, incumbent evaluation,
quote regressions, CLI, and learned-forward behavior. Scoped Ruff and mypy
passed. This is not a new full-suite result. Existing production model/config
hashes are unchanged. Code backups are under
`outputs/research/incumbent_20260909_code_backup/`; the logging fix is reversible
independently of the research evaluator.

The old `evaluate_ready_challengers.py` is not used: it expects `result` and
`model_probability` from a loader returning `outcome` and `probability`, joins
only event IDs, and defaults missing probabilities to 0.5. Those issues remain
in that legacy path; this new runner is the supported path for this generation.

## Next iteration

1. Reconcile the two WNBA mismatches and capture complete, hash-bound model
   inputs/state for all families. The five empty current-model cohorts need
   decision collection or an independently reproducible historical replay.
2. For MLB, preserve useful incumbent information and test incremental
   sport-specific features and quote-aware selection in a new development
   generation. Do not select new thresholds on this now-examined sample.
3. Continue sport-specific improvements, prioritizing degraded NCAAF and the
   weak KBO/NPB and WNBA-total candidates already identified by generation one.
4. Collect a fresh forward cohort with exact contracts, actual observation
   times, full raw quote hashes, depth, and independently matched closing data.
   Evaluate predictive gains and absolute net returns before proposing any
   replacement. More model versions by themselves are not progress.

---

## Part 5 — Paired Rebuild (source: PAIRED_REBUILD_2026-09-10.md)

# Rebuild: prospective outcomes and exact paired capture

The 21 fitted research candidates now have a market-aware comparison path.
The first 41 pregame forecasts have completed outcomes. Neither development
establishes a reliably more accurate or profitable replacement. The rebuild
remains open; current models and prior artifacts remain available.

## First prospective batch

`outputs/research/forward_outcome_scoring_20260910_settled_v1/` scores all 41
original predictions from `forward_capture_20260909_v1`, rebuilding every
prediction from its archived pregame source bytes. These are 11 distinct
events across two UTC start dates, not 41 independent games. No refit occurred.

| Candidate | Forecasts | Correct top outcome | Vector Brier | Log loss |
|---|---:|---:|---:|---:|
| MLB moneyline | 10 | 5 | 0.486528 | 0.679604 |
| MLB spread | 10 | 8 | 0.439055 | 0.631820 |
| MLB total | 10 | 3 | 0.536278 | 0.780589 |
| MLB NRFI | 10 | 6 | 0.498084 | 0.691241 |
| NFL moneyline | 1 | 1 | 0.190839 | 0.369471 |

Vector Brier sums squared error over every outcome class; it is twice the
usual scalar Brier for binary predictions. The totals result is weak in this
small batch. The spread result does not establish a durable advantage. Exact
lines came from archived ESPN schedules, not verified exchange executions;
profitability remains unavailable.

MLB outcomes were independently fetched from ESPN's completed September 9
scoreboard. The raw response, source URL/time/hash, strict normalized results,
and reconciliation are retained in
`outputs/research/espn_outcomes_20260910_verified_v2/`. All 80 corresponding
canonical ledger records agree on event identity, start, participants, and
the correct score dimension. NFL used its completed local historical record.
The first empty ESPN output directory records the unsuccessful sandbox attempt.

The new `research_outcomes.py` distinguishes explicit zero runs from missing
scores, requires completed events and exact identities, and reads explicit
first-inning periods. Missing first-inning data can leave NRFI unavailable
while full-game outcomes remain usable. The scoring command accepts archived
outcome overrides without replacing any original prediction inputs.

## Exact comparisons and payout math

`scripts/evaluate_captured_pairs.py` covers all 21 model registrations. Each
pair must reproduce both models, match event/start/participants/line and model
observation time, and independently reproduce the canonical settlement.
Soccer/international draws and integer-line pushes remain separate outcomes.
International expected settlement values are not mistaken for win probabilities.
Materially incoherent MLB spread side/push probability mass is rejected;
only tiny recorded serving-rounding error can be normalized.

The actual first-batch evaluation has zero eligible comparisons:

| Family | Rows | Exclusion |
|---|---:|---|
| MLB moneyline | 10 | Different candidate/incumbent observation times |
| MLB spread | 10 | Missing older incumbent serving snapshots |
| MLB NRFI | 10 | Missing older incumbent serving snapshots |
| MLB structural v10 total | 10 | No matching current-incumbent decisions |
| NFL moneyline | 1 | No matching incumbent decisions |

Evidence: `outputs/research/captured_pair_evaluation_20260910_v1/`.
Zero pairs produce null comparative metrics, not a zero-loss or passing result.

`research_pairing.simulate_contracts` evaluates complete explicit payout tables,
including numeric half-value draws and returned entry principal. It retains
entry fees, observes depth and a common $5 all-in budget, and reproduces the
existing binary policy. This is policy math, not quote authentication. The
runner currently authenticates archived full-game binary moneyline quotes;
other contract families still require source-backed rule/quote adapters.

## Pregame paired capture

`scripts/capture_research_pairs.py` separates preparation from capture:

1. `prepare` freezes explicit future fixtures, fitted artifacts, recipes and
   source bytes in a new immutable directory.
2. After an incumbent forecast, `capture` reads canonical SQLite in read-only
   mode and selects the first incumbent decision after preparation. It does
   not skip a bad first decision to choose a convenient later probability.
3. The candidate uses the incumbent's information cutoff. Its actual later
   creation time is recorded separately, and must still precede event start.
   Source capture and preparation must precede the common cutoff.
4. The scoring/comparison pipeline retains the preparation hash and checks
   the exact bound pick, incumbent snapshot, source and artifact identities.

No production forecast or order is launched by this command. Missing, changed,
conflicting or late inputs remain explicit failures. This proves use of the
frozen available sources, not upstream historical point-in-time certification.

Today’s public schedules were archived in
`outputs/research/paired_fixtures_20260910_v1/`: three future MLB games across
four markets and one NFL moneyline, 13 contracts total. Two already-started
MLB games were excluded; WNBA had no scheduled games. Inputs were prepared at
18:01 UTC in `paired_preparation_20260910_v1/`. The subsequent capture was run
after all 13 starts and correctly refused every prediction as
`fixture_not_future`; see `paired_capture_20260910_v1/`. This batch cannot be
recovered prospectively. Preparation alone does not collect paired evidence:
capture must actually run between an incumbent forecast and event start.

This timing gap is now addressed by the bounded `observe` command. It checks
the canonical ledger every 30 seconds, runs inference when pending fixtures
acquire new incumbent contexts, and retains the first successful pregame
prediction. Every intermediate batch is immutable. Later decisions or
postgame failures cannot replace a previously captured prediction. At event
start or the time limit, missing evidence becomes an explicit refusal. It
does not trigger the incumbent forecaster or alter its schedule.

The observer is running for the September 11 slate at
`outputs/research/paired_observer_20260911_v1/`, with inputs in
`paired_preparation_20260911_v1/` and public schedule evidence in
`paired_fixtures_20260911_v1/`. The cohort contains 68 explicit contracts over
15 MLB and five NCAAF games. One MLB NRFI lacks a named starter/venue and
three MLB games lack derivative lines; those contracts were not fabricated.
WNBA and NFL schedules were empty. The observer has a 24-hour maximum lifetime;
its live `report.json` states pending, captured and refused counts. Running
the observer is not evidence that any pair has already been captured.

Later update: the first observer was stopped before capturing any predictions
to correct NCAAF's historical participant namespace and incorporate three
new fitted joint CFB models. The active replacement is
`paired_observer_20260911_v2/`; see
[the fitted CFB iteration](CFB_JOINT_ITERATION_2026-09-11.md).

## Settlement repair and verification

NRFI settlement previously could substitute full-game scores when first-inning
scores were missing. It now leaves that pick pending before quote lookup.
The ESPN adapter no longer fabricates a zero from missing first-inning values.
No existing settled ledger row was rewritten; the 80 audited MLB rows were
already correct. Rollback is the previous code; new research artifacts remain
alongside old versions.

The complete suite collected 2,845 tests: 2,832 passed, three skipped, nine
localhost binding failures and one rounding-assertion failure. The latter
test now checks the documented per-team rounding order exactly. The full
affected 88-test group passes. The complete HTTP file passes all 23 tests
with localhost access after its GET authorization test was isolated from live
dashboard inventory I/O. These are a broad run plus focused resolutions, not
a claim that a second monolithic full-suite run occurred. Scoped Ruff/mypy and
file hashes are retained in `outputs/research/paired_verification_20260910/`.
After adding the observer, its complete affected inference/scoring/pairing
group passes 54 tests, including two new observer tests. All 70 changed Python
files pass Ruff. Protected JSON artifacts and champion configuration exactly
match the prior verified checkpoint.

## Remaining rebuild work

- Verify the active bounded observer captures real incumbent contexts, then
  extend ongoing schedule coverage and collect fresh paired outcomes across
  the 21 market families. V10's separate shadow path still needs its own
  canonical evidence connection when no canonical incumbent row exists.
- Bind nonbinary execution policies to actual archived exchange rules, exact
  quotes, fees and contract settlement semantics.
- Continue fitted sport-specific iterations, especially score distributions;
  the generic first generation is a development baseline, not an upgrade claim.
- Resolve historical source-availability limitations and the older lifecycle
  evidence-loader debt. Missing history cannot be repaired by relabeling it.

No reliable accuracy/profit superiority, promotion, or real-money result is
claimed by this checkpoint.

---

## Part 6 — Recency and Quote Iteration (source: RECENCY_AND_QUOTE_ITERATION_2026-09-11.md)

# Recency experiment and prospective quote evaluation

The rebuild now has a completed 17-market recency experiment and an executable
quote evaluator for captured full-game binary moneylines and MLB half-point
spreads/totals. Neither establishes improved production performance or profit.
The existing 21-model research generation remains available; this experiment
is preserved alongside it, without champion changes or orders.

## Recency experiment

Theory: season and roster changes can make older training games less useful.
Evidence: the original training partitions end substantially before their later
evaluation dates. Test: keep the exact features, date partitions, estimator
architecture and hyperparameters; compare no weighting against training weights
with 180-day and 730-day half-lives. Normalize weights to mean one and apply them
to both the scaler and estimator. Choose on the original selection partition;
fit temperature or residual calibration only on its separate original partition.

All 17 v1 research references reproduce every saved evaluation prediction with
maximum absolute error zero before candidate fitting. The control is explicitly
the prior research candidate, not the production incumbent. CFB retains its
separate three-market joint-score experiment; NRFI is excluded because its
archived generic feature files do not contain the specialized first-inning
cohort. Its original source/lineage limitations remain unresolved.

Ten markets selected recency weighting. Seven selected the unchanged reference:
WNBA total, NBA, NFL, CS2, Valorant, KBO and NPB moneylines. All 17 fitted bundles
are versioned and reloadable, but those seven are equivalent reference fits,
not seven predictive improvements. Two complete runs produced identical fitted
artifact hashes for every market.

None clears the predeclared improvement threshold: the lower endpoint of the
pointwise date-cluster 95% interval must exceed 0.004 vector Brier or 1% of
reference score MAE. The intervals are exploratory and not adjusted for the
17 comparisons. Reused evaluation periods are development evidence.

| Market | Reference | Recency candidate | Interpretation |
|---|---:|---:|---|
| MLB moneyline accuracy | 54.22% | 54.62% | Brier gain too small and uncertain |
| LoL moneyline accuracy | 67.40% | 68.03% | Vector Brier gain 0.001172, interval [0.000367, 0.001984]; below 0.004 |
| WNBA moneyline accuracy | 63.64% | 62.91% | Worse point estimate |
| WNBA spread score MAE | 10.5135 | 10.9667 | Worse; reference-minus-candidate interval [-0.7460, -0.1592] points |
| Soccer vector Brier | 0.608251 | 0.608439 | No improvement |

Failure mode: emphasizing recent samples can discard useful information and
amplify a transient selection-period pattern. The WNBA spread result illustrates
that failure. Preserving the existing generation is the rollback; no production
configuration has been replaced. Further generic weighting is not supported by
this experiment. Sport-specific information and fresh outcome/price evidence
remain the substantive work.

Artifacts:

- `outputs/research/recency_iteration_20260911_v2_verified/`
- `outputs/research/recency_iteration_20260911_v2_reproduced/`
- Trainer: `scripts/train_recency_research.py`.
- 39 actual pregame forecasts from the three MLB recency models, covering 15
  games: `outputs/research/recency_forward_20260911_v2/`. They are a separate
  cohort, not additional independent games or replacements for the prepared
  incumbent-paired forecasts. Their archived sources and predictions rebuild.

## Captured quote and prediction audit

The corrected observer has captured 49 paired forecasts across 18 games. All 49
reproduce both the candidate and its bound production decision. At this
checkpoint, five contracts have insufficient participant history and fourteen
are still pending. The live observer report remains authoritative:
`outputs/research/paired_observer_20260911_v2/report.json`.

The final audit is
`outputs/research/paired_quote_audit_20260911_v3_verified/`.
There are **24 verified event-market quote opportunities across 14 MLB games**:
14 moneylines and 10 spreads. The remaining 25 reproduced forecasts have no
verified economics: 14 NRFI, eight CFB, one moneyline start mismatch, one spread
book timestamp mismatch, and one spread price/depth failure. Missing references
and unsupported quote adapters are distinct from invalid archived evidence.

The new MLB adapter uses the existing canonical envelope hash loader. It checks
archive root and record identity, event/start/team matching, full-game contract
slug, signed selection line, long/short mapping, market state, raw book timestamp,
ask/bid/depth and agreement between the projected price and raw executable ask.
It rejects first-five-inning contracts and integer lines whose push terms are
not established by this archive. For half-point lines, an impossible push can
be removed only when both models assign exactly zero probability to it.

The same $5 all-in, whole-contract, depth-limited, entry-fee policy selects 18
candidate trades and 14 incumbent trades across those 24 opportunities. These
are independently simulated per-market choices at archived decision prices;
they are not current quotes, fills, a combined portfolio, or independent events.
All realized P&L, ROI and accuracy remain null before settlement. Exact quote
records, payout tables, choices, source hashes and a read-only ledger snapshot
are retained. Settled evaluation uses the same adapter and policy math.

A discovered ledger-mirroring race is also fixed: when an equivalent tier copy
arrives after capture with a smaller pick ID, it must not replace the original
bound decision. The first deduplicated decision context must still match, and
the original bound ID must belong to that verified mirror group. A genuinely
earlier or conflicting decision remains disqualifying.

## Verification and remaining work

118 focused tests pass, including exact incumbent/reference reproduction,
archive tampering, participant/line/time mismatches, wrong contract horizons,
missing depth, invalid numeric inputs, impossible pushes, pregame null P&L,
settlement parity, mirror races, training-only weighting and serialization.
Scoped Ruff and mypy pass. Exact artifacts, code and verification hashes are in
`outputs/research/recency_quote_verification_20260911/validation.json`.

The rebuild is not complete in the sense requested: no replacement has yet
proved both improved production prediction accuracy and executable profitability.
Next evidence comes from these already-captured games settling, verified NRFI
and CFB price capture, and sport-specific feature improvements. Existing frozen
protocols remain obsolete; missing statistical or price evidence is a factual
limitation, not a frozen-protocol restriction.

---

## Part 7 — CFB Joint Iteration (source: CFB_JOINT_ITERATION_2026-09-11.md)

# Fitted CFB joint-score iteration

Three new research versions now share one fitted joint distribution:
`ncaaf-moneyline-joint-research-20260911-v2`,
`ncaaf-spread-joint-research-20260911-v2`, and
`ncaaf-total-joint-research-20260911-v2`.

The candidate modestly improves moneyline point estimates against research
generation v1, but none of the measured gains is statistically reliable.
It has not established superiority over the production incumbent or executable
profitability. It is integrated with the common fixture, source-rebuild,
outcome-scoring and exact-pair capture interfaces.

## Model and experiment

Theory: separate market heads can disagree, and arbitrary score dispersion can
produce poorly calibrated probabilities. Fit home and away score means and
preserve their paired held-out errors, then price all three markets from the
same score distribution.

The model uses standardized ridge regression with penalties 1, 10 and 100.
The mean home/away absolute error on the selection period chooses the penalty;
penalty 1 won. The scaler and coefficients use only 3,061 training games.
Selection uses 510 games. The 563 calibration games provide paired residuals;
their covariance is approximately [[147.63, -1.71], [-1.71, 125.39]]. No test
rows enter the fit, penalty selection or residual distribution.

For inference, the model adds every calibration residual pair to the predicted
score means, rounds to integer points and clips negative scores to zero. All
markets condition on unequal final scores, consistent with college football
having an eventual winner; no overtime points are invented. About 1.955% of
raw evaluation samples tied before that conditioning. This empirical support
is still an approximation, not a complete drive or overtime simulator.

The exact archived v1 feature rows and complete-date partitions are retained.
Before comparing, all three v1 research models reproduce their saved 878
evaluation outputs with zero error. They are explicitly a **research reference**,
not the production incumbent. The old CFB evaluator's raw-Elo label and default
spread/total lines are not accepted as incumbent/economic evidence here.

## Same 878 historical evaluation games, 112 dates

| Measure | Research v1 reference | Joint v2 candidate |
|---|---:|---:|
| Moneyline accuracy | 68.56% | 69.70% |
| Moneyline scalar Brier | 0.194203 | 0.192886 |
| Moneyline log loss | 0.569047 | 0.564274 |
| Margin absolute error, points | 13.5013 | 13.5319 |
| Total absolute error, points | 12.8466 | 12.8331 |

Date-cluster bootstrap 95% intervals for reference-minus-candidate loss:

- Moneyline Brier: +0.001317, interval [-0.001940, +0.004664].
- Margin absolute error: -0.030601 points, interval [-0.071517, +0.006901].
- Total absolute error: +0.013426 points, interval [-0.009540, +0.041337].

These are reused historical development dates. No fresh confirmation is
claimed. Spread/total contract-probability scores and economics remain null
because exact historical contract lines and executable quotes are unavailable.
The candidate's small improvement in moneyline accuracy does not outweigh
those limitations or demonstrate that every market improved.

## Artifacts and actual forward use

- Fitted artifact and three reloadable serving bundles:
  `outputs/research/cfb_joint_20260911_v2_verified/`.
- Two complete fits produced the same joint artifact SHA-256:
  `577b3be49dc9be51b82089d53893bae7fbe62b7d452bfbb5b5329c92a2b570e3`.
- Current 21-model research generation:
  `outputs/research/generation_20260911_joint_update/`. It retains 18 prior
  research candidates and updates the three CFB candidates. This is not a
  change to production champions.
- Nine actual pregame forecasts across three games replay and independently
  rebuild from captured sources in
  `outputs/research/cfb_joint_forward_20260911_v2_mapped/`.
- Six additional contracts are unavailable: the frozen history has no records
  for Norfolk State Spartans or Richmond Spiders. No team history is fabricated.

The first forward attempt used numeric ESPN team IDs and was refused. The
historical NCAAF source has 5,259 rows keyed by exact team names, with no numeric
team-ID fields. Corrected fixtures preserve provider IDs separately and use
the exact historical names as feature keys. Outcome scoring requires an exact
provider ID/name match before translating a provider result into that legacy
namespace; this is not a fuzzy-name join.

The first bounded observer was stopped before it captured any predictions.
Its replacement is `outputs/research/paired_observer_20260911_v2/`, using
`paired_preparation_20260911_v2/`. It monitors 68 explicit contracts across 20
MLB/NCAAF games every 30 seconds, for at most 24 hours, and retains the first
valid pregame capture. Its report is the live source for actual capture counts.
An observing process is not already a successful comparison.

## Verification and remaining work

The integrated joint-training, shared-generation, forecast, scorer and paired
capture suite passes 70 tests. Tests cover serialization, coherent market
probabilities, integer pushes, fitted residual covariance, corrupt artifacts,
invalid inputs/lines, provider identity mismatches, and observer deadlines.
The actual 15-contract forward attempt records nine passes and six explicit
cold-start failures. Current lint/type checks and exact source snapshots are
in `outputs/research/joint_verification_20260911/`.

Next work is real incumbent-paired outcomes and verified economic evaluation,
followed by further sport-specific fitting. The prior production models and
all prior research versions remain available for rollback and comparison.

---

## Part 8 — NRFI and Settled Rebuild (source: NRFI_AND_SETTLED_REBUILD_2026-09-12.md)

# Corrected NRFI model and first archived paired returns

The current 21-model research generation is
`outputs/research/generation_20260912_clean_update/`. It adds the corrected
NRFI candidate to the three joint CFB candidates and 17 generic candidates.
The recency experiments remain separate. This is a usable research generation,
not a set of qualified production replacements.

## Prospective results from September 11

The bounded observer exited with 49 captured forecasts, 19 unavailable contracts
and no pending work. Its process exited with code 2, matching a partial capture;
it is no longer running. Independent ESPN scoreboards contain completed results
for all 15 MLB and five CFB games on the prepared slate. All 49 captured forecasts
score, across 18 distinct games. Nineteen preparation failures remain failures.

All 49 bound canonical production rows are now marked `removed`, with no
canonical settlement. The pregame audit preserved exact decision records,
model inputs, source files, quotes and policy choices before the games. The new
archive evaluator independently reproduces both models, revalidates the embedded
quote hashes and contract terms, verifies the pregame choices, and joins the
completed scoreboard. It does not relabel or restore any removed ledger row.
These results measure archived forecasts and simulated trades, not actual fills.

| Market | Games | Incumbent accuracy | Candidate accuracy | Incumbent vector Brier | Candidate vector Brier |
|---|---:|---:|---:|---:|---:|
| MLB moneyline | 15 | 80.00% | 60.00% | 0.436588 | 0.481547 |
| MLB spread | 12 | 50.00% | 50.00% | 0.541600 | 0.468008 |
| MLB NRFI, original research v1 | 14 | 35.71% | 35.71% | 0.498529 | 0.503380 |
| CFB moneyline | 2 | 100.00% | 100.00% | 0.341037 | 0.466436 |
| CFB spread | 3 | 33.33% | 0.00% | 0.891484 | 0.926901 |
| CFB total | 3 | 66.67% | 0.00% | 0.522804 | 0.623864 |

The same games can appear in multiple markets. These are not 49 independent
outcomes. Brier is the sum across all outcome classes, including pushes where
applicable. The NRFI results above belong to the earlier research candidate,
not the corrected version fitted on September 12.

Twenty-four event-market opportunities across 14 MLB games retain eligible
quotes. The entry-fee, whole-contract, $5 all-in policy is unchanged from the
pregame audit. Its already-recorded choices produce:

| Quoted market | Opportunities | Incumbent simulated P&L | Candidate simulated P&L |
|---|---:|---:|---:|
| MLB moneyline | 14 | -$17.785 | -$28.235 |
| MLB spread | 10 | -$8.345 | +$14.310 |
| Combined event-market simulations | 24 | -$26.130 | -$13.925 |

The incumbent made 14 hypothetical trades costing $67.130, for -38.92% ROI.
The candidate made 18 costing $86.925, for -16.02% ROI. This is a single US game
date, not a reliable profitability estimate or an independently sized combined
portfolio. Both policies lost overall. The spread subset does not justify a
profitable-model claim. Confidence intervals, actual fills and CLV remain null.
NRFI/CFB forecasts without verified quotes remain unpriced.

Separate recency forecasts also scored all 39 contracts. They reuse many of
these games and are not additional independent evidence. Their output is
`outputs/research/recency_outcomes_20260912_v1/`.

Evidence:

- Raw and normalized independent results:
  `outputs/research/espn_outcomes_20260912_for_20260911/`.
- Original paired candidate outcome scores:
  `outputs/research/paired_outcomes_20260912_v1/`.
- Both-model comparison and immutable pregame choices:
  `outputs/research/archived_pair_evaluation_20260912_v2/`.
- Runner: `scripts/evaluate_archived_research_pairs.py`.

## NRFI input audit and corrected model

The old builder included a canceled game (`746577`, training) and a scheduled
game (`823042`, evaluation), both labeled NRFI. It also placed both teams'
batters into each team's recent player pool, reversed the home/away defensive
history buckets, and used different history/rest timing in training and live
research inference. A separate game (`746942`) has a player identity on both
teams; the corrected cohort excludes that ambiguous case rather than inventing
an assignment.

The new `research_nrfi.py` builder requires completed status and explicit,
consistent first-inning scores. It rejects missing counts, contradictory labels,
invalid IDs and ambiguous cross-team/player assignments. Both training and live
research inference share the same team-specific accumulators, opponent-role
priors, two-calendar-day outcome embargo and starter rest measured to scheduled
start. Normalized starter names may resolve to only one historical player ID.

The recent-batter features are named honestly: they are PA-weighted recent
team pools, not confirmed top-three batters or confirmed starting lineups.
Historical starter identities still come from completed box scores. The source
is a retrospective reconstruction, and historical pregame availability remains
uncertified. The repairs do not retroactively certify point-in-time provenance.
The production legacy builder and incumbent artifact remain unchanged.

All 1,375 saved v1 evaluation predictions reproduce with zero error before the
comparison. The cleaned cohort uses the original partition memberships, after
removing invalid/ambiguous rows: 4,105 train, 697 selection, 686 calibration,
1,374 evaluation. No boundary moves to compensate for removed rows. League
priors use only cleaned training dates; model/temperature selection uses the
same separate date partitions and existing five classifier variants.

The fitted candidate is `mlb-nrfi-clean-research-20260911-v2` (the version tag
was established when implementation began; the verified fit ran September 12).
It selects logistic regression C=0.1. On the same 1,374 valid evaluation games:

| Metric | Original research reference | Corrected candidate |
|---|---:|---:|
| Accuracy | 51.3828% | 51.6739% |
| Vector Brier | 0.499205 | 0.499835 |
| Log loss | 0.692351 | 0.692983 |

The reference-minus-candidate Brier interval is [-0.001315, -0.000012]. Cleaning
fixes input defects, but this fitted model does not improve probability quality.
It is not promoted. The old candidate remains available for exact comparison.

Two complete fits produce identical fitted artifact hashes:
`outputs/research/clean_nrfi_20260912_v2_verified/` and
`outputs/research/clean_nrfi_20260912_v2_reproduced/`.
Trainer: `scripts/train_clean_nrfi_research.py`.

Fourteen actual pregame forecasts for September 12 independently rebuild from
archived sources in `outputs/research/clean_nrfi_forward_20260912_v2/`.
One additional scheduled game lacked probable starters and was excluded.
Those outcomes and executable returns are not yet known.

## Validation and remaining scope

The complete suite passes: **2,897 passed, 3 skipped**. All 21 saved candidates
pass artifact-identity and probability-inference checks, including explicit
integer and half-point derivative lines. Fourteen new NRFI forecasts rebuild
from source, and the two corrected fits are byte-identical. Scoped mypy passes.
Exact code, artifact checks and lint results are retained in
`outputs/research/nrfi_and_settlement_verification_20260912/`.

Broader economic support still needs exact captured contract evidence for NRFI,
CFB and other unsupported settlement/archive forms. A missing quote reference is
not proof that a market does not exist: the current Polymarket archive does
contain NRFI books, but those are not automatically the price at a candidate's
recorded decision. New predictions need timely bound prices and explicit rules.
Inactive or uncaptured sports retain historical research metrics and null
prospective/economic evidence. No new champion registration, production ledger
write or order submission is part of this rebuild.

---

## Part 9 — Rebuild Status (source: REBUILD_STATUS_2026-09-14.md)

# All-model rebuild: implementation and evidence

There are 21 fitted research candidates covering every current model, with
archived inputs, inference, incumbent state capture/replay and outcome evaluation.
The current generation remains
`outputs/research/generation_20260912_clean_update/`: 17 generic candidates,
three joint CFB candidates and the corrected NRFI candidate. The later recency
experiments remain separate. **No candidate has established reliable improvement
in both accuracy and profitability. None is qualified as a production replacement.**

## Completed economic adapters

The new `capture_research_prices.py` captures a later pricing decision against an
already frozen forecast. Prediction time, request time, book observation and
pricing decision remain separate. It archives public event, market and book
responses, verifies the generation and rebuilds features from source before
pricing. It never attaches a later quote to an earlier prediction timestamp.

`score_research_prices.py` verifies the entire archive, rebuilds the forecast,
reconstructs its payout table and pregame choice, and then applies an independently
captured completed result. Unpriced, missing and conflicting evidence remains
visible. Costs, fees, whole-contract size and choices cannot change at settlement.
Results are hypothetical taker simulations, not fills or portfolio returns.

| Contract family | Supported settlement | Evidence limits |
|---|---|---|
| Existing binary moneylines | Existing exact paired-quote adapter | Requires a bound archived production decision and valid quote |
| MLB spread/total | Existing full-game half-point archive adapter | Integer/push terms remain unverified |
| MLB NRFI | Yes = either team scores in inning one; No = zero runs | Requires exact start, both teams, actual rule, fresh book and completed first-inning scores |
| CFB moneyline | Full game including overtime, binary official winner | Unresolved tied official results unavailable |
| CFB and WNBA spread/total | Consistent full-game half-point contracts including overtime | Positive long-side spread descriptions currently conflict or remain unverified; integers unavailable |
| KBO/NPB moneyline | Win $1, loss $0, tie $0.50 | A tie is not an entry-price refund; void pricing unavailable |
| Soccer moneyline | Explicit home-win, away-win or draw Yes/No contract over 90 minutes plus stoppage | Target fixed in fixture before quotes; default home-win; completed result must explicitly show two periods |

The soccer adapter retains all three probabilities. No on a home-win contract
wins on either an away win or a draw; Yes on the draw contract wins only on a draw.
It does not optimize retrospectively among three different captured contracts.
One event/market/line is admitted per batch. Cross-batch repeated decisions must
not be pooled as independent outcomes.

The common policy uses a $5 budget per event-market, whole contracts bounded by
displayed depth, a verified 0.06 fee coefficient and the existing per-fill fee
rounding. It selects a positive expected return per dollar or abstains. The
five-minute limit applies to the request window and book transaction timestamp.
Canceled/postponed/void settlement is not assumed to return principal.

Policy v1 is retained for the first NRFI batch, including its inaccurate inherited
full-game/retrospective metadata. Its actual payout tables and timestamps were
correct. v2 corrected that metadata and added CFB/KBO/NPB support; v3 adds WNBA
and soccer. Old policy identities and original choices still reproduce exactly.

## New prospective results

| Cohort | Forecast outcomes | Price evidence | Simulated result |
|---|---|---|---|
| Clean NRFI, September 12 | 6/14 correct; vector Brier 0.500933; log loss 0.694080 | No timely bound entry quotes | Unavailable |
| Clean NRFI, September 13 | 7/15 correct; vector Brier 0.507164; log loss 0.700319 | 6 valid opportunities; 2 calls | **-$9.75** on $9.75 cost, including $0.31 fees; both calls lost |
| Soccer, September 13 | 1/1 correct; vector Brier 0.417143; log loss 0.749270 | No pregame price captured | Unavailable; second fixture had no forecast |
| CFB, September 17 | Three forecasts for one future game | Moneyline quote valid, one simulated call; spread/total books stale | Awaiting outcome; no P&L |

Across the two clean NRFI days, 13 of 29 forecasts were correct. Those 29 outcome
forecasts are distinct from the six priced opportunities and two calls. The
small cohort and losing trades provide no support for promotion. The CFB game
has not happened as of September 14; its $4.77 simulated cost is not settled P&L.

The nine unpriced NRFI cases on September 13 were six stale books, two rate-limited
requests and one conflicting event/market start. Kansas City–Boston's event
showed 19:05 UTC while its NRFI contract still showed 17:35 UTC. The original
batch is retained without price retries or silent repairs. Discovery is now
cached once per league per batch to reduce redundant requests.

WNBA's current public event list was empty on September 13. A named archived
August 30 event supplied actual contract terms for tests. Those tests use
synthetic open books and are not prospective WNBA economics. KBO/NPB and soccer
tests likewise distinguish real provider metadata from synthetic prices.

Earlier results are preserved: on the September 11 paired MLB cohort, the
candidate lost $13.925 and the incumbent lost $26.130 across 24 quote
opportunities. Both lost overall; the smaller candidate loss is not profitability.
See `NRFI_AND_SETTLED_REBUILD_2026-09-12.md` for the full cohort and limitations.

## Every current model

These are chronological historical test metrics, not prospective trading
performance or claims of incumbent superiority. Historical feature publication
availability remains unverified. MAE measures score margin or total error in
the sport's units; it is not spread/total betting accuracy. Soccer, KBO and NPB
accuracy uses three outcomes. Separate calibration and selection precede the
test partitions; previously examined tests are development evidence.

| Candidate market | Historical test games | Metric |
|---|---:|---:|
| WNBA moneyline | 275 | 63.64% accuracy |
| WNBA spread | 275 | 10.513 MAE |
| WNBA total | 275 | 16.778 MAE |
| MLB moneyline | 1,492 | 54.22% accuracy |
| MLB spread | 1,492 | 3.534 MAE |
| MLB total | 1,492 | 3.556 MAE |
| MLB NRFI | 1,374 | 51.67% accuracy |
| NBA moneyline | 587 | 68.48% accuracy |
| NFL moneyline | 165 | 66.06% accuracy |
| CFB moneyline | 878 | 69.70% accuracy |
| CFB spread | 878 | 13.532 MAE |
| CFB total | 878 | 12.833 MAE |
| Soccer moneyline | 1,483 | 50.78% accuracy |
| Tennis moneyline | 5,527 | 63.38% accuracy |
| CS2 moneyline | 6,688 | 64.14% accuracy |
| Dota 2 moneyline | 1,698 | 65.37% accuracy |
| LoL moneyline | 2,399 | 67.40% accuracy |
| Valorant moneyline | 2,293 | 61.67% accuracy |
| Rainbow Six moneyline | 495 | 68.28% accuracy |
| KBO moneyline | 1,665 | 52.91% accuracy |
| NPB moneyline | 809 | 53.28% accuracy |

Exact model IDs, artifact hashes, proper-score metrics and adapter mapping are
in `outputs/research/pricing_verification_20260914/model_status.json`.
No new fitting was done against the September 13 outcomes. Missing prospective
cohorts and unavailable ROI/CLV for other models remain null, not zero or green.

## Verification and reproducible commands

All 21 current artifacts pass fresh identity and probability-inference checks,
including integer and half-point distribution checks. Integer inference does
not authorize settlement without contract rules. Scoped type checking passes
for six modules; Ruff passed for all 91 changed/new Python files at the saved
snapshot. That full suite passed **2,964 tests with 3 skipped**; the affected
pricing suite passed 139 tests after the final NO_FORECAST exit-code fix.
Further local edits followed. See
[the evening consolidation record](CONTROL_PLANE_CONSOLIDATION_2026-09-14.md)
for the current-tree verification and unresolved control-plane gaps.
Exact logs, code/fixture snapshots and validation hashes are retained in
`outputs/research/pricing_verification_20260914/`.

Run from the repository root with `PYTHONPATH=src:.` and `.venv/bin/python`:

```sh
scripts/capture_research_prices.py --forecasts EXISTING_FORECAST_DIRECTORY --output NEW_PRICE_DIRECTORY
scripts/score_research_prices.py --prices EXISTING_PRICE_DIRECTORY --outcomes COMPLETED_RESULTS_JSONL --output NEW_SCORE_DIRECTORY
```

An existing output directory is never reused. Partial pricing returns exit code
2; completed score batches with integrity failures also return 2. Price capture
requires public network access. The source/model/quote archives and code snapshots
are retained under each output directory.

Key evidence:

- `clean_nrfi_forward_20260913_v2/` and `clean_nrfi_prices_20260913_v1/`.
- `clean_nrfi_outcomes_20260914_v2/` and `clean_nrfi_priced_outcomes_20260914_v1/`.
- `soccer_forward_20260913_v1/` and `soccer_outcomes_20260914_v1/`.
- `cfb_forward_20260913_v2/`, `cfb_prices_20260913_v2/` and `cfb_price_replay_20260914_v2/`.
- `espn_outcomes_20260914_for_20260913/` preserves raw and normalized results.

All paths above are under `outputs/research/`. Production champions, protected
model JSON artifacts and the canonical trading ledger were not changed by this
pricing work; no orders were submitted. The separate existing YAML and runtime
state changes remain outside this work.

The implementation supports research across every current model. Demonstrating
better accuracy and profitability remains unfinished empirical work: collect
distinct future events with valid prices, preserve every abstention/failure,
and evaluate the fixed policy after settlement. Do not promote on the present
results, manufacture missing prices, or treat additional retraining as proof.

---

## Part 10 — Control Plane Consolidation (source: CONTROL_PLANE_CONSOLIDATION_2026-09-14.md)

# Control-plane consolidation: verified findings and implementation queue

Local checkout: `8b713cd0944f94fe1dc1100e48b439c920122a88` (September 9),
plus the uncommitted rebuild and other local work. Audit snapshot:
`outputs/research/control_plane_verification_20260914/audit.json`, captured
September 14 at 21:42 EDT. This document does not claim to verify remote HEAD,
GitHub Actions, branch protection, or current account exposure.

## Findings that change the September 4 assessment

| Finding | Verified local state | Consequence |
|---|---|---|
| GitHub snapshot versus local work | Local HEAD is five commits beyond the supplied September 4 snapshot, with substantial uncommitted work | Use the exact code snapshot for validation claims |
| Five reported test failures | All five pass locally; the v4 test previously depended on ignored files | Local passing tests did not establish fresh-checkout reproducibility |
| Mutable order/state files in Git | Removed from tracking by `8ba3576` on September 8; local files remain | No new deletion/migration is needed to remove these files from Git |
| Auto-Buyer mode | The state file actually read by `auto_executor.py` is `data/auto_buyer_state.json`: enabled, **paper** | Enabled does not mean real-money execution |
| Global capital authority | `automated_orders: false` / `manual_orders_only: true` are loaded by the production registry but not consumed by Auto-Buyer | Still requires a common authorization boundary |
| Static allowlist drift | Saved whitelist includes unregistered `soccer-poisson-dc-v2` and `soccer-total-v1` | Whitelisting is not model qualification |
| Configured challengers | All 21 sport/market pointers lack registered challenger entries | Lifecycle integrity now explicitly fails; serving remains available |
| Implementation status | Registry reports 16 implemented and 5 planned lanes after removing unsupported status claims | Import success alone does not prove mechanics validation |
| Champion evidence | Config declares 13 historical-only, 5 predictively-qualified, 3 degraded champions | These are declarations, not a fresh independent qualification audit |
| MLB v10 | Operator serving promotion is explicitly `predictive_only`; Phase F YAML still declares confirmation/ACTIVE | Serving basis, scientific result, and capture freshness are separate facts |
| Phase F supervision | No v10 collector worker in canonical `WORKERS`; no corresponding worker in runtime run history | YAML ACTIVE is not verified worker health |
| Research reset | September 9 AGENTS records the operator's reset of old frozen research restrictions | Preserve old artifacts and results; do not reinstate obsolete authorization locks |
| All-model rebuild | 21 fitted research candidates with replay, inference and increasingly complete pricing support exist | Integrate their identities/evidence; do not restart model development from zero |

The latest successful runtime starts in the snapshot were daily 01:08 UTC,
manual-bet-sync 01:18 UTC on September 15; production 21:44 UTC and rebuild
22:36 UTC on September 14. These run records prove those worker executions,
not model qualification or Phase F capture. The read used the canonical
runtime `runs.db` in SQLite read-only mode.

## Implemented in this pass

### P0-01 — Make the v4 integrity test reproducible

Files: `tests/test_mlb_v9_v4.py`, `tests/fixtures/mlb_v9_v4/`.

Packaged byte-identical copies of the existing 285 KB historical parquet and
its manifest. The test reads its versioned fixture directory rather than
ignored `outputs/research/`, verifies SHA-256 and dimensions, and recomputes
condition number, VIF, and the audit result. The original research files were
preserved. The fixture is regression evidence, not PIT or promotion evidence.

### P0-02 — Stop unbound challenger qualification claims

Files: `src/model_prediction/model_lifecycle.py`, `production_registry.py`,
`qualification_registry.py`; `tests/test_qualification_integrity.py` and
`tests/test_model_lifecycle.py`.

- Lifecycle validation now reports missing, unavailable, wrong-model,
  wrong-market, or hashless challenger entries and missing freeze/protocol
  metadata. It does not mutate champion availability.
- Freeze timestamp and protocol hash are read from the hash-verified artifact.
  A timestamp must be timezone-aware and cannot be in the future.
- Removed the two name-based prospective exceptions and file-existence freeze
  inference. Importable code reports IMPLEMENTED, not MECHANICS_VALIDATED.
- Offline report acceptance requires the exact registered model/artifact,
  explicit non-synthetic origin and a valid integer count.
- Evidence loading requires an expected artifact hash and filters both market
  and exact hash. Rows cannot supply their own authoritative freeze timestamp.
- JSON/Markdown summaries expose qualification errors, identify the evidence
  column as **champion** evidence, and preserve the operator serving basis and
  its predictive-only scope.

The initial eight adversarial tests failed before the changes. Their failures
demonstrated the bugs independently of the current configuration. Additional
tests cover freeze metadata, report identity, cross-market evidence and
operator-promotion reporting. The existing serving test now explicitly expects
the current lifecycle failure, then separately checks every serving champion.
It does not turn the unresolved registration state into a passing gate.

Rollback consists of reverting these source/test changes; no champion pointer,
old model artifact, runtime setting, or order was changed by this pass.

## Remaining queue, in commit-sized order

### P0-03 — Register actual research candidates and reconcile pointers

Files: `config/production.yaml`, `src/model_prediction/production_registry.py`,
`research_generation.py`, `research_forward.py`, `qualification_registry.py`;
new immutable binding manifests and `tests/test_qualification_integrity.py`.

Use the existing September 12 generation manifest to bind each new model ID,
sport/market, artifact, inference implementation, source hashes, training data,
calibration, freeze timestamp and evaluation protocol. Preserve the legacy
research-target names as historical queue entries. Do not assign their names
to different fitted models. Support the generation artifact format explicitly
rather than rewriting artifact bytes to imitate production JSON.

Acceptance: all 21 new candidates resolve and reproduce archived forecasts;
tampering any required dependency refuses the candidate; champion routing is
unchanged. Every active challenger pointer either resolves or is explicitly
planned. No synthetic or retrospective row becomes prospective through the
registration step. A model card links actual results and missing evidence.

### P0-04 — Enforce one capital authorization boundary

Files: `src/model_prediction/portfolio/auto_executor.py`,
`data_sources/polymarket_execute.py`, `execution_ticket.py`,
`production_registry.py`, `dashboard_server.py`, `config/production.yaml`;
`tests/test_auto_buyer_mode_boundary.py`, `tests/test_execution_gate.py`,
`tests/test_auto_polymarket_buyer.py`, and a dedicated authorization test module.

Make the production execution policy the global permission boundary; the
runtime Auto-Buyer switch requests operation within that permission. Enforce
it at the final order-submission boundary, including fallback/top-up paths.
Require exact registered artifact identity, explicit sport/market approval,
eligible evidence and existing context/quote/risk checks. Retain allowlists
only as additional restrictions. Define any operator exception as an audited,
expiring, artifact-specific authorization with an explicit reason; never infer
one from an enabled switch. Keep serving, predictive qualification, economic
qualification and execution eligibility separate.

Acceptance: a static whitelist, direct buyer construction, dashboard one-off
override, fallback or top-up cannot bypass the global denial. Paper mode stays
paper. Missing/invalid policy and unbound/degraded/historical-only/synthetic
evidence refuse submission. Revoking permission between quote and submit stops
the order. Existing position reconciliation remains available independently of
permission to add exposure. Tests use fake executors and never submit orders.

### P0-05 — Supervise prospective capture and derive freshness

Files: `src/model_prediction/run_supervisor.py`,
`scripts/mlb_v10_prospective_shadow.py`,
`scripts/run_daily.sh`, `scripts/run_production.sh`, `ops/launchd/`,
`dashboard/production.py`, `config/research/phase_f_state.yaml`;
`tests/test_run_supervisor.py` and dedicated capture-health tests.

Register capture and integrity audit as explicit workers. Reuse leases,
run IDs and failure recording. Connect scheduling to the actual T-minus window;
do not assume a three-hour trigger will capture a 30-minute horizon. Derive
capture ACTIVE/STALE/FAILED/NO_DATA from eligible opportunities, append-only
prediction times and successful audit runs. Preserve Phase F's historical
protocol results and report the operator research reset separately.

Acceptance: one worker owns a capture window, duplicate wakes are idempotent,
post-start predictions are rejected, missed eligible windows are visible,
empty off-season slates differ from failed capture, and a failing audit cannot
leave health ACTIVE. Ordinary service restart must not rewrite frozen rows.

### P0-06 — Tie release claims to the exact tested revision

Files: `.github/workflows/ci.yml`, `scripts/generate_rebuild_verification.py`,
deployment/promotion entry points, `src/model_prediction/model_promotion.py`.

Require a passing test receipt for the exact deployed source/artifact revision.
Retain failing checks as failures. Configure required GitHub checks when
publishing this work; local test output alone is not a remote CI receipt.

Acceptance: stale receipts, dirty-source mismatches and red checks block
promotion/release. A clean checkout includes all required test fixtures and
runs without ignored local research products. This gate concerns software
verification; it does not replace the separate evidence gate.

### P1-01 — Consolidate context, paired replay and evidence counting

Files: `model_lifecycle.py`, `research_pairing.py`, `research_forward.py`,
`research_incumbent_evaluation.py`, `research_scoring.py`,
`scripts/evaluate_ready_challengers.py`, `scripts/evaluate_captured_pairs.py`,
`scripts/evaluate_mlb_residual_tournament.py` and canonical runtime storage.

Reuse the implemented replay adapters and immutable source archives. Bind
exact event, participants, market, period, line, decision timestamp/horizon,
quotes, features and both artifacts. Count unique eligible events/dates;
duplicate horizons are not independent games. Validate conflicting outcomes
and score the challenger's selected side, never copy an incumbent win/loss
label blindly. Replace the legacy horizon-file/event-ID-only join and all
pseudo-incumbent comparisons. Route each public qualification metric through
this engine.

Acceptance: exact incumbent replay, same-context pairing, tamper refusal,
duplicate/conflict exclusions, chronological origin verification, and null
economics for missing executable quotes. Reports include market/champion/
candidate proper scores, calibration, coverage and date-clustered uncertainty;
CLV and cost-adjusted results have explicitly separate cohorts.

### P1-02 — Measure served-value distributions

Files: `production_canary.py`, `cli_production.py`, `ledger.py`,
`dashboard/production.py`, canonical runtime queries and new health tests.

Compute per-model/market counts, exact-0.5 fraction, unique values, probability
spread, quote age, missing/fallback features, feature variance and
model–market disagreement. Preserve NO_DATA/INSUFFICIENT_SAMPLE rather than
inventing green health. Compare observed distributions with declared training
or approved benchmark ranges, with explicit sample-size rules.

Acceptance: a finite normalized but constant-0.5 market feed is detected;
one sparse event is not treated as a robust distribution; benchmark-serving
forecasts remain visible while actionable eligibility reflects the anomaly.

### P1-03 — Generate status from the canonical registry and queue

Files: `qualification_registry.py`, `scripts/generate_multisport_status.py`,
`dashboard/rebuild_status.py`, `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`.

Generate current identities, counts, errors and next actions, link immutable
reports, and retain dated history separately. Publish implemented versus
validated versus qualified as distinct states. The rebuild is an experiment
and component incubator, not another production authority.

Acceptance: missing reports, unknown artifact IDs, absent workers and stale
evidence are visible. No static table overrides the canonical state.

### P2 — Sport-by-sport evidence and model iteration

1. **NCAAF:** compare the actual current incumbent and rebuilt joint-score
   candidates using real decision-time ML/spread/total contracts. Historical
   comparisons against a prior rebuild generation are a different baseline.
2. **MLB:** retain v10's exact existing artifact for prospective comparisons;
   collect genuine T-minus lineup/starter/bullpen/weather/market inputs for
   moneyline residual work. Retain clean NRFI's negative results.
3. **WNBA:** use the new candidate/replay/pricing support with chronological
   availability and possession features; report executable-price coverage.
4. **NFL/NBA:** capture availability and market histories before adding model
   complexity. Football's current season increases the cost of missed capture.
5. **Tennis/Soccer:** regenerate orientation-sensitive evidence and make
   provider identity, competition coverage and settlement rules explicit.
6. **Esports:** fit PIT contextual effects and test inactivity/roster shifts;
   independently validate event identity and result timing in settlements.
7. **KBO/NPB:** prioritize reliable pitcher/lineup sources and tie rules;
   source breadth comes before additional model families.

For every lane: use chronological inner-fold selection, OOF-only ensembles,
market and exact-incumbent baselines, feature-family ablations, and an untouched
prospective cohort. Repeated use of a historical holdout is development, not
independent confirmation. Predictive improvement and executable profitability
remain distinct requirements. The existing 21-model generation has not yet
established both.

## Verification

The full suite passed **2,990 tests, 3 skipped**, in 354.22 seconds. A final
synthetic-origin safeguard and regression assertion were added during that
run; the affected 65-test suite passed against the final code. Scoped mypy
passed for three registry/lifecycle modules; Ruff passed across all 102
changed/new Python files. The v4 integrity test also passed in an isolated
temporary directory containing only its test and packaged fixtures.

Both source snapshots and exact logs are recorded under
`outputs/research/control_plane_verification_20260914/validation.json`.
Its status is deliberately `SOFTWARE_CHECKS_PASS_LIFECYCLE_INTEGRITY_FAIL`:
the software correctly reports 21 unregistered challenger lanes. Remote CI
was not verified, and the changes have not been committed or pushed.

The earlier 2,964-pass run belongs to the saved snapshot under
`outputs/research/pricing_verification_20260914/`. Additional local changes to
CFB, esports and ledger code arrived after that run; its result must not be
presented as validation of those later changes. This pass preserved those
edits and did not change live settings or existing model artifact bytes.
