# Esports overhaul proposal — 2026-10-22

Status: draft for discussion. No implementation, retraining, activation, or ledger mutation performed.

## Recommendation and alternatives

Recommended: evidence and serving repair, then CS2 moneyline as the first modeling project, then title-specific extensions and joint series pricing. Shared infrastructure covers all five titles; model complexity follows proven data coverage.

Alternative A: recalibrate v6 only. Fast and useful as a control, but cannot represent roster turnover, map-specific strength, or reliable derivative distributions.
Alternative B: replace all titles immediately with contextual v7 or the specialist modules. Rejected: contextual v7 contains heuristic coefficients, and module availability does not establish point-in-time inputs, training, or superiority.
Alternative C (recommended): repair measurement, compare simple learned candidates, acquire contextual data only where it can resolve a measured weakness.

Working objective pending operator preference: executable paper profitability, with probability-quality safeguards. Treat model quality and trading policy as separate evaluations.

## Current evidence

Read-only runtime SQLite snapshot, Flat tier, win/loss results, latest created row per (event_id, market_type, selection, line, model_id). Counts below are pick keys, not necessarily independent matches. Recorded market probability is a diagnostic benchmark; quote semantics and timing still require verification.

| Moneyline title | Pick keys | Model Brier | Stored market Brier |
|---|---:|---:|---:|
| CS2 | 338 | .2452 | .2384 |
| Dota2 | 60 | .2324 | .2031 |
| LoL | 15 | .2595 | .2219 |
| Valorant | 18 | .2498 | .2247 |
| Rainbow Six | 10 | .2950 | .3119 |

CS2 has 337 distinct events. Positive recorded CS2 moneyline P&L (+19.478U) is not proof of a durable advantage; model Brier is worse than the stored market benchmark. Small title samples cannot support confident rankings. Raw history files contain CS2 42,116, Dota2 11,514, LoL 13,216, Valorant 15,010, R6 3,324 records; these are not audited eligible sample counts.

Sources inspected:
- `src/model_prediction/esports.py`: ingestion, NeutralElo, chronological validation and serving.
- `src/model_prediction/esports_replay.py`: captured state and exact-probability checks.
- `src/model_prediction/models/esports_contextual_v7.py`: fixed contextual coefficients and assumed defaults.
- `src/model_prediction/models/esports_cs2.py`: deterministic veto and default Mirage for Bo1.
- `src/model_prediction/models/esports_derivatives.py`: identical independent map assumptions and inverse series probability.
- `config/production.yaml`: v6 champions, contextual v7 challenger declarations.
- `src/model_prediction/calibration_pipeline.py`: extraction across ledger tiers; overlap and artifact selection require investigation.
- `docs/ARCHITECTURE.md`, root/docs AGENTS.md, current handoff: research and operational constraints.

## Phase 1 — Establish the actual system and repair evidence

Theory: some apparent model weakness or strength is caused by inconsistent serving, replay, contract interpretation, or duplicated evaluation records.

1. Trace one real stored decision per available title/market through artifact selection, raw probability, internal Platt/temperature, downstream calibration, format adjustment, sizing, and settlement. Record exact hashes and transformation order.
2. Audit Flat/Main/legacy research overlap. Maintain execution records, but build a unique evaluation cohort; keep multiple decisions from one event in the same split and uncertainty cluster. Audit calibrator training overlap and selected artifact precedence rather than assuming duplication is present.
3. Unify prediction transformations across serving and replay. Current serving captures inputs before Bo1/Bo5 logit scaling; replay reconstructs the underlying book probability. Historical validation also uses the book directly. Verify these discrepancies with focused fixtures and a real archived decision before changing behavior.
4. Audit series format, map-versus-round contract semantics, line sign, selection anchor, postponed/forfeited matches, unknown-team diagnostics, and push/void settlement. Unknown format or ambiguous identities must produce explicit ineligibility for actionable prices.
5. Publish coverage: source identity, result availability, observed timestamps, archived quotes, format and map scores. Match start alone does not prove when a result became available; overlapping matches must not update each other prematurely.

Deliverables: read-only cohort report, per-row parity report, verified defect list with reproduction cases. Extend existing replay and validation tools; do not introduce a second ledger.

Acceptance: every evaluated decision has a unique key, model identity, decision time, and probability lineage; non-replayable rows are explicitly excluded with counts and reasons. Fixes have direct tests and a measured before/after difference. No claim that an unfetched historical observation existed before collection.

## Phase 2 — Build an honest comparison

1. Freeze data/code/artifact manifests. Reproduce the exact v6 configuration as a named control against its recorded results; report per-row differences. Stop candidate ranking if reproduction fails. Separately label a corrected v6 comparator so a serving repair is not misrepresented as new model lift.
2. Use chronological development folds with result-availability checks and a final untouched time window. Previously inspected holdouts are development evidence; collect fresh prospective evidence where necessary. Fit model parameters on training data, calibrate using chronological out-of-fold training predictions, select candidates/policies on validation, evaluate the final holdout once.
3. Baselines: v6 exact, corrected v6, recalibration-only v6, simple learned rating model, and recorded market probabilities where comparable quotes exist. Evaluate a market residual policy separately from the independent outcome model.
4. Report paired Brier/log-loss deltas, calibration, coverage, and event/date-clustered uncertainty. Evaluate executable paper returns only on actual archived contracts and prices, including applicable fees, depth/fill assumptions and latency. Do not convert result-only history into invented -110 returns.
5. Choose minimum useful improvement, uncertainty rule, risk budget, and window definitions before fitting. A comparative verdict must consume its uncertainty interval. Retain existing project release rules unless explicitly revised; explain the weakness of universal accuracy targets under variable odds.

Acceptance: reproducible control, no leakage, explicit unavailable metrics, separate accuracy and profitability verdicts. Insufficient evidence is a valid outcome; do not force a winner for every title.

## Phase 3 — CS2 moneyline model research

Start with the available match history: learned rating dynamics, recency, opponent strength, tournament/competition tier and series format. Audit each field before use; tune parameters on training folds instead of adding arbitrary constants. Compare a regularized residual model against a simple dynamic rating challenger; prefer the simpler model if their useful gains are indistinguishable.

Context acquisition order is conditional on coverage and incremental validation:
1. Timestamped roster continuity and stand-ins; stable team identity must not imply unchanged players.
2. Map results, versioned active map pools and opponent-adjusted map strength.
3. LAN/online and event context.
4. Pre-veto distributions, followed by actual veto information only after its recorded observation time.

Do not treat one deterministic veto as certainty or a default map as observed fact. Missing contextual data returns the validated baseline with a quality flag only where that fallback has itself been evaluated; otherwise decline the contextual prediction.

Acceptance: one new version beside v6, learned hash-verified configuration, calibrated out-of-time predictions, ablations and error slices by format, tier, inactivity and unseen teams. No new feature enters the model without demonstrated incremental value and point-in-time availability.

## Phase 4 — Joint series distribution and derivatives

Build a reusable series engine consuming per-map probabilities and known format, producing terminal score probabilities. Moneyline, map handicaps and map totals must derive from that same normalized distribution. Account for map-specific differences; measure the independent-map simplification against a richer dependence model rather than assuming complexity helps.

Pre-veto uncertainty is integrated over eligible map/veto scenarios. Post-veto forecasts are a separate decision-time product. Explicitly support only audited formats and contract types, including any title-specific draw or map-advantage rules.

Acceptance: probabilities sum to one, complement/swap invariants hold, identical-map special cases match closed forms, and exact archived derivative contracts are evaluated independently. Strong moneyline performance alone cannot certify spread or total performance.

## Phase 5 — Extend by title and evidence

- Valorant: transfer map/series infrastructure, then test roster, map and agent-meta features where observed inputs exist.
- LoL: test patch/region strength and roster continuity. Side and draft information must be unknown before assignment/draft and modeled accordingly.
- Dota2: test roster and tournament strength, patch context, then draft/side information only at eligible decision times.
- R6: prioritize identity and roster coverage; use stronger shrinkage or partial pooling if justified by training data. Its small settled cohort cannot validate a complex standalone upgrade.

Order after CS2 depends on audited data coverage and market opportunity, not enthusiasm for a game. Each title and market earns its own comparative verdict.

## Phase 6 — Integrate and observe

Trace the actual daily forecast route, rolling runtime artifacts, frozen rollback artifacts, production registry, calibrator lookup and Flat/Main logging. Model ID, artifact hash, replay schema and served math must agree. Keep prospective evidence separate from reused history and monitor coverage, abstentions, calibration, log loss, executable edge, fills, CLV where defined and drawdown.

Activation is a separate operator decision; this brainstorming request does not authorize champion changes. Rollback must restore the compatible model, transformation/calibration artifacts and serving configuration together. Real-money execution remains disabled.

## First implementation package after design agreement

Scope: Phase 1 plus the reproducible comparison foundation for CS2. Likely files: `esports.py`, `esports_replay.py`, focused esports tests, and an isolated research report script. Include `calibration_pipeline.py` only if overlap or selection defects are confirmed. Precise interfaces and implementation steps follow the verified defect list.

Verification follows repository rules: batch changes, run Ruff, then only directly affected mathematical/replay test files with `-o addopts='' -q`; no monolithic test run. Existing uncommitted work and protected model artifacts remain preserved.

## Decisions to settle

1. Primary objective: executable profitability with probability-quality safeguards, probability accuracy first, or equal priority.
2. Approve the staged evidence-first approach and CS2 as the first modeling project.
3. Set candidate-selection effect thresholds and paper risk budget from the audited cohort before research fitting; arbitrary values would create false precision now.
