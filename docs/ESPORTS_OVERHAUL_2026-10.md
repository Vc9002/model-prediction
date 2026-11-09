# Esports Overhaul (2026-10-22 to 2026-10-23)

> Consolidated from: esports-overhaul-proposal-2026-10-22.md, ESPORTS_OVERHAUL_2026-10-23.md, esports-series-contract.md. Archival merge — all content preserved, chronological order.

---

## Part 1 — Esports Overhaul Proposal (source: esports-overhaul-proposal-2026-10-22.md)

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

---

## Part 2 — Esports Overhaul (source: ESPORTS_OVERHAUL_2026-10-23.md)

# Esports overhaul execution — 2026-10-23

Executed the approved staged proposal. Serving/data-quality repairs are applied to E:/MP. New learned challengers and the joint series engine remain research components; no champion/config artifact was replaced, no ledger was mutated, and no orders were placed.

## Delivered changes

- Removed unfitted Bo1/Bo5 logit scaling from v6 serving; captured state, serving and replay now use the same book probability. Explicit serving-math metadata and context provenance are retained.
- Added result-end-aware chronological replay for corrected research. The legacy path remains available for exact historical control. End times are lower bounds, not proof of provider publication/collection times.
- Added prospective BO3 schedule capture, completed-fetch observation timestamps, immutable raw request/response archives and exact source-team-ID plus UTC-start binding. Incomplete scans, conflicts, stale/late observations, ambiguous matches and unsupported formats remain unresolved. Fuzzy team names are diagnostic-only and cannot borrow source context. Explicit audited aliases remain supported.
- Removed missing-line defaults and spread-anchor guessing. Logger now persists exact totals 3.5/4.5 and signed map handicaps +/-2.5 instead of reducing them to 2.5/1.5. Ambiguous contracts fail before logging.
- Preserved the operator's current Flat/Main paper CALL routing. Legacy parameter names do not restore retired research gates. Persisted risks distinguish invalid actionable inputs, context source and identity-resolution methods.
- Deduplicated esports calibration cohorts within each tier and ordered decisions chronologically. Flat authority wins over larger retired cohorts; insufficient or zero-eligible Flat data explicitly revokes stale fitted artifacts on the next artifact-writing calibration run. Current calibrators were not regenerated by this research task. Zero probabilities are preserved.
- Added an exact research terminal-score distribution for explicit Bo1/3/5 with heterogeneous ordered map probabilities and explicit weighted scenarios. Moneylines, map handicaps and totals share that distribution and preserve push mass. It is not registered as a live champion.
- Added a read-only research CLI, source-verified historical control replay, chronological learned rating and regularized fixed-Elo-offset residual candidates for all five titles, new versioned/hash-verified research artifacts and immutable data snapshots.

## Frozen evidence and verdict

Authoritative run: [research report](../outputs/esports_overhaul/research-20261023-final/report.md), [machine-readable report](../outputs/esports_overhaul/research-20261023-final/report.json), [manifest](../outputs/esports_overhaul/research-20261023-final/manifest.json), [per-row parity](../outputs/esports_overhaul/research-20261023-final/incumbent_per_row_parity.jsonl).

- Frozen queried SQLite esports rows: 3,586.
- Active settled moneyline records before tier/forecast deduplication: 1,826.
- Canonical pick keys: 451 (447 Flat, 4 Main); 1,375 duplicates removed, zero settlement conflicts.
- Exact source-verified control replays: **450 / 451** after follow-up recovery.
- One archived-source mismatch remains: the Oct 11 esports module hash `a3c853f4…15c8bce8a` has no exact source copy in the current checkout, reachable or unreachable Git blobs, session/editor history, or newline-normalized candidates. Its one row remains unresolved; no substitute source was accepted.
- Control verdict remains **BLOCKED_REPRODUCTION**. Production rankings, new-model activation and profitability conclusions remain blocked.

Follow-up source-recovery evidence is stored in [the verified replay and recovered source archive](../outputs/esports_overhaul/source-recovery-20261023-verified/). The resolver now scans unreachable Git blobs by exact SHA-256 and records each Git blob ID separately from commit provenance. It recovered source hashes `8d25951d…30e7b330` (69 rows) and `c1c2408e…03b02c01` (1 row), accounting for 70 of the previous 71 failures. This archive is separate from the frozen initial report and does not rewrite its manifest.

Stored market probabilities are diagnostic benchmarks; executable quote semantics, fees, fills and latency were not certified. Lower Brier is better.

| Title | Canonical pick keys | Stored model Brier | Stored market Brier | Eligible historical events |
|---|---:|---:|---:|---:|
| cs2 | 339 | 0.2458 | 0.2387 | 42061 |
| dota2 | 65 | 0.2317 | 0.2079 | 11511 |
| lol | 15 | 0.2595 | 0.2219 | 13196 |
| rainbow_six | 11 | 0.2848 | 0.2941 | 3317 |
| valorant | 21 | 0.2481 | 0.2192 | 14826 |

The historical candidates use strictly prior result-end availability and nested chronological development folds. Contextual tier/format features are excluded because their historical observation timestamps are unverified. The final 20% of dates remains unscored and is labeled reused history, not fresh prospective confirmation. No diagnostic candidate is presented as a validated v6 replacement.

The final run freezes the exact five history byte streams as deterministic gzip, the queried SQLite records, recovered historical source files and research source. All 41 manifest entries were independently verified. Earlier numbered worktree runs are superseded and were not applied to E:/MP.

## Verification and review

Ruff passed on all changed code and focused tests. **178 targeted tests passed** across the directly affected modules, each file run separately with `-o addopts='' -q`; the CLI check selected only esports tests. Source import location was verified to avoid testing the original checkout from a shared editable venv. No full suite was run, following AGENTS.md.

The source-recovery follow-up added a regression test for unreachable Git blobs. Its focused suite passed **10 tests**, Ruff passed, and the verified 451-row replay produced 450 exact matches with one source-unavailable row. Both the initial 41-file frozen research manifest and the separate 37-file recovery manifest independently re-verified.

Independent review found and corrected UTC train/validation overlap, canonical contract normalization, insufficient/empty Flat authority, and fuzzy participant identity borrowing. Series math was checked against closed forms and independent exhaustive paths.

Live read-only prospective proof: Rainbow Six source match 132559, IDs 19230/19728, start 2026-10-24T19:00:00Z, explicit Bo3, fetched 2026-10-23T16:33:42.496213Z; capture hash verified. Source evidence is retained under `outputs/esports_overhaul/prospective-context-proof/`. This proves acquisition/binding, not model predictive lift or real-market coverage.

Live `esports-forecast --all` also completed with exit code 0 and no `--log` flag: 10 CS2, 1 Dota 2, 3 LoL, 0 Rainbow Six and 4 Valorant contracts were priced. It produced no ledger writes. The provider returned unsupported `SPORTS_MARKET_TYPE_PROP` market types on four titles; those were dropped with warnings.

## Remaining evidence requirements

1. Recover the final missing exact archived code/state lineage or establish a separately preregistered fresh prospective comparison; do not silently discard the failed control to obtain a passing verdict.
2. Accumulate timestamped source/context and exact contract/quote observations. Roster, patch, map/veto and draft inputs cannot be fabricated from today's information. The new collector solves prospective series format only.
3. Validate map-specific predictions and derivative contracts separately before connecting the new joint distribution to serving. Existing v6 derivative diagnostics retain their independent-map limitations.
4. Existing generic calibration tournaments still lack event-group/result-availability embargo metadata. Deduplication and sorting do not certify those folds as point-in-time valid; no new calibration lift is claimed here.
5. Select a champion only after the valid control, fresh comparison, uncertainty and exact-market evidence requirements are met. This execution did not authorize changing champions.

## Operational integration and rollback

Exact reviewed source/test files were copied from isolated branch `codex/esports-overhaul-20261023` to E:/MP after concurrent-edit hash checks. Unrelated edits were preserved. All 135 protected model JSON files matched the pre-publication hashes. Changes are left uncommitted for review; nothing was pushed.

Original versions of the five modified existing files are at `scratch/esports-overhaul-20261023/rollback/`; published file hashes and verification evidence are alongside them. To roll back, first compare those hashes against current files so later edits are not overwritten, then restore only the affected originals and remove only this task's new files. Retain research/prospective evidence. The isolated checkout remains available for inspection.

---

## Part 3 — Esports Series Contract (source: esports-series-contract.md)

# Research series distribution contract

`model_prediction.models.esports_series` is an isolated mathematical API, with
no serving registration, trained artifact, champion activation or market lookup.

```python
from model_prediction.models.esports_series import (
    MapScenario, mixture_series_distribution, series_distribution,
)

post_veto = series_distribution(3, [0.8, 0.3, 0.6])
pre_veto = mixture_series_distribution(3, [
    MapScenario(weight=0.25, map_win_probabilities=[0.8, 0.3, 0.6]),
    MapScenario(weight=0.75, map_win_probabilities=[0.2, 0.7, 0.4]),
])
assert abs(post_veto.moneyline_a - 0.612) < 1e-12
handicap = post_veto.map_handicap(-1, team="a")
total = post_veto.total_maps(2, side="over")
```

The example's probabilities and weights are illustrative inputs, not learned
estimates or evidence of observed vetoes. Callers establish decision-time
availability separately. `series_distribution` requires explicit integer
`best_of` equal to 1, 3 or 5 and exactly that many ordered probabilities of an A
map win, each finite and in [0, 1]. Both boundary probabilities are valid. Unknown
formats reject. Every potentially played map needs an input; play stops at the
first team to reach `best_of // 2 + 1` wins. No default map, veto, format, or
inverse-series estimate is supplied.

Conditional on a scenario, map results are independent, but probabilities may
differ by map position. Mixtures integrate shared scenario uncertainty rather
than first averaging map probabilities. Each scenario needs an explicit finite,
nonnegative weight; weights must sum to one within absolute tolerance 1e-12.
Empty mixtures and unnormalized weights reject. Zero-weight scenarios still
require valid inputs. Normalization only removes floating-point summation error.

`terminal_scores` is an immutable mapping from `(A maps won, B maps won)` to
unconditional probability, including zero-probability legal terminal scores.
`moneyline_a` and `moneyline_b` sum their respective winning terminal scores.
All derivative outputs are `OutcomeProbabilities(win, loss, push)`, each
unconditional; push mass is never redistributed into win/loss probabilities.

- `map_handicap(line, team="a" | "b")`: add the signed line to the selected
  team's map margin. Positive is a win, zero a push, negative a loss. A -1.5
  requires an A victory by at least two maps. B +1.5 is its complementary side.
- `total_maps(line, side="over" | "under")`: compare total maps played to the
  finite threshold. Equality is a push for either side. Over/under swap win and
  loss mass and retain identical push mass.

Finite numerical thresholds are analytical queries, not claims that such
contracts exist. These are map counts, never round counts. A real market still
needs verified participant identity, selection anchor, signed line, archived
quote, and provider settlement rules. The API assumes ordinary completed series;
draws, map advantages, forfeits, cancellations, voids, and title-specific variants
are unsupported. Mathematical push equality does not establish a provider's
refund policy. Quarter-line split stakes are not implemented.

The DP is exact for its stated probability model up to floating-point arithmetic.
It does not establish calibration, historical derivative coverage, predictive
lift, profitability, or superiority of independent maps over richer dependence
models. Those require separately audited data and chronological evaluation.

Verification: `tests/test_esports_series.py` checks Bo1/3/5 closed forms,
independent exhaustive heterogeneous-map paths, normalization, team swaps,
selection complements, integer pushes, scenario integration, immutability, and
invalid-input rejection.
